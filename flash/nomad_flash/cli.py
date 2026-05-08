"""Top-level CLI for nomad-flash.

Subcommands:
    web              start the web UI (default if no subcommand given)
    cli              fully-scripted flash, no browser
    list-versions    show available releases on GitHub
    clear-cache      remove cached downloads

Each subcommand is a thin shim — the actual logic lives in the
modules they delegate to.
"""

import sys
import click

from . import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """nomad-flash — write a Nomad ISO + data partition to a USB drive."""
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
    from .web import run_server
    run_server(host=host, port=port, open_browser=not no_browser)


@main.command()
@click.option("--device", required=True,
              help="Target block device (e.g. /dev/sdb).")
@click.option("--mode", type=click.Choice(["full", "base"]), default="full",
              show_default=True,
              help="full: prebuilt docker (fast first boot, works offline). "
                   "base: no docker images (first boot pulls from internet).")
@click.option("--version", default=None,
              help="Release tag to use (e.g. v0.6.0). Defaults to latest.")
@click.option("--iso", "iso_path", default=None, type=click.Path(exists=True),
              help="Use a local ISO file instead of downloading.")
@click.option("--docker-tar", "prebuilt_docker_path", default=None,
              type=click.Path(exists=True),
              help="Use a local prebuilt docker tarball instead of downloading. "
                   "Only meaningful in --mode=full.")
@click.option("--yes", is_flag=True,
              help="Skip confirmation prompt. DESTRUCTIVE — be sure.")
def cli(device: str, mode: str, version: str | None,
        iso_path: str | None, prebuilt_docker_path: str | None,
        yes: bool) -> None:
    """Fully-scripted flash, no browser."""
    from .cli_runner import run_cli_flash
    sys.exit(run_cli_flash(
        device=device,
        flash_mode=mode,
        version=version,
        iso_path=iso_path,
        prebuilt_docker_path=prebuilt_docker_path,
        skip_confirm=yes,
    ))


@main.command("list-versions")
@click.option("--owner", default="joz1trillion", show_default=True)
@click.option("--repo", default="nomad-usb", show_default=True)
def list_versions(owner: str, repo: str) -> None:
    """Show recent releases available for download."""
    from .steps_download import list_releases
    try:
        rels = list_releases(owner, repo)
    except Exception as e:
        click.echo(f"failed to list releases: {e}", err=True)
        sys.exit(1)

    if not rels:
        click.echo("no releases found")
        return

    click.echo(f"Available releases on {owner}/{repo}:")
    for r in rels:
        click.echo(f"  {r['tag']:12s}  {r.get('published_at', '')}  {r['name']}")


@main.command("clear-cache")
@click.option("--version", default=None,
              help="Clear only this version (default: clear everything).")
def clear_cache(version: str | None) -> None:
    """Remove cached ISO and tarball downloads."""
    from .steps_download import clear_cache_for_version, clear_all_cache

    def log(msg: str) -> None:
        click.echo(msg)

    if version:
        clear_cache_for_version(version, log)
    else:
        clear_all_cache(log)


if __name__ == "__main__":
    main()
