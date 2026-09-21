"""Upgrading over a running till: closing the app, and starting it again.

Both halves of that hand-over were broken, and both were invisible from inside
the application - which is why they are pinned here from the outside, against
the installer script and the workflow that publishes the switches.

Measured by driving the Restart Manager's own API against an installed 0.2.0:

* the shell is listed as ``RmMainWindow`` and closes gracefully on its own -
  a non-forced ``RmShutdown`` of just its executable returns 0 and takes the
  QtWebEngine children with it;
* but every ``QtWebEngineProcess.exe`` child is listed as ``RmUnknownApp``,
  the class the Restart Manager will not shut down gracefully at all, and one
  of those fails the *whole* ``RmShutdown`` with ``ERROR_FAIL_SHUTDOWN``
  (351) - leaving the shell running too. That is Inno's "Setup was unable to
  automatically close all applications" dialog, and it stopped every upgrade.
  The same shutdown with ``RmForceShutdown`` returns 0 and leaves nothing
  behind, which is ``CloseApplications=force``;
* and the shell is listed with ``bRestartable=False``, so
  ``/RESTARTAPPLICATIONS`` - which is ``RmRestart``, and restarts only a
  process that called ``RegisterApplicationRestart`` - was asking for
  something that could never happen. Measured A/B on one process, with and
  without that call: ``restartable=True`` against ``restartable=False``.

The registration only helps a build that carries it, so the installer also
takes ``/RELAUNCH``: that is what reaches the tills already running 0.2.0,
because the switches come from the published manifest rather than from the
installed binary.
"""

from __future__ import annotations

import ctypes
import re
import sys
from pathlib import Path

import pytest

from bytesraw_erp import app as app_module

_ROOT = Path(__file__).resolve().parents[1]
_ISS = (_ROOT / "packaging" / "bytesraw-erp.iss").read_text(encoding="utf-8")
_WORKFLOW = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

#: The switch the app passes and the installer reads. Named once here so a test
#: cannot agree with itself while the two sides disagree.
_RELAUNCH = "/RELAUNCH"


def _run_entries() -> list[str]:
    """The ``[Run]`` section's entries, comments and blank lines dropped."""
    section = _ISS.split("[Run]", 1)[1].split("[Code]", 1)[0]
    return [
        line.strip()
        for line in section.splitlines()
        if line.strip() and not line.strip().startswith(";")
    ]


# -- closing -----------------------------------------------------------------


def test_the_installer_may_force_a_running_till_closed() -> None:
    """``yes`` cannot close this app, because of the QtWebEngine children."""
    assert re.search(r"^CloseApplications=force$", _ISS, re.M)


# -- starting again ----------------------------------------------------------


def test_the_updaters_own_install_starts_the_app_again() -> None:
    """Gated on the switch, and never as the administrator who elevated Setup.

    Running the app elevated would have it write its accounts, settings and
    web profiles as whoever answered the UAC prompt rather than as the user at
    the till.
    """
    relaunch = [entry for entry in _run_entries() if "RelaunchRequested" in entry]
    assert len(relaunch) == 1
    assert "runasoriginaluser" in relaunch[0]
    assert "nowait" in relaunch[0]
    assert f"'{_RELAUNCH}'" in _ISS.split("function RelaunchRequested", 1)[1]


def test_the_finished_page_entry_still_skips_a_silent_install() -> None:
    """Otherwise an upgrade would start the app twice over.

    The single-instance guard would fold the second launch into the first, so
    the cost is a window coming forward rather than two copies - but the
    checkbox on the finished page is for someone who is *looking* at it.
    """
    interactive = [entry for entry in _run_entries() if "postinstall" in entry]
    assert len(interactive) == 1
    assert "skipifsilent" in interactive[0]


def test_the_published_manifest_passes_the_switch_the_installer_reads() -> None:
    """The release workflow writes the switches every installed build obeys."""
    declared = re.search(r"silent_args = @\((?P<args>[^)]*)\)", _WORKFLOW)
    assert declared is not None
    arguments = re.findall(r"'([^']+)'", declared.group("args"))
    assert arguments == ["/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", _RELAUNCH]


@pytest.mark.skipif(sys.platform != "win32", reason="the Restart Manager is Windows'")
def test_the_app_asks_windows_to_restart_it_after_an_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no command line, and without declining the one case that matters."""
    calls: list[tuple[object, int]] = []

    def register(command_line: object, flags: int) -> int:
        calls.append((command_line, flags))
        return 0

    monkeypatch.setattr(app_module, "is_installed_build", lambda: True)
    monkeypatch.setattr(
        ctypes.windll.kernel32, "RegisterApplicationRestart", register, raising=False
    )

    app_module._register_for_restart()

    assert len(calls) == 1
    command_line, flags = calls[0]
    # Windows supplies the executable path itself; passing it again would
    # arrive as argv[1].
    assert command_line is None
    # RESTART_NO_PATCH is 4, and an installer replacing the files under a
    # running till is exactly the patch case this exists for.
    assert flags & 4 == 0
    assert flags == 1 | 2 | 8


@pytest.mark.skipif(sys.platform != "win32", reason="the Restart Manager is Windows'")
def test_a_source_checkout_does_not_register_for_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is nothing to replace, and nothing sensible to restart.

    Windows would relaunch the interpreter with no arguments, which is a
    Python prompt rather than the app.
    """
    calls: list[object] = []

    monkeypatch.setattr(app_module, "is_installed_build", lambda: False)
    monkeypatch.setattr(
        ctypes.windll.kernel32,
        "RegisterApplicationRestart",
        lambda command_line, flags: calls.append(flags) or 0,
        raising=False,
    )

    app_module._register_for_restart()

    assert calls == []


# -- the identity the two sides share ----------------------------------------


def test_the_app_user_model_id_matches_the_installers() -> None:
    """A shortcut carrying a different one silently unpins itself.

    Windows would treat the pinned shortcut and the running window as two
    different applications, which is the whole reason the app claims the
    identity at startup.
    """
    declared = re.search(r'#define MyAppUserModelID "(?P<value>[^"]+)"', _ISS)
    assert declared is not None
    assert declared.group("value") == app_module._APP_USER_MODEL_ID
