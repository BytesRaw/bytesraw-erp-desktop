r"""Finding, fetching and running a new build of the app.

The shape of this module is set by four decisions, each of which is load-bearing.

**The manifest, not the GitHub API.** The URL a client checks is compiled into
every shipped binary and can never be changed for a till already in the field,
so it points at :data:`~bytesraw_erp.constants.UPDATE_BASE_URL` - a domain we
own - rather than at ``api.github.com``. Artifacts still live on GitHub
releases; the indirection is what lets the hosting move later without stranding
the installed base, and ``next_manifest_url`` is how a client that only knows
the old URL is *told* where to look. Following that chain is therefore not a
nicety: it is the only migration path those installations have.

**Verify before running, and say which verification.** The SHA-256 in the
manifest is checked on every download, always: the manifest arrives over TLS
from a host we control, so it is the anchor for the bytes that came from
elsewhere. Authenticode is checked only when the manifest's ``signed`` field
says there is a signature to check. That field is the switch the whole signing
plan turns on - the day a certificate exists it flips to ``true`` on the update
host and enforcement starts fleet-wide, with no new client code shipped. Which
is exactly why the verification below is written now, while ``signed`` is still
false: the flip has to be a manifest edit, not a release.

**Staged rollout is decided on the client.** A manifest saying ``rollout: 25``
is honoured by each installation working out its own bucket from a random id
minted on first use, so the update host needs no telemetry, no per-machine
state and no idea who is asking. Two consequences worth knowing: an update the
rollout holds back is silently *not offered*, and a manual "check now" ignores
the gate, because a user who asks the question deserves the true answer.

**The installer closes the app, not the other way round.** ``setup.exe`` is run
with ``/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`` and then simply left to
it: Inno's Restart Manager asks the running app to close, replaces the files and
starts it again. Quitting first looks tidier and is wrong - with nothing running
there is nothing for ``/RESTARTAPPLICATIONS`` to restart, and the ``[Run]``
entry that would otherwise relaunch it carries ``skipifsilent``, so the till
would be left sitting on the desktop with no app at all.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QObject, QTimer, Signal

from bytesraw_erp.constants import (
    APP_NAME,
    APP_VERSION,
    UPDATE_BASE_URL,
    UPDATE_CHECK_SECONDS,
    UPDATE_DOWNLOAD_TIMEOUT,
    UPDATE_KEEP_INSTALLERS,
    UPDATE_LAUNCH_DELAY_SECONDS,
    UPDATE_MANIFEST_MAX_BYTES,
    UPDATE_MAX_HOPS,
    UPDATE_PRODUCT,
    UPDATE_SCHEMA,
    UPDATE_TIMEOUT,
)
from bytesraw_erp.core.errors import UpdateError
from bytesraw_erp.core.paths import updates_dir
from bytesraw_erp.data.models import UpdateChannel, is_newer_version, parse_version
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services.tasks import run_async

_log = logging.getLogger(__name__)

#: Read size for the installer download. Big enough that the progress signal is
#: not the expensive part of a 140 MB transfer.
_CHUNK_BYTES = 256 * 1024

#: Don't report progress more often than this. A toast and a label cannot show
#: 140 MB worth of updates, and the event loop should not be asked to deliver
#: them while the same machine is running Chromium.
_PROGRESS_STEP_BYTES = 2 * 1024 * 1024

#: Sanity bound on the declared artifact size. The installer is ~140 MB; a
#: manifest claiming gigabytes is wrong, and believing it means filling the
#: user's disk on the strength of a typo.
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024

_USER_AGENT = f"BytesrawERP/{APP_VERSION} (+https://bytesraw.com)"

__all__ = [
    "Update",
    "UpdateArtifact",
    "UpdateChannelEmpty",
    "UpdateError",
    "UpdateService",
    "download_update",
    "fetch_update",
    "install_update",
    "is_installed_build",
    "manifest_url",
    "rollout_bucket",
    "verify_authenticode",
]


class _Cancelled(Exception):
    """Internal: the caller asked for the download to stop. Never shown."""


class UpdateChannelEmpty(UpdateError):
    """The channel has no manifest, because nothing has been published to it.

    One file per channel is what makes a beta impossible to publish into the
    path stable clients read - and the price of that is that a channel nobody
    has released to yet simply has no file, so the host answers 404. That is
    the *normal* state of a new channel, not a failure: nothing is wrong, there
    is just nothing on offer.

    It is a subclass so that a caller which does not care still gets a sentence
    an end user can read, and so that it can never be mistaken for a manifest
    that was fetched successfully. Only the channel's own URL may raise it - a
    404 on a ``next_manifest_url`` is a genuinely broken chain, and the
    distinction is in :func:`_resolve_manifest`.
    """


# --- the manifest ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UpdateArtifact:
    """The installer a manifest points at."""

    url: str
    filename: str
    sha256: str
    size: int
    silent_args: tuple[str, ...] = ()

    @property
    def size_mb(self) -> str:
        return f"{self.size / (1024 * 1024):.0f} MB"


@dataclass(frozen=True, slots=True)
class Update:
    """One release, as described by a channel manifest."""

    version: str
    channel: UpdateChannel
    artifact: UpdateArtifact
    notes_url: str = ""
    released: str = ""
    minimum_supported: str = ""
    mandatory: bool = False
    #: Whether the artifact is Authenticode-signed, and therefore whether the
    #: signature is enforced before it is run. See the module docstring.
    signed: bool = False
    rollout: int = 100

    def is_required_for(self, current_version: str) -> bool:
        """Whether this release is not optional for a build on ``current_version``.

        Either the release says so, or the running build is below the manifest's
        ``minimum_supported`` floor - which is that field's whole purpose, and
        the case a staged rollout must not hold back.
        """
        if self.mandatory:
            return True
        return bool(self.minimum_supported) and is_newer_version(
            self.minimum_supported, current_version
        )


def manifest_url(channel: UpdateChannel) -> str:
    """The manifest for ``channel``. One file per channel, never one with a flag."""
    return f"{UPDATE_BASE_URL}/{channel.value}.json"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise UpdateError(message)


def _parse_artifact(raw: object) -> UpdateArtifact:
    """Read and *validate* the artifact block.

    Everything here arrives from the network, so nothing is taken on trust. The
    filename in particular is reduced to a bare name: it becomes a path under
    the updates cache, and a manifest is exactly the wrong thing to let choose
    where a 140 MB executable lands.
    """
    _require(isinstance(raw, dict), "The update information names nothing to download.")
    assert isinstance(raw, dict)  # narrowed above; for the reader and the checker

    url = str(raw.get("url") or "")
    _require(
        urlparse(url).scheme == "https",
        "The update information points at a download that is not over HTTPS.",
    )

    sha256 = str(raw.get("sha256") or "").strip().lower()
    _require(
        len(sha256) == 64 and all(char in "0123456789abcdef" for char in sha256),
        "The update information carries no usable checksum for its download.",
    )

    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    _require(
        0 < size <= _MAX_ARTIFACT_BYTES,
        "The update information declares an implausible download size.",
    )

    filename = Path(str(raw.get("filename") or "")).name
    _require(
        filename.lower().endswith(".exe"),
        "The update information does not name a Windows installer.",
    )

    arguments = raw.get("silent_args")
    silent_args = (
        tuple(str(argument) for argument in arguments)
        if isinstance(arguments, list)
        else ()
    )

    return UpdateArtifact(
        url=url,
        filename=filename,
        sha256=sha256,
        size=size,
        silent_args=silent_args,
    )


def _parse_manifest(payload: object, channel: UpdateChannel) -> Update:
    _require(isinstance(payload, dict), "The update information is not in the expected format.")
    assert isinstance(payload, dict)

    try:
        schema = int(payload.get("schema") or 0)
    except (TypeError, ValueError):
        schema = 0
    # Refused rather than misread, exactly as accounts.json and settings.json
    # are: a manifest written for a later client may mean something different
    # by the same field names.
    _require(
        schema <= UPDATE_SCHEMA,
        f"The update information was published for a newer version of {APP_NAME}.",
    )

    product = str(payload.get("product") or "")
    _require(
        product == UPDATE_PRODUCT,
        f"The update information at that address is for '{product}', not {APP_NAME}.",
    )

    version = str(payload.get("version") or "")
    _require(
        bool(parse_version(version)), "The update information declares no readable version."
    )

    published_channel = str(payload.get("channel") or channel.value)
    if published_channel != channel.value:
        # Not fatal - the version and the checksum are what matter - but it
        # means the host is serving the wrong file, and that is worth a line.
        _log.warning(
            "The %s manifest describes itself as '%s'", channel.value, published_channel
        )

    try:
        rollout = int(payload.get("rollout", 100))
    except (TypeError, ValueError):
        rollout = 100

    return Update(
        version=version,
        channel=channel,
        artifact=_parse_artifact(payload.get("artifact")),
        notes_url=str(payload.get("notes_url") or ""),
        released=str(payload.get("released") or ""),
        minimum_supported=str(payload.get("minimum_supported") or ""),
        mandatory=bool(payload.get("mandatory", False)),
        signed=bool(payload.get("signed", False)),
        rollout=max(0, min(100, rollout)),
    )


#: Statuses that mean "there is no such document", as opposed to "the request
#: went wrong". A channel nobody has published to has no manifest at all, so
#: this is what an empty channel looks like from the client.
_MISSING_STATUSES: Final[frozenset[int]] = frozenset({404, 410})


def _get_manifest(
    url: str, client: httpx.Client, *, empty_message: str = ""
) -> dict[str, object]:
    """Fetch one manifest document.

    ``follow_redirects`` matters more than it looks: GitHub Pages answers a
    ``github.io`` address with a permanent redirect once a custom domain is
    bound, so a client compiled against the old URL keeps working after the
    move - which is half of why starting there was safe.

    ``empty_message`` is passed only for a channel's own URL, and turns a 404
    into :class:`UpdateChannelEmpty` rather than an error. See that class.
    """
    try:
        response = client.get(url)
        response.raise_for_status()
        body = response.content[: UPDATE_MANIFEST_MAX_BYTES + 1]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if empty_message and status in _MISSING_STATUSES:
            raise UpdateChannelEmpty(empty_message) from exc
        raise UpdateError(f"The update service answered HTTP {status}.") from exc
    except httpx.HTTPError as exc:
        raise UpdateError(f"Could not reach the update service: {exc}") from exc

    _require(
        len(body) <= UPDATE_MANIFEST_MAX_BYTES,
        "The update service returned something far too large to be update information.",
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("The update service did not return update information.") from exc
    _require(
        isinstance(payload, dict), "The update service did not return update information."
    )
    assert isinstance(payload, dict)
    return payload


def _may_follow(current: str, following: str) -> bool:
    """Whether a manifest move is allowed to go to ``following``.

    Stated as "must not downgrade the transport" rather than "must be HTTPS",
    because that is the actual rule: the point is that moving the hosting can
    never quietly cost the installed base its TLS. In the field the chain starts
    at the HTTPS URL compiled into the binary, so every hop is HTTPS; the
    weaker case exists only for a local harness.
    """
    scheme = urlparse(following).scheme
    return scheme == "https" or scheme == urlparse(current).scheme


def _resolve_manifest(
    channel: UpdateChannel, client: httpx.Client
) -> tuple[dict[str, object], str]:
    """Follow ``next_manifest_url`` to the manifest that is actually current.

    The hop cap and the seen-set are not paranoia about an attacker - a manifest
    is only as trustworthy as the host it came from either way - they are about
    a mistake. A manifest that points at itself, or two that point at each
    other, would otherwise be an infinite request loop on every till in the
    field, which is a denial of service we would have inflicted on our own host.
    """
    url = manifest_url(channel)
    seen = {url}
    # Only the first fetch may report an empty channel. After a hop the client
    # has been *told* where to look, so a missing document there is a broken
    # chain and has to be reported as the error it is.
    empty = (
        f"No {channel.value} releases have been published yet, so there is "
        "nothing to update to."
    )
    for _hop in range(UPDATE_MAX_HOPS + 1):
        payload = _get_manifest(url, client, empty_message=empty if not _hop else "")
        following = payload.get("next_manifest_url")
        if not following or not isinstance(following, str):
            return payload, url
        if not _may_follow(url, following):
            _log.warning("Ignoring a next_manifest_url that would drop TLS: %s", following)
            return payload, url
        if following in seen:
            _log.warning("next_manifest_url loops back to %s; stopping here", following)
            return payload, url
        _log.info("Update information has moved to %s", following)
        seen.add(following)
        url = following
    raise UpdateError("The update service redirected too many times.")


#: What :func:`_look_for_update` returns instead of raising when the channel has
#: nothing published. A sentinel rather than ``None``, because ``None`` already
#: means "this build is current" and the card says something different for each.
_CHANNEL_EMPTY: Final[object] = object()


def _look_for_update(*args: object, **kwargs: object) -> object:
    """:func:`fetch_update`, with an empty channel turned into a return value.

    Only :class:`UpdateService` uses this, and only because ``run_async`` logs
    a full traceback for anything that raises. An empty channel is a supported
    configuration - a till following Beta before the first pre-release - and a
    stack trace under "Background task fetch_update failed" every four hours is
    exactly the sort of thing that sends somebody reading a till's log chasing
    a fault that is not there. ``fetch_update`` itself keeps raising: for every
    other caller an exception is the honest answer.
    """
    try:
        return fetch_update(*args, **kwargs)  # type: ignore[arg-type]
    except UpdateChannelEmpty as exc:
        _log.info("Update check: %s", exc)
        return _CHANNEL_EMPTY


def rollout_bucket(install_id: str, version: str) -> int:
    """This installation's 0-99 place in the rollout for ``version``.

    The version is part of the hash on purpose. Hashing the id alone gives a
    machine the same place in the queue for every release it will ever be
    offered, so the same unlucky tills would be the guinea pigs for all of
    them - and the ones at the back would never see a release that was rolled
    out to 50% and then superseded.
    """
    digest = hashlib.sha256(f"{install_id}:{version}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def fetch_update(
    channel: UpdateChannel = UpdateChannel.STABLE,
    *,
    current_version: str = APP_VERSION,
    install_id: str = "",
    honour_rollout: bool = True,
    client: httpx.Client | None = None,
) -> Update | None:
    """Ask the update service what is current. Blocking; call through ``run_async``.

    Returns the release to offer, or ``None`` when there is nothing to offer -
    which covers both "this build is current" and "a staged rollout has not
    reached this machine yet". The two are distinguished in the log, not in the
    return value, because the caller does the same thing either way.
    """
    owned = client is None
    client = client or httpx.Client(
        timeout=UPDATE_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        payload, url = _resolve_manifest(channel, client)
    finally:
        if owned:
            client.close()

    update = _parse_manifest(payload, channel)
    _log.info(
        "Update check: channel=%s manifest=%s offers=%s running=%s",
        channel.value,
        url,
        update.version,
        current_version,
    )

    if not is_newer_version(update.version, current_version):
        return None

    required = update.is_required_for(current_version)
    if honour_rollout and not required and update.rollout < 100:
        # No id means no stable bucket, so the honest thing is to wait for the
        # next check rather than roll a fresh dice and call it a rollout.
        bucket = rollout_bucket(install_id, update.version) if install_id else 100
        if bucket >= update.rollout:
            _log.info(
                "Holding %s back: rollout is %d%% and this installation is bucket %d",
                update.version,
                update.rollout,
                bucket,
            )
            return None

    if required:
        _log.info("%s is required for a build on %s", update.version, current_version)
    return update


# --- downloading -----------------------------------------------------------


def installer_path(update: Update) -> Path:
    """Where ``update``'s installer lives once it is complete.

    Named after the version as well as the manifest's filename, so two channels
    or two releases cannot collide on one file - and so a file already on disk
    can be recognised as belonging to the release being offered.
    """
    return updates_dir() / f"{update.version}-{update.artifact.filename}"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def download_update(
    update: Update,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Fetch the installer, verify it, and return where it landed.

    Blocking; call through ``run_async``. Three things about the mechanics:

    * the bytes go to a ``.part`` file and are renamed only once the checksum
      matches, so an interrupted download can never be mistaken for a finished
      one by the next launch;
    * an already-verified copy is reused. A till that was offered an update
      yesterday and restarted overnight should not fetch 140 MB again to be
      offered the same one;
    * ``cancelled`` is polled every chunk, and it is what makes closing the app
      mid-download immediate. Qt waits on its thread pool at process exit, so
      without it a user who quits during a download waits out the whole
      transfer in front of a window that has already gone.
    """
    target = installer_path(update)
    if target.exists() and _sha256_of(target) == update.artifact.sha256:
        _log.info("Reusing the verified installer already at %s", target)
        _verify_signature_if_required(update, target)
        return target

    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    reported = 0

    try:
        with (
            httpx.Client(
                timeout=httpx.Timeout(UPDATE_DOWNLOAD_TIMEOUT),
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client,
            client.stream("GET", update.artifact.url) as response,
        ):
            response.raise_for_status()
            total = int(response.headers.get("content-length") or update.artifact.size)
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    if cancelled is not None and cancelled():
                        raise _Cancelled
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None and received - reported >= _PROGRESS_STEP_BYTES:
                        reported = received
                        progress(received, total)
        if progress is not None:
            progress(received, received)
    except _Cancelled:
        partial.unlink(missing_ok=True)
        raise
    except httpx.HTTPStatusError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The download answered HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The update could not be downloaded: {exc}") from exc
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The update could not be saved: {exc}") from exc

    actual = digest.hexdigest()
    if actual != update.artifact.sha256:
        partial.unlink(missing_ok=True)
        _log.error(
            "Checksum mismatch for %s: expected %s, got %s",
            update.artifact.filename,
            update.artifact.sha256,
            actual,
        )
        raise UpdateError(
            "The downloaded update did not match its checksum, so it has been "
            "discarded. Nothing has been installed."
        )

    try:
        os.replace(partial, target)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The update could not be saved: {exc}") from exc

    _verify_signature_if_required(update, target)
    prune_installers(keep=target)
    _log.info("Downloaded and verified %s (%d bytes)", target.name, received)
    return target


def prune_installers(*, keep: Path | None = None) -> None:
    """Keep the updates cache from growing by 140 MB per release, forever."""
    try:
        files = sorted(
            (path for path in updates_dir().glob("*.exe") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:  # pragma: no cover - a cache we cannot list is not fatal
        _log.debug("Could not list the updates cache: %s", exc)
        return
    for stale in files[UPDATE_KEEP_INSTALLERS:]:
        if keep is not None and stale == keep:
            continue
        with contextlib.suppress(OSError):
            stale.unlink()
            _log.info("Removed the superseded installer %s", stale.name)
    for orphan in updates_dir().glob("*.part"):
        with contextlib.suppress(OSError):
            orphan.unlink()


# --- Authenticode ----------------------------------------------------------

#: ``WinVerifyTrust`` results worth translating. Everything else is reported as
#: its code, which is more use to whoever has to look it up than a guess would be.
#:
#: ``0xC000A000`` is the one that is not obvious and is measured rather than
#: looked up: a signed executable whose bytes have been altered comes back as
#: ``STATUS_INVALID_IMAGE_HASH``, not as the ``TRUST_E_BAD_DIGEST`` the name
#: suggests. That is the exact case this check exists to catch, so it must not
#: fall through to a bare hex code.
_TRUST_MESSAGES = {
    0x800B0100: "it is not signed at all",
    0x800B0101: "its certificate has expired",
    0x800B0109: "its certificate is not from a trusted authority",
    0x800B0111: "its certificate is explicitly distrusted",
    0x800B010C: "its certificate has been revoked",
    0x80096010: "its signature does not match its contents",
    0xC000A000: "its signature does not match its contents",
}


def verify_authenticode(path: Path) -> None:
    """Raise :class:`UpdateError` unless ``path`` carries a signature Windows trusts.

    Called only when the manifest's ``signed`` field is true - see the module
    docstring for why that gate exists rather than a hardcoded choice.

    This checks that Windows trusts the signature, not *whose* it is. Pinning
    the publisher would mean parsing the certificate subject, and the stronger
    guarantee is already elsewhere: the SHA-256 comes from a manifest fetched
    over TLS from a host we control, so Authenticode here is the second lock -
    and the one whose result the user also sees on the UAC prompt.

    One measured surprise, in case this is ever tested by hand: ``WTD_CHOICE_FILE``
    reads the signature **embedded in the file** and does not consult the
    security catalogues, so a Windows system binary - ``notepad.exe``, say - is
    reported here as "not signed at all" even though Explorer shows it as
    signed. That is correct for this use: the installer is signed by signtool
    with an embedded signature, which is what this mode verifies. Verify against
    a third-party installer, not against something out of ``System32``.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows-only concern
        raise UpdateError("Signed updates can only be verified on Windows.")

    import ctypes
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        )

    class _FileInfo(ctypes.Structure):
        _fields_ = (
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", ctypes.c_wchar_p),
            ("hFile", ctypes.c_void_p),
            ("pgKnownSubject", ctypes.c_void_p),
        )

    class _TrustData(ctypes.Structure):
        _fields_ = (
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.c_void_p),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", ctypes.c_void_p),
            ("pwszURLReference", ctypes.c_wchar_p),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        )

    # WINTRUST_ACTION_GENERIC_VERIFY_V2.
    action = _Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = _FileInfo(
        cbStruct=ctypes.sizeof(_FileInfo),
        pcwszFilePath=str(path),
        hFile=None,
        pgKnownSubject=None,
    )
    data = _TrustData(
        cbStruct=ctypes.sizeof(_TrustData),
        dwUIChoice=2,  # WTD_UI_NONE - there is nobody here to answer a prompt
        fdwRevocationChecks=0,  # WTD_REVOKE_NONE
        dwUnionChoice=1,  # WTD_CHOICE_FILE
        pFile=ctypes.cast(ctypes.byref(file_info), ctypes.c_void_p),
        dwStateAction=1,  # WTD_STATEACTION_VERIFY
        dwProvFlags=0x100,  # WTD_SAFER_FLAG
        dwUIContext=0,
    )

    wintrust = ctypes.WinDLL("wintrust")
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    try:
        result = int(wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data)))
    finally:
        # WTD_STATEACTION_CLOSE. The verify call allocates state hanging off
        # hWVTStateData, and this second call is the only thing that frees it.
        data.dwStateAction = 2
        with contextlib.suppress(OSError):
            wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))

    if result == 0:
        _log.info("The Authenticode signature on %s is trusted", path.name)
        return

    code = result & 0xFFFFFFFF
    reason = _TRUST_MESSAGES.get(code, f"Windows reported 0x{code:08X}")
    _log.error("Refusing to install %s: %s", path.name, reason)
    raise UpdateError(
        f"The downloaded update was not installed because {reason}. "
        "Nothing has changed on this computer."
    )


def _verify_signature_if_required(update: Update, path: Path) -> None:
    if not update.signed:
        _log.info(
            "%s is published unsigned, so no Authenticode check was made; its "
            "SHA-256 matched.",
            path.name,
        )
        return
    verify_authenticode(path)


# --- installing ------------------------------------------------------------


def is_installed_build() -> bool:
    """Whether this process is the packaged application rather than a checkout.

    Only the *automatic* check is gated on this. A source checkout has no
    installed copy to upgrade, so a toast offering to replace it every four
    hours is noise - but an explicit "check now" still answers, and running the
    downloaded installer from a developer's machine is an ordinary thing to want.
    """
    return bool(getattr(sys, "frozen", False))


def install_update(path: Path, artifact: UpdateArtifact | None = None) -> None:
    """Hand ``path`` to Windows and return. The installer takes it from there.

    Deliberately **not** ``QProcess.startDetached``. The installer is
    per-machine, so its manifest requires elevation, and ``CreateProcess`` -
    which is what ``QProcess`` uses - fails outright with
    ``ERROR_ELEVATION_REQUIRED`` rather than raising the UAC prompt.
    ``ShellExecuteW`` is the call that elevates, so it is the one used here.

    Note what this does *not* do: quit. See the module docstring - the app is
    closed and restarted by Inno's Restart Manager, and doing it ourselves
    leaves the till with nothing running.
    """
    _require(path.exists(), "The downloaded update is no longer on disk.")
    if sys.platform != "win32":  # pragma: no cover - Windows-only concern
        raise UpdateError(f"{APP_NAME} can only install updates on Windows.")

    arguments = " ".join(artifact.silent_args) if artifact and artifact.silent_args else ""
    _log.info("Starting the installer: %s %s", path.name, arguments)

    import ctypes

    # SW_SHOWNORMAL. /SILENT still shows a progress window, which is wanted:
    # the app is about to be closed, and a till that goes blank with no
    # explanation looks like a crash.
    result = int(
        ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), arguments, None, 1)
    )
    if result > 32:
        return
    if result == 5:
        # SE_ERR_ACCESSDENIED, which is what a declined UAC prompt comes back
        # as - and the only one of these the user did on purpose.
        raise UpdateError(
            "The update needs permission to install, and that was declined. "
            f"Nothing has changed; {APP_NAME} is still running."
        )
    raise UpdateError(f"Windows would not start the update installer (error {result}).")


# --- the service -----------------------------------------------------------


class UpdateService(QObject):
    """Checks for updates on a timer, and downloads one when asked to.

    Owned by the :class:`~bytesraw_erp.ui.app_context.AppContext`, like the
    print service, because two screens need it: the Odoo page raises the toast
    and the settings page offers the controls. Nothing here touches a widget -
    it reports through signals, and both pages are projections of them.
    """

    #: A check has started. The settings page shows it; nothing else does.
    checking = Signal()
    #: A newer release is available, carrying an :class:`Update`.
    update_available = Signal(object)
    #: The check finished and this build is already current.
    up_to_date = Signal()
    #: The check itself failed, with an end-user-readable reason.
    check_failed = Signal(str)

    #: A download has started, carrying its :class:`Update`.
    download_started = Signal(object)
    #: Bytes received, bytes expected.
    download_progress = Signal(int, int)
    #: The installer is verified and on disk, carrying its :class:`Path`.
    download_finished = Signal(object)
    download_failed = Signal(str)

    def __init__(self, settings: SettingsStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        #: The release last found, so a page opened afterwards can still show
        #: it without asking the network again.
        self._available: Update | None = None
        #: Set when the channel has no manifest at all. Distinct from "up to
        #: date": this build is not current, there is simply nothing published
        #: to compare it with, and a card saying "up to date" would be a lie.
        self._channel_empty = False
        self._checking = False
        self._downloading = False
        #: Polled by the download worker every chunk. Raised on shutdown, which
        #: is what keeps closing the app immediate during a 140 MB transfer.
        self._cancelled = False
        #: Set once an installer has been handed to Windows. From that moment
        #: the app is waiting to be closed by it, and starting another check or
        #: another download would be nonsense.
        self._installing = False

        self._timer = QTimer(self)
        self._timer.setInterval(UPDATE_CHECK_SECONDS * 1000)
        self._timer.timeout.connect(self.check)

    # -- state -------------------------------------------------------------

    @property
    def available(self) -> Update | None:
        return self._available

    @property
    def channel_empty(self) -> bool:
        """Whether the last check found the channel had nothing published."""
        return self._channel_empty

    @property
    def busy(self) -> bool:
        return self._checking or self._downloading

    @property
    def checking_now(self) -> bool:
        return self._checking

    @property
    def downloading(self) -> bool:
        return self._downloading

    @property
    def installing(self) -> bool:
        return self._installing

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Arm the launch-time check and the periodic one.

        Both are skipped when automatic checking is off or this is not an
        installed build; "check now" on the settings page works regardless.
        """
        if not self._settings.updates.check_automatically:
            _log.info("Automatic update checks are off")
            return
        if not is_installed_build():
            _log.info("Not an installed build, so automatic update checks are skipped")
            return
        # Delayed so the check does not compete with sign-in and the first page
        # load for the same connection.
        QTimer.singleShot(UPDATE_LAUNCH_DELAY_SECONDS * 1000, self._check_on_launch)
        self._timer.start()

    def _check_on_launch(self) -> None:
        if self._cancelled:
            return
        self.check()

    def apply_settings(self) -> None:
        """Re-read the stored settings after the user has changed them.

        The emptiness flag is dropped here for the same reason the settings
        page drops the pending release: both describe the *other* channel the
        moment the user switches, and a card still reporting "no beta releases"
        over a stable install has outlived its answer.
        """
        self._channel_empty = False
        wanted = self._settings.updates.check_automatically and is_installed_build()
        if wanted and not self._timer.isActive():
            self._timer.start()
        elif not wanted:
            self._timer.stop()

    def shutdown(self) -> None:
        """Stop checking, and tell an in-flight download to give up.

        Called from ``AppContext.shutdown`` *before* the task gate closes:
        cancelling the callbacks is not enough on its own, because the worker
        would still be streaming bytes that Qt then waits for at process exit.
        """
        self._cancelled = True
        self._timer.stop()

    # -- checking ----------------------------------------------------------

    def check(self, *, manual: bool = False) -> None:
        """Ask the update service what is current.

        ``manual`` comes from the settings page's button and changes one thing:
        a staged rollout is ignored, because a user who asked the question is
        owed the true answer rather than the one their bucket allows.
        """
        if self._checking or self._installing or self._cancelled:
            return
        self._checking = True
        self.checking.emit()

        channel = self._settings.updates.channel
        install_id = self._settings.ensure_install_id()

        def found(update: object) -> None:
            self._checking = False
            self._settings.record_update_check()
            # An empty channel is news of a sort - the check reached the host
            # and got a straight answer - so it counts as a check and reports
            # "no news", never a failure. It is not "up to date" either, which
            # is why it has a flag of its own rather than folding into None.
            self._channel_empty = update is _CHANNEL_EMPTY
            if update is None or self._channel_empty:
                self._available = None
                self.up_to_date.emit()
                return
            self._available = update if isinstance(update, Update) else None
            if self._available is not None:
                self.update_available.emit(self._available)

        def failed(exc: Exception) -> None:
            self._checking = False
            # Logged rather than shown unless it was asked for: a till between
            # access points fails this check every four hours, and a toast each
            # time would train the user to ignore toasts.
            _log.info("Update check failed: %s", exc)
            self.check_failed.emit(str(exc))

        run_async(
            _look_for_update,
            channel,
            current_version=APP_VERSION,
            install_id=install_id,
            honour_rollout=not manual,
            on_success=found,
            on_error=failed,
        )

    # -- downloading and installing ----------------------------------------

    def download_and_install(self, update: Update | None = None) -> None:
        """Fetch the installer and, once it verifies, run it.

        One step rather than two by design: the user has already said yes, and a
        second "now really install it" click is a second chance to leave a till
        on an old build with 140 MB of the new one already downloaded.
        """
        update = update or self._available
        if update is None or self._downloading or self._installing or self._cancelled:
            return
        self._downloading = True
        self.download_started.emit(update)
        artifact = update.artifact

        def ready(path: object) -> None:
            self._downloading = False
            if not isinstance(path, Path):  # pragma: no cover - defensive
                return
            self.download_finished.emit(path)
            self._install(path, artifact)

        def failed(exc: Exception) -> None:
            self._downloading = False
            if isinstance(exc, _Cancelled):
                _log.info("The update download was cancelled")
                return
            _log.warning("The update download failed: %s", exc)
            self.download_failed.emit(str(exc))

        run_async(
            download_update,
            update,
            progress=self._report_progress,
            cancelled=lambda: self._cancelled,
            on_success=ready,
            on_error=failed,
        )

    def _report_progress(self, received: int, total: int) -> None:
        """Called on the worker thread; the signal is queued to the GUI one."""
        self.download_progress.emit(received, total)

    def _install(self, path: Path, artifact: UpdateArtifact) -> None:
        self._installing = True
        self._timer.stop()
        try:
            install_update(path, artifact)
        except UpdateError as exc:
            # A declined UAC prompt lands here, and the app carries on running
            # the build it has - so the flag has to come back down or the user
            # could never try again without restarting.
            self._installing = False
            self.download_failed.emit(str(exc))
