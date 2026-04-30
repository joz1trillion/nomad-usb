"""Top-level CLI for nomad-flash.

Three subcommands:
    web              start the web UI (default if no subcommand given)
    cli              fully-scripted flash, no browser
    verify-template  compare bundled compose template against an ISO

Each subcommand is a thin shim — the actual logic lives in the
modules they delegate to. Keeping CLI parsing separate from logic
makes both easier to test.
"""

import sys
import click

from . import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """nomad-flash — write a Nomad ISO + data partition to a USB drive."""
    # `nomad-flash` with no subcommand → launch the web UI.
    # This is the most common case; everything else is power-user.
    if ctx.invoked_subcommand is None:
        ctx.invoke(web)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind address for the web UI server.")
@click.option("--port", default=5050, show_default=True, type=int,
              help="Port for the web UI server.")
@click.option("--no-browser", is_flag=True,
              help="Don't try to auto-open the browser.")
def web(host: str, port: int, no_browser: bool) -> None:
    """Start the web UI (this is the default mode)."""
    # Lazy import so a quick `--help` doesn't pull in the whole stack.
    from .web import run_server
    run_server(host=host, port=port, open_browser=not no_browser)


@main.command()
@click.option("--iso", "iso_path", required=True, type=click.Path(exists=True),
              help="Path to the Nomad ISO to write.")
@click.option("--device", required=True,
              help="Target block device (e.g. /dev/sdb).")
@click.option("--from-archive", "archive_path", default=None,
              type=click.Path(exists=True),
              help="Use a pre-built docker image archive instead of pulling.")
@click.option("--no-cache", is_flag=True,
              help="Force re-pull of all docker images even if cached locally.")
@click.option("--apps", default="",
              help="Comma-separated list of optional app images to bundle.")
@click.option("--yes", is_flag=True,
              help="Skip confirmation prompt. DESTRUCTIVE — be sure.")
def cli(iso_path: str, device: str, archive_path: str | None,
        no_cache: bool, apps: str, yes: bool) -> None:
    """Fully-scripted flash, no browser."""
    from .cli_runner import run_cli_flash
    sys.exit(run_cli_flash(
        iso_path=iso_path,
        device=device,
        archive_path=archive_path,
        no_cache=no_cache,
        apps=[a.strip() for a in apps.split(",") if a.strip()],
        skip_confirm=yes,
    ))


@main.command("verify-template")
@click.option("--iso", "iso_path", required=True, type=click.Path(exists=True),
              help="ISO to compare the bundled compose template against.")
def verify_template(iso_path: str) -> None:
    """Mount an ISO and compare its compose template to ours.

    Useful when flashing a custom-built ISO — confirms the compose
    file we'd write to NOMAD_DATA matches the one baked into the ISO
    at /usr/share/nomad/templates/docker-compose.yml.
    """
    from .verify import verify_template_against_iso
    sys.exit(verify_template_against_iso(iso_path))


if __name__ == "__main__":
    main()
