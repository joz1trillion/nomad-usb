"""Shared helpers for running external commands inside pipeline steps.

Pipeline steps shell out to lots of tools (sgdisk, mkfs.ext4, rsync,
grub-install, ...). They all need:
  - Stream stdout/stderr to the live log as it happens, not buffered
    until completion (so the user sees progress in real time)
  - Raise a clear exception on failure so the orchestrator's error
    handler can wrap it
  - Be findable: tools may live in /usr/sbin instead of /usr/bin and
    won't be on $PATH for the unprivileged user — we resolve via shutil

Centralizing here keeps each step short and readable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Sequence

StepLogger = Callable[[str], None]


class CommandFailed(Exception):
    """A subprocess returned a non-zero exit code.

    The exception message is short — the full stdout/stderr was already
    streamed to the log line-by-line during the run.
    """
    def __init__(self, cmd: Sequence[str], returncode: int):
        self.cmd = list(cmd)
        self.returncode = returncode
        super().__init__(
            f"command exited {returncode}: {' '.join(cmd)}"
        )


def which(tool: str) -> str:
    """Resolve a tool name to a full path, searching common sbin dirs.

    Some partition tools live in /usr/sbin or /sbin which aren't on a
    regular user's $PATH. We probe those explicitly so error messages
    say "command not found: sgdisk" only if it's genuinely missing.
    """
    found = shutil.which(tool)
    if found:
        return found
    for sbin in ("/usr/sbin", "/sbin", "/usr/local/sbin"):
        candidate = os.path.join(sbin, tool)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"required tool not found: {tool}")


def run(cmd: Sequence[str], log: StepLogger, *,
        check: bool = True, prefix: str = "  > ") -> int:
    """Run a command, streaming combined stdout+stderr to `log`.

    Each output line is prefixed with `prefix` so it's visually distinct
    from the step's own log messages.

    If check=True (default), raises CommandFailed on non-zero exit.
    Returns the exit code.
    """
    log(f"$ {' '.join(cmd)}")

    # bufsize=1 + text=True forces line buffering so we see output as
    # it happens rather than only when the process exits.
    # stderr=STDOUT folds error output into the same stream so we don't
    # need to read two pipes (which complicates ordering).
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None  # for the type checker
    for line in proc.stdout:
        # Only strip the trailing newline — preserve leading whitespace
        # because some tools (rsync, grub) use indentation for structure.
        log(prefix + line.rstrip("\n"))

    rc = proc.wait()
    if check and rc != 0:
        raise CommandFailed(cmd, rc)
    return rc
