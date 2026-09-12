"""Choosing how the embedded web view is rasterised.

This is the one piece of configuration that has to be decided *before* the
``QApplication`` is constructed. QtWebEngine reads ``QTWEBENGINE_CHROMIUM_FLAGS``
when it starts Chromium, and Qt picks its OpenGL implementation from
application attributes that are read at the same moment; setting either one
after the fact is silently ignored.

Why the setting exists at all: on a machine whose graphics driver is broken,
Chromium composites the page into a corrupt surface - measured on Intel HD
Graphics (Bay Trail, Celeron J1800) under Windows 10 as regular vertical
black-and-white stripes filling the whole web view, with the native app bar
above it painting perfectly. Nothing in the page is wrong, so no amount of
reloading, re-theming or re-signing-in changes it, and the symptom looks like
a bug in the shell. :class:`~bytesraw_erp.data.models.RenderMode.SOFTWARE`
takes the GPU out of the path and the same page renders correctly, slower.

Both halves are needed. ``--disable-gpu`` moves Chromium's own rasterising and
compositing onto the CPU, and ``AA_UseSoftwareOpenGL`` points Qt at the bundled
software OpenGL library (``opengl32sw.dll``) for the surface the web view is
blitted into - the driver is in that path too.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QCoreApplication, Qt

from bytesraw_erp.data.models import RenderMode

_log = logging.getLogger(__name__)

#: The environment variable QtWebEngine passes on to Chromium verbatim.
CHROMIUM_FLAGS_VAR = "QTWEBENGINE_CHROMIUM_FLAGS"

#: Switches that take the GPU out of Chromium's path. ``--disable-gpu`` implies
#: the other two on current Chromium, which are listed anyway so that a reader
#: of a log line can see what was asked for. Note the absence of
#: ``--disable-software-rasterizer``: that would remove the CPU fallback this
#: mode depends on.
_SOFTWARE_FLAGS: tuple[str, ...] = (
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-gpu-rasterization",
)

#: What ``configure_rendering`` last put in force, for the settings page to
#: compare the stored value against - a change only takes effect on restart.
_active: RenderMode = RenderMode.AUTO


def flags_for(mode: RenderMode, existing: str = "") -> str:
    """Chromium's command line for ``mode``, preserving what was already set.

    A deployment may already point ``QTWEBENGINE_CHROMIUM_FLAGS`` at something
    of its own - a proxy switch, a certificate exception - so the app's flags
    are appended to that rather than replacing it, and a flag that is already
    present is not added twice.
    """
    present = existing.split()
    additions = [flag for flag in _flags(mode) if flag not in present]
    return " ".join([*present, *additions]).strip()


def _flags(mode: RenderMode) -> tuple[str, ...]:
    return _SOFTWARE_FLAGS if mode is RenderMode.SOFTWARE else ()


def configure_rendering(mode: RenderMode) -> RenderMode:
    """Apply ``mode`` to the environment and to Qt. Call before ``QApplication``.

    Returns the mode now in force, which is also what :func:`active_render_mode`
    will report for the rest of the process.
    """
    global _active

    merged = flags_for(mode, os.environ.get(CHROMIUM_FLAGS_VAR, ""))
    if merged:
        os.environ[CHROMIUM_FLAGS_VAR] = merged

    if mode is RenderMode.SOFTWARE:
        QCoreApplication.setAttribute(
            Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True
        )

    _active = mode
    _log.info(
        "Rendering: mode=%s chromium_flags=%s",
        mode.value,
        os.environ.get(CHROMIUM_FLAGS_VAR, "<none>"),
    )
    return mode


def active_render_mode() -> RenderMode:
    """The mode this process actually started with."""
    return _active
