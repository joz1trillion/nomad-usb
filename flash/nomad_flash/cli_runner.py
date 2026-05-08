"""Scripted-flash entry point (no browser, no UI)."""

from __future__ import annotations

import os

from rich.console import Console

from .flash_pipeline import FlashConfig, build_pipeline, run_pipeline


console = Console()


def _confirm_destructive(device: str) -> bool:
    """Make the user type the device name to confirm.

    A simple y/n is too easy to fat-finger when you're about to wipe
    a 64GB drive. Typing the device path forces them to look at it
    once more before committing.
    """
    console.print()
    console.print(f"[bold red]ABOUT TO WIPE {device}[/bold red]")
    console.print("[red]All data on this device will be lost.[/red]")
    console.print()
    console.print(f"To confirm, type the device path exactly: [bold]{device}[/bold]")
    try:
        typed = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("[yellow]aborted[/yellow]")
        return False

    if typed != device:
        console.print(f"[yellow]got {typed!r}, expected {device!r} — aborting[/yellow]")
        return False
    return True


def run_cli_flash(*, device: str, flash_mode: str, version: str | None,
                  iso_path: str | None,
                  prebuilt_docker_path: str | None,
                  skip_confirm: bool) -> int:
    """Run a fully-scripted flash. Returns process exit code."""
    if os.geteuid() != 0:
        console.print("[red]error:[/red] nomad-flash cli must be run as root")
        console.print("       try: sudo nomad-flash cli ...")
        return 2

    cfg = FlashConfig(
        device=device,
        flash_mode=flash_mode,
        version=version,
        iso_path=iso_path,
        prebuilt_docker_path=prebuilt_docker_path,
    )

    pipeline = build_pipeline(cfg)

    console.print()
    console.print("[bold]Flash plan:[/bold]")
    console.print(f"  Device:        {device}")
    console.print(f"  Mode:          {flash_mode}")
    console.print(f"  Version:       {version or 'latest'}")
    if iso_path:
        console.print(f"  Local ISO:     {iso_path}")
    if prebuilt_docker_path:
        console.print(f"  Local docker:  {prebuilt_docker_path}")
    console.print()
    console.print("[bold]Steps:[/bold]")
    for i, step in enumerate(pipeline, 1):
        console.print(f"  {i}. {step.name}")
    console.print()

    if not skip_confirm:
        if not _confirm_destructive(device):
            return 1

    def log(msg: str) -> None:
        if msg.startswith("::step::"):
            try:
                _, _, rest = msg.partition("::step::")
                num_part, _, name = rest.partition("::")
                console.print(f"\n[bold cyan]── Step {num_part}: {name} ──[/bold cyan]")
            except Exception:
                console.print(msg)
        elif msg.startswith("::error::"):
            console.print(f"[red]{msg.replace('::error::', '✗ ')}[/red]")
        elif msg.startswith("::done::"):
            console.print("[bold green]✓ Done[/bold green]")
        elif msg == "":
            console.print()
        else:
            console.print(msg)

    try:
        run_pipeline(cfg, log, pipeline=pipeline)
        return 0
    except Exception as e:
        console.print(f"\n[red]Flash failed:[/red] {e}")
        return 1
