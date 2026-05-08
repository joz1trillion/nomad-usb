"""Filesystem browser for the file picker in advanced options.

The flash tool's wizard has two advanced fields where users can point
at a local file instead of downloading:
  - Local ISO override
  - Local docker tarball override

Rather than making them type a path, we expose a simple filesystem
browser via /api/picker/browse. The frontend renders this as a modal
that lets them navigate folders and pick a file.

We don't use zenity (the GTK file dialog) anymore. Earlier versions
did, but it was finicky on different desktops, hard to position
correctly relative to the browser, and required a desktop session.
The in-app browser works the same in any browser, on any host
(including headless ones via SSH tunnel).

Filtering: we list directories always, and files matching the caller-
provided extension hint. If the caller doesn't pass one, all files
show but only files with allowed extensions are selectable.
"""

from __future__ import annotations

import os
from pathlib import Path


# Default starting directory if none provided. Most users keep
# downloads here. Falling back to home if Downloads doesn't exist
# is fine — they can navigate from there.
def _default_start_dir() -> Path:
    home = Path.home()
    candidates = [home / "Downloads", home]
    for c in candidates:
        if c.is_dir():
            return c
    return Path("/")


def list_directory_dict(path: str | None,
                        allowed_exts: list[str] | None = None) -> dict:
    """List a directory's contents for the in-app file browser.

    Returns a dict with:
      cwd:     resolved absolute path
      parent:  the parent dir's path, or None if we're at /
      entries: list of {name, path, is_dir, is_match, size}

    `allowed_exts` is a list like ['.iso'] or ['.gz', '.xz', '.tar'].
    Entries matching one of these are flagged with is_match=True so
    the frontend can style them as selectable. is_dir entries are
    always navigable regardless of allowed_exts.

    Hidden entries (.foo) and broken symlinks are skipped.
    """
    target = Path(path).expanduser().resolve() if path else _default_start_dir()

    # Don't let callers escape into anything ridiculous via .. or
    # symlinks — but we DO let them navigate to absolute paths since
    # that's the whole point of the picker. The 'safety' here is
    # purely about not crashing on weird paths.
    if not target.is_dir():
        # Fall back to default rather than error — gentler UX when
        # state from a previous session points at a deleted folder.
        target = _default_start_dir()

    entries = []
    try:
        for child in sorted(target.iterdir(),
                            key=lambda p: (not p.is_dir(), p.name.lower())):
            # Skip hidden files. They're rarely what the user wants
            # and they clutter the list with .git, .cache, etc.
            if child.name.startswith("."):
                continue

            try:
                # is_dir() raises on broken symlinks; treat those as
                # files so they at least show up but aren't clickable.
                is_dir = child.is_dir()
                size = 0 if is_dir else child.stat().st_size
            except (OSError, PermissionError):
                continue

            # Match check: any allowed extension matches the suffix(es)?
            is_match = False
            if allowed_exts:
                # Multi-suffix files like "foo.tar.xz" — match against
                # the joined trailing suffixes too.
                suffixes = "".join(child.suffixes).lower()
                last = child.suffix.lower()
                for ext in allowed_exts:
                    e = ext.lower()
                    if e == last or suffixes.endswith(e):
                        is_match = True
                        break

            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": is_dir,
                "is_match": is_match,
                "size": size,
            })
    except PermissionError:
        # Can't list this dir — return empty entries with a message
        # the frontend can render. We still set cwd/parent so the
        # back navigation works.
        entries = []

    parent = str(target.parent) if target.parent != target else None

    return {
        "cwd": str(target),
        "parent": parent,
        "entries": entries,
    }
