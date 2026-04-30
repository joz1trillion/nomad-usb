"""nomad-flash — flash tool for the Project Nomad survival USB."""

from pathlib import Path


def _read_version() -> str:
    """Read the canonical version from the repo's VERSION file.

    The flash package lives at <repo>/flash/nomad_flash/, and VERSION
    is at the repo root, so we walk up two directories to find it.
    Falling back to "unknown" if the file's missing keeps the tool
    working in odd installs (e.g. someone vendored just the package).
    """
    here = Path(__file__).resolve()
    # here = .../flash/nomad_flash/__init__.py
    # parent.parent.parent = repo root
    candidate = here.parent.parent.parent / "VERSION"
    if candidate.is_file():
        return candidate.read_text().strip()
    return "unknown"


__version__ = _read_version()
