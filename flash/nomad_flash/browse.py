"""File-picking helpers for the wizard.

Two strategies, in order of preference:

1. Native dialog via `zenity --file-selection`. Real path, real
   experience, only works if zenity is installed and a desktop
   session is available.

2. Built-in directory browser. Talks to the backend to list a
   directory, user clicks through. Works anywhere, no GUI deps.

The frontend probes /api/picker/zenity-available at load time and
shows the appropriate UI affordance — a "Browse" button if zenity
is available, otherwise the in-app browser as the only option.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


# --------------------------------------------------------------------
# Zenity (option 2) — native file picker
# --------------------------------------------------------------------

def zenity_available() -> bool:
    """True if we can pop a native file dialog.

    Two checks: zenity is on PATH, and there's a display server we
    can attach to. Without DISPLAY (or WAYLAND_DISPLAY) zenity will
    silently exit non-zero — better to know up front.
    """
    if shutil.which("zenity") is None:
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def pick_iso_with_zenity(start_dir: str | None = None) -> str | None:
    """Pop a native file picker, return chosen path or None if cancelled.

    `start_dir` sets where the dialog opens — defaults to ~/Downloads,
    falling back to $HOME if that doesn't exist.
    """
    if start_dir is None:
        downloads = Path.home() / "Downloads"
        start_dir = str(downloads if downloads.is_dir() else Path.home())

    # zenity wants a trailing slash on the start dir to mean "open
    # this directory" rather than "select this filename".
    if not start_dir.endswith("/"):
        start_dir += "/"

    cmd = [
        "zenity", "--file-selection",
        "--title=Select Nomad ISO",
        f"--filename={start_dir}",
        "--file-filter=ISO files | *.iso",
        "--file-filter=All files | *",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        # User left the dialog open for 5 minutes — treat as cancel.
        return None
    except FileNotFoundError:
        return None

    # zenity exit codes: 0 = file picked, 1 = cancelled, 5 = timeout
    if result.returncode != 0:
        return None

    path = result.stdout.strip()
    return path or None


# --------------------------------------------------------------------
# Built-in browser (option 3) — directory listing API
# --------------------------------------------------------------------

@dataclass
class BrowseEntry:
    name: str       # display name (just basename)
    path: str       # absolute path
    is_dir: bool    # for icon and click behavior on the frontend
    size: int = 0   # bytes; 0 for directories
    is_iso: bool = False  # true if file ends in .iso (case-insensitive)


@dataclass
class BrowseResult:
    cwd: str                  # the directory we listed
    parent: str | None        # absolute path of the parent, or None at /
    entries: list[BrowseEntry]


def list_directory(path: str | None = None,
                   show_hidden: bool = False) -> BrowseResult:
    """List a directory for the in-app browser.

    Defaults to ~/Downloads. Falls back to $HOME if Downloads doesn't
    exist. Always returns absolute, resolved paths so the frontend
    doesn't have to do path arithmetic.

    Filtering behavior:
      - Directories are always shown (so users can navigate into them)
      - Files are shown but only `.iso` files are flagged with is_iso
      - Hidden items (leading dot) are skipped unless show_hidden=True
    """
    if path is None:
        downloads = Path.home() / "Downloads"
        path = str(downloads if downloads.is_dir() else Path.home())

    target = Path(path).expanduser().resolve()

    # If the user passed a non-existent or non-directory path, fall
    # back to home rather than 500ing the API. Friendlier UX — the
    # in-app browser just opens at home and the user can navigate.
    if not target.is_dir():
        target = Path.home().resolve()

    entries: list[BrowseEntry] = []
    try:
        for child in sorted(target.iterdir(),
                            key=lambda p: (not p.is_dir(), p.name.lower())):
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
                size = 0 if is_dir else child.stat().st_size
            except OSError:
                # Permission denied / broken symlink / race — skip silently.
                continue
            entries.append(BrowseEntry(
                name=child.name,
                path=str(child),
                is_dir=is_dir,
                size=size,
                is_iso=(not is_dir and child.suffix.lower() == ".iso"),
            ))
    except PermissionError:
        # Can't read the directory at all — return an empty list with
        # the cwd set so the UI shows where it tried to go.
        pass

    parent = None
    if target.parent != target:  # not at the filesystem root
        parent = str(target.parent)

    return BrowseResult(cwd=str(target), parent=parent, entries=entries)


def list_directory_dict(path: str | None = None) -> dict:
    """JSON-serializable wrapper around list_directory()."""
    result = list_directory(path)
    return {
        "cwd": result.cwd,
        "parent": result.parent,
        "entries": [asdict(e) for e in result.entries],
    }
