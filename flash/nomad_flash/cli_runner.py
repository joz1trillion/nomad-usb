"""Scripted-flash entry point (no browser, no UI)."""

from __future__ import annotations

from rich.console import Console

console = Console()


def run_cli_flash(*, iso_path: str, device: str,
                  archive_path: str | None, no_cache: bool,
                  apps: list[str], skip_confirm: bool) -> int:
    """Run a fully-scripted flash. Returns process exit code.

    Stub — wires up to the real flash pipeline once it exists.
    """
    console.print("[yellow]CLI flash mode not yet implemented.[/yellow]")
    console.print(f"  ISO:           {iso_path}")
    console.print(f"  Device:        {device}")
    console.print(f"  Archive:       {archive_path or '(pull from registry)'}")
    console.print(f"  No-cache:      {no_cache}")
    console.print(f"  Optional apps: {apps or '(none)'}")
    console.print(f"  Skip confirm:  {skip_confirm}")
    return 1
