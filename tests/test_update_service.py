"""Update service tests.

The theme of this file is that everything the update path acts on arrives from
the network, so nothing may be taken on trust: not the version, not the
checksum, not the filename, and not the claim that there is anything to install
at all. A local HTTP server stands in for the update host so the transport, the
redirect chain and the streaming download are all really exercised.

Two deliberate details:

* the manifests here carry an ``https`` artifact URL that is never fetched,
  because the parser refuses anything else; the download tests build their own
  artifact pointing at the local server, which is legitimate because the parser
  is the gate and ``download_update`` trusts what it was handed. A separate test
  pins that refusal.
* the redirect chain is exercised over ``http``, which the service allows only
  because it never *downgrades* the transport - see ``_may_follow``. A chain that
  starts at an https URL, as every shipped binary's does, can only stay there.
* ``updates_dir`` is patched **on the update service module**, not on
  ``core.paths``, because that is where it was imported. Patching the
  definition would leave the real ``%LOCALAPPDATA%`` cache in play.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from bytesraw_erp.constants import UPDATE_KEEP_INSTALLERS
from bytesraw_erp.core.errors import UpdateError
from bytesraw_erp.data.models import (
    UpdateChannel,
    UpdateSettings,
    is_newer_version,
    parse_version,
)
from bytesraw_erp.services import update_service
from bytesraw_erp.services.update_service import (
    Update,
    UpdateArtifact,
    UpdateChannelEmpty,
    download_update,
    fetch_update,
    installer_path,
    rollout_bucket,
    verify_authenticode,
)

_ARTIFACT_PATH = "/BytesrawERP-setup.exe"
_PAYLOAD = b"MZ" + b"pretend installer" * 40_000  # ~680 KB, several chunks


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(**overrides: object) -> dict[str, object]:
    """A valid stable manifest, shaped exactly like the published one."""
    payload: dict[str, object] = {
        "schema": 1,
        "product": "bytesraw-erp",
        "channel": "stable",
        "version": "0.2.0",
        "released": "2026-09-13",
        "minimum_supported": "0.1.0",
        "mandatory": False,
        "rollout": 100,
        "signed": False,
        "notes_url": "https://example.invalid/notes",
        "next_manifest_url": None,
        "artifact": {
            # A real https URL, because the parser refuses anything else - and
            # nothing in a manifest test fetches it. The download tests build
            # their own artifact pointing at the local server instead.
            "url": "https://example.invalid/BytesrawERP-0.2.0-setup.exe",
            "filename": "BytesrawERP-0.2.0-setup.exe",
            "sha256": _sha256(_PAYLOAD),
            "size": len(_PAYLOAD),
            "installer": "inno-setup",
            "silent_args": ["/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
        },
    }
    payload.update(overrides)
    return payload


class _Handler(BaseHTTPRequestHandler):
    #: path -> body. Rewritten per test.
    documents: ClassVar[dict[str, bytes]] = {}
    #: How many times each path was fetched, so "did it download again?" can
    #: be asked rather than assumed.
    hits: ClassVar[dict[str, int]] = {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        type(self).hits[path] = type(self).hits.get(path, 0) + 1

        if path == _ARTIFACT_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(_PAYLOAD)))
            self.end_headers()
            self.wfile.write(_PAYLOAD)
            return

        body = type(self).documents.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence the default stderr access log."""


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def host(server: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the service at the local stand-in and start with a clean slate."""
    _Handler.documents = {}
    _Handler.hits = {}
    monkeypatch.setattr(update_service, "UPDATE_BASE_URL", f"{server}/erp")
    return server


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep downloaded installers out of the real %LOCALAPPDATA% cache."""
    directory = tmp_path / "updates"
    directory.mkdir()
    monkeypatch.setattr(update_service, "updates_dir", lambda: directory)
    return directory


def _publish(_host: str, channel: str = "stable", **overrides: object) -> dict[str, object]:
    """Serve a manifest at ``/erp/<channel>.json`` on the stand-in host."""
    payload = _manifest(**overrides)
    _Handler.documents[f"/erp/{channel}.json"] = json.dumps(payload).encode()
    return payload


def _artifact(host: str, *, sha256: str | None = None) -> UpdateArtifact:
    return UpdateArtifact(
        url=f"{host}{_ARTIFACT_PATH}",
        filename="BytesrawERP-0.2.0-setup.exe",
        sha256=sha256 if sha256 is not None else _sha256(_PAYLOAD),
        size=len(_PAYLOAD),
        silent_args=("/SILENT",),
    )


def _update(host: str, **overrides: object) -> Update:
    fields: dict[str, object] = {
        "version": "0.2.0",
        "channel": UpdateChannel.STABLE,
        "artifact": _artifact(host),
    }
    fields.update(overrides)
    return Update(**fields)  # type: ignore[arg-type]


# --- comparing versions -----------------------------------------------------


def test_ten_is_newer_than_nine() -> None:
    """The reason versions are not compared as strings: "0.10" < "0.9" lexically."""
    assert is_newer_version("0.10.0", "0.9.0") is True
    assert is_newer_version("0.9.0", "0.10.0") is False


def test_a_padded_version_is_the_same_release() -> None:
    assert is_newer_version("0.2", "0.2.0") is False
    assert is_newer_version("0.2.0", "0.2") is False


def test_a_leading_v_is_tolerated() -> None:
    assert is_newer_version("v0.2.0", "0.1.8") is True


def test_a_suffix_truncates_rather_than_raising() -> None:
    assert parse_version("0.2.0-rc1") == (0, 2, 0)


def test_an_unreadable_version_is_never_an_upgrade() -> None:
    """Offering a version the app cannot even parse is worse than offering none."""
    assert is_newer_version("", "0.1.8") is False
    assert is_newer_version("latest", "0.1.8") is False


# --- reading the manifest ---------------------------------------------------


def test_a_current_build_is_offered_nothing(host: str) -> None:
    _publish(host, version="0.1.8")
    assert fetch_update(current_version="0.1.8") is None


def test_an_older_build_is_offered_the_release(host: str) -> None:
    _publish(host)
    update = fetch_update(current_version="0.1.8")
    assert update is not None
    assert update.version == "0.2.0"
    assert update.notes_url == "https://example.invalid/notes"
    assert update.artifact.silent_args == (
        "/SILENT",
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
    )
    assert update.artifact.sha256 == _sha256(_PAYLOAD)


def test_a_manifest_from_a_newer_schema_is_refused(host: str) -> None:
    """Refused rather than misread - the same forward guard as the JSON stores."""
    _publish(host, schema=99)
    with pytest.raises(UpdateError, match="newer version"):
        fetch_update(current_version="0.1.8")


def test_a_manifest_for_another_product_is_refused(host: str) -> None:
    _publish(host, product="somebody-elses-app")
    with pytest.raises(UpdateError, match="somebody-elses-app"):
        fetch_update(current_version="0.1.8")


def test_a_download_over_plain_http_is_refused(host: str) -> None:
    artifact = dict(_manifest()["artifact"])  # type: ignore[arg-type]
    artifact["url"] = "http://example.invalid/x.exe"
    _publish(host, artifact=artifact)
    with pytest.raises(UpdateError, match="HTTPS"):
        fetch_update(current_version="0.1.8")


def test_a_manifest_with_no_usable_checksum_is_refused(host: str) -> None:
    artifact = dict(_manifest()["artifact"])  # type: ignore[arg-type]
    artifact["sha256"] = "not-a-digest"
    _publish(host, artifact=artifact)
    with pytest.raises(UpdateError, match="checksum"):
        fetch_update(current_version="0.1.8")


def test_an_implausible_size_is_refused(host: str) -> None:
    artifact = dict(_manifest()["artifact"])  # type: ignore[arg-type]
    artifact["size"] = 500 * 1024 * 1024 * 1024
    _publish(host, artifact=artifact)
    with pytest.raises(UpdateError, match="size"):
        fetch_update(current_version="0.1.8")


def test_a_manifest_naming_no_installer_is_refused(host: str) -> None:
    artifact = dict(_manifest()["artifact"])  # type: ignore[arg-type]
    artifact["filename"] = "payload.dll"
    _publish(host, artifact=artifact)
    with pytest.raises(UpdateError, match="Windows installer"):
        fetch_update(current_version="0.1.8")


def test_a_filename_cannot_escape_the_updates_cache(host: str, cache: Path) -> None:
    """A manifest does not get to choose where a 140 MB executable lands."""
    artifact = dict(_manifest()["artifact"])  # type: ignore[arg-type]
    artifact["filename"] = "../../../Startup/evil.exe"
    _publish(host, artifact=artifact)

    update = fetch_update(current_version="0.1.8")
    assert update is not None
    assert update.artifact.filename == "evil.exe"
    assert installer_path(update).parent == cache


def test_an_oversized_body_is_refused(host: str) -> None:
    """A wrong URL must fail, not stream something enormous into memory."""
    _Handler.documents["/erp/stable.json"] = b"x" * (64 * 1024 + 10)
    with pytest.raises(UpdateError, match="too large"):
        fetch_update(current_version="0.1.8")


def test_a_channel_with_no_manifest_is_empty_not_broken(host: str) -> None:
    """One manifest per channel means an unreleased channel has no file at all.

    This is what a till switched to Beta actually met against the live host:
    nothing has ever been pre-released, so ``beta.json`` does not exist and
    GitHub Pages answers 404. Reporting that as "The update service answered
    HTTP 404" put a red error banner over a system working exactly as designed.
    """
    with pytest.raises(UpdateChannelEmpty, match="No stable releases"):
        fetch_update(current_version="0.1.8")


def test_an_empty_channel_is_still_an_update_error(host: str) -> None:
    """Anything that only catches ``UpdateError`` must still get a sentence."""
    with pytest.raises(UpdateError) as caught:
        fetch_update(current_version="0.1.8")
    assert "nothing to update to" in str(caught.value)


def test_a_missing_manifest_after_a_hop_is_a_broken_chain(host: str) -> None:
    """A 404 the client was *sent* to is a misconfiguration, not an empty channel.

    The distinction is the whole point of passing ``empty_message`` for the
    first fetch only: the channel URL is compiled into the binary and may
    legitimately not exist yet, but a ``next_manifest_url`` is a host saying
    "look over there", and nothing being there is a genuine fault.
    """
    _publish(host, next_manifest_url=f"{host}/erp/moved.json")
    with pytest.raises(UpdateError, match="HTTP 404") as caught:
        fetch_update(current_version="0.1.8")
    assert not isinstance(caught.value, UpdateChannelEmpty)


# --- the next_manifest_url chain -------------------------------------------


def test_it_follows_next_manifest_url(host: str) -> None:
    """The only migration path an already-installed binary has."""
    _publish(host, next_manifest_url=f"{host}/erp/moved.json")
    _publish(host, channel="moved", version="0.3.0")

    update = fetch_update(current_version="0.1.8")
    assert update is not None
    assert update.version == "0.3.0"


def test_a_chain_that_loops_stops_rather_than_spinning(host: str) -> None:
    """A manifest pointing at itself would be a self-inflicted request loop."""
    _publish(host, next_manifest_url=f"{host}/erp/stable.json")

    update = fetch_update(current_version="0.1.8")
    assert update is not None
    assert update.version == "0.2.0"
    assert _Handler.hits["/erp/stable.json"] == 1


def test_too_many_hops_is_refused(host: str) -> None:
    for step in range(6):
        _publish(
            host,
            channel=f"hop{step}",
            version=f"0.{step + 2}.0",
            next_manifest_url=f"{host}/erp/hop{step + 1}.json",
        )
    _Handler.documents["/erp/stable.json"] = _Handler.documents["/erp/hop0.json"]

    with pytest.raises(UpdateError, match="redirected too many times"):
        fetch_update(current_version="0.1.8")


def test_a_move_may_not_drop_tls(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Moving the hosting must never quietly cost the installed base its TLS.

    Checked from an https starting point, because that is the only case that
    ships: the chain is followed over http in the tests above only because it
    started there.
    """
    monkeypatch.setattr(update_service, "UPDATE_BASE_URL", "https://example.invalid/erp")
    assert update_service._may_follow(
        "https://example.invalid/erp/stable.json", "http://example.invalid/erp/stable.json"
    ) is False
    assert update_service._may_follow(
        "https://example.invalid/erp/stable.json", "https://elsewhere.invalid/erp/stable.json"
    ) is True


def test_a_downgrading_next_manifest_url_is_ignored_not_fatal(host: str) -> None:
    """Refusing to follow a move is not the same as refusing the manifest it is in."""
    _publish(host, next_manifest_url="ftp://example.invalid/erp/stable.json")
    update = fetch_update(current_version="0.1.8")
    assert update is not None
    assert update.version == "0.2.0"


# --- staged rollout ---------------------------------------------------------


def test_the_bucket_is_stable_for_a_version(host: str) -> None:
    assert rollout_bucket("abc", "0.2.0") == rollout_bucket("abc", "0.2.0")


def test_the_bucket_moves_between_versions() -> None:
    """Otherwise the same tills are the guinea pigs for every release."""
    buckets = {rollout_bucket("abc", f"0.{minor}.0") for minor in range(2, 12)}
    assert len(buckets) > 1


def test_a_staged_rollout_can_hold_a_release_back(host: str) -> None:
    _publish(host, rollout=0)
    assert fetch_update(current_version="0.1.8", install_id="abc") is None


def test_a_full_rollout_reaches_everyone(host: str) -> None:
    _publish(host, rollout=100)
    assert fetch_update(current_version="0.1.8", install_id="abc") is not None


def test_a_manual_check_ignores_the_rollout(host: str) -> None:
    """A user who asked the question is owed the true answer."""
    _publish(host, rollout=0)
    update = fetch_update(current_version="0.1.8", install_id="abc", honour_rollout=False)
    assert update is not None


def test_a_mandatory_release_ignores_the_rollout(host: str) -> None:
    _publish(host, rollout=0, mandatory=True)
    assert fetch_update(current_version="0.1.8", install_id="abc") is not None


def test_a_build_below_the_floor_ignores_the_rollout(host: str) -> None:
    """What ``minimum_supported`` is for: a build that must not stay behind."""
    _publish(host, rollout=0, minimum_supported="0.2.0")
    update = fetch_update(current_version="0.1.8", install_id="abc")
    assert update is not None
    assert update.is_required_for("0.1.8") is True
    assert update.is_required_for("0.2.0") is False


def test_no_install_id_waits_rather_than_rolling_a_fresh_dice(host: str) -> None:
    _publish(host, rollout=99)
    assert fetch_update(current_version="0.1.8", install_id="") is None


# --- downloading ------------------------------------------------------------


def test_it_verifies_the_checksum_and_keeps_the_file(host: str, cache: Path) -> None:
    path = download_update(_update(host))
    assert path.exists()
    assert path.read_bytes() == _PAYLOAD
    assert path.parent == cache
    assert not list(cache.glob("*.part"))


def test_the_name_carries_the_version(host: str, cache: Path) -> None:
    """So two releases cannot collide on one file in the cache."""
    path = download_update(_update(host))
    assert path.name.startswith("0.2.0-")


def test_a_corrupt_download_is_discarded(host: str, cache: Path) -> None:
    """Nothing is left behind that a later run could mistake for verified."""
    update = _update(host, artifact=_artifact(host, sha256="0" * 64))
    with pytest.raises(UpdateError, match="checksum"):
        download_update(update)
    assert list(cache.iterdir()) == []


def test_an_already_verified_installer_is_not_fetched_again(host: str, cache: Path) -> None:
    """A till restarted overnight should not re-fetch 140 MB to be re-offered it."""
    update = _update(host)
    download_update(update)
    assert _Handler.hits[_ARTIFACT_PATH] == 1

    download_update(update)
    assert _Handler.hits[_ARTIFACT_PATH] == 1


def test_a_truncated_file_on_disk_is_downloaded_again(host: str, cache: Path) -> None:
    update = _update(host)
    target = installer_path(update)
    target.write_bytes(b"half a file")

    path = download_update(update)
    assert path.read_bytes() == _PAYLOAD


def test_progress_is_reported(host: str, cache: Path) -> None:
    seen: list[tuple[int, int]] = []
    download_update(_update(host), progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1][0] == len(_PAYLOAD)


def test_cancelling_stops_the_download_and_leaves_nothing_behind(
    host: str, cache: Path
) -> None:
    """What keeps closing the app immediate rather than waiting out a transfer."""
    # The exception type is deliberately private to the service, so this
    # matches on the name rather than importing it.
    with pytest.raises(Exception) as caught:
        download_update(_update(host), cancelled=lambda: True)
    assert type(caught.value).__name__ == "_Cancelled"
    assert list(cache.iterdir()) == []


def test_it_prunes_superseded_installers(host: str, cache: Path) -> None:
    """Otherwise the cache grows by one installer per release, forever."""
    for index, name in enumerate(("0.1.5-old.exe", "0.1.6-old.exe", "0.1.7-old.exe")):
        stale = cache / name
        stale.write_bytes(b"stale")
        os.utime(stale, (1_700_000_000 + index, 1_700_000_000 + index))

    kept = download_update(_update(host))
    remaining = sorted(p.name for p in cache.glob("*.exe"))
    assert kept.name in remaining
    assert len(remaining) <= UPDATE_KEEP_INSTALLERS


# --- the signature gate -----------------------------------------------------


def test_an_unsigned_release_is_not_signature_checked(host: str, cache: Path) -> None:
    """The gate the whole signing plan turns on.

    While the manifest says ``signed: false`` there is no signature to check, so
    the download must succeed on its checksum alone - otherwise no release could
    ship until a certificate existed.
    """
    path = download_update(_update(host, signed=False))
    assert path.exists()


def test_a_release_claiming_a_signature_must_have_one(host: str, cache: Path) -> None:
    """And the other half: flipping ``signed`` to true starts enforcing it.

    The payload here is not a signed executable, so a manifest claiming it is
    signed must be refused rather than installed - which is what makes the flip
    on the update host a real change in behaviour with no new client code.
    """
    with pytest.raises(UpdateError, match="not installed because"):
        download_update(_update(host, signed=True))


def test_a_verified_signature_is_accepted() -> None:
    """Pinned against a real embedded signature, not a mock.

    ``python.exe`` from python.org carries one. Deliberately *not* a binary out
    of System32: those are catalogue-signed, and this check reads the signature
    embedded in the file - see :func:`verify_authenticode`.
    """
    interpreter = Path(sys.executable)
    if _windows_signature_status(interpreter) != "Valid":
        pytest.skip("this interpreter carries no embedded Authenticode signature")
    verify_authenticode(interpreter)


def test_an_unsigned_file_is_refused(tmp_path: Path) -> None:
    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(b"MZ not really an executable")
    with pytest.raises(UpdateError, match="not signed at all"):
        verify_authenticode(unsigned)


def test_a_tampered_signed_file_is_refused(tmp_path: Path) -> None:
    """The case the check exists for, and the one whose result code surprises.

    A signed executable with altered bytes comes back as
    ``STATUS_INVALID_IMAGE_HASH``, not ``TRUST_E_BAD_DIGEST``, so this also pins
    that the message says what went wrong instead of printing a bare code.
    """
    interpreter = Path(sys.executable)
    if _windows_signature_status(interpreter) != "Valid":
        pytest.skip("this interpreter carries no embedded Authenticode signature")

    tampered = tmp_path / "tampered.exe"
    data = bytearray(interpreter.read_bytes())
    data[-64:] = b"\x00" * 64
    tampered.write_bytes(bytes(data))

    with pytest.raises(UpdateError, match="does not match its contents"):
        verify_authenticode(tampered)


def _windows_signature_status(path: Path) -> str:
    """Windows' own verdict on ``path``, so the tests above can skip honestly.

    Asking Windows rather than assuming lets these tests run on a machine whose
    interpreter is unsigned - a system Python, or one built from source - instead
    of failing for a reason that has nothing to do with this code.
    """
    if sys.platform != "win32":
        return "NotSupported"
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-AuthenticodeSignature '{path}').Status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


# --- settings round trip ----------------------------------------------------


def test_update_settings_round_trip() -> None:
    settings = UpdateSettings(
        check_automatically=False,
        channel=UpdateChannel.BETA,
        install_id="deadbeef",
        last_check="2026-09-13T10:00:00+00:00",
    )
    assert UpdateSettings.from_dict(settings.to_dict()) == settings


def test_an_unknown_channel_falls_back_to_stable() -> None:
    assert UpdateSettings.from_dict({"channel": "nightly"}).channel is UpdateChannel.STABLE


def test_an_unreadable_last_check_is_treated_as_never() -> None:
    assert UpdateSettings(last_check="whenever").last_check_at is None
    assert UpdateSettings().last_check_at is None


# --- the service's reading of an empty channel ------------------------------


def test_the_service_treats_an_empty_channel_as_no_news(
    host: str, tmp_path: Path, qtbot
) -> None:
    """It reports ``up_to_date``, never ``check_failed``.

    ``check_failed`` is what the settings page turns into a red banner, and on
    a till moved to Beta - a channel nothing has ever been published to - that
    banner was the only thing the user saw. The check itself succeeded: it
    reached the host and got a straight answer.
    """
    from bytesraw_erp.data.settings_store import SettingsStore
    from bytesraw_erp.services.update_service import UpdateService

    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    service = UpdateService(settings)

    failures: list[str] = []
    service.check_failed.connect(failures.append)
    with qtbot.waitSignal(service.up_to_date, timeout=5000):
        service.check(manual=True)

    assert failures == []
    assert service.channel_empty is True
    assert service.available is None
    # The check happened, so the card may honestly say when it last looked.
    assert settings.updates.last_check_at is not None


def test_a_real_answer_clears_the_empty_flag(host: str, tmp_path: Path, qtbot) -> None:
    """Otherwise a till switched back to a live channel keeps reporting emptiness."""
    from bytesraw_erp.data.settings_store import SettingsStore
    from bytesraw_erp.services.update_service import UpdateService

    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    service = UpdateService(settings)

    with qtbot.waitSignal(service.up_to_date, timeout=5000):
        service.check(manual=True)
    assert service.channel_empty is True

    _publish(host, version="9.9.9")
    with qtbot.waitSignal(service.update_available, timeout=5000):
        service.check(manual=True)

    assert service.channel_empty is False
    assert service.available is not None


def test_switching_channel_drops_the_empty_flag(tmp_path: Path, qtbot) -> None:
    """It described the channel the user has just left."""
    from bytesraw_erp.data.settings_store import SettingsStore
    from bytesraw_erp.services.update_service import UpdateService

    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    service = UpdateService(settings)
    service._channel_empty = True

    service.apply_settings()

    assert service.channel_empty is False
