"""`notes sync`: one-way copy of the Notein backup from the rclone remote.

Runs `rclone copy <remote> <data root>/NoteInDataSync --progress`. `copy` never deletes
anything at the destination and never writes to the remote. rclone's output goes straight
to the terminal. The destination folder is not created here: rclone creates it.
"""

from __future__ import annotations

import shutil
import subprocess

from notes_cli.config import Config


class ToolMissing(Exception):
    """An external program the command needs is not on PATH."""

    def __init__(self, program: str):
        super().__init__(f"{program} is not installed or not on PATH")
        self.program = program


def sync(config: Config) -> int:
    """Run rclone and return its exit code. Raises ToolMissing("rclone") if it is not on PATH."""
    if shutil.which("rclone") is None:
        raise ToolMissing("rclone")
    argv = ["rclone", "copy", config.remote, str(config.sync_dir), "--progress"]
    return subprocess.run(argv, check=False).returncode
