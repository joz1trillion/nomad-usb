"""Verify the bundled compose template against an ISO's copy.

Useful when flashing a custom-built ISO. Mounts the ISO read-only,
extracts /usr/share/nomad/templates/docker-compose.yml from the
squashfs inside it, diffs against our bundled copy.
"""

from __future__ import annotations

from rich.console import Console

console = Console()


def verify_template_against_iso(iso_path: str) -> int:
    """Compare bundled compose template to the one inside ISO_PATH.

    Returns 0 on match, 1 on difference, 2 on error.
    Stub for now.
    """
    console.print(f"[yellow]Verify-template not yet implemented.[/yellow]")
    console.print(f"  Would mount: {iso_path}")
    console.print(f"  Would extract: /usr/share/nomad/templates/docker-compose.yml")
    console.print(f"  Would diff against: bundled/docker-compose.yml")
    return 2
