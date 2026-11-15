"""Shared fixtures for the notes CLI tests."""

import json
from pathlib import Path

import pytest

from notes_cli.config import load_config

NOTES_TOML = """\
remote = "my-drive:NoteInDataSync"
tree_root = "Uni CS"

[modules]
PRG = 1
DBA = 1
DSA = 1
IST = 1
CA1 = 1
ELC = 1
FM1 = 1
SWE = 2
IAS = 2
FM2 = 2
PAR = 2

[subfolders]
"lecture" = "Lecture Notes"
"lecture notes" = "Lecture Notes"
"slides" = "Lecture Notes"
"lgt" = "LGT"
"lgt notes" = "LGT"
"sgt" = "SGT"
"sgt notes" = "SGT"
"""

REAL_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "realdata: needs the real Notein backup in data/NoteInDataSync (skipped when absent)",
    )


@pytest.fixture(autouse=True)
def _never_run_real_tools(monkeypatch):
    """Safety net: no test may start real `rclone` or `claude`, even if a test forgets to
    patch `subprocess.run` / `os.execvp` or the code changes how it starts them."""
    import os
    import subprocess

    forbidden = {"rclone", "claude"}

    def program(argv) -> str:
        first = argv if isinstance(argv, (str, bytes, os.PathLike)) else argv[0]
        return os.path.basename(os.fsdecode(first).split()[0]) if first else ""

    def guard(real):
        def run(argv, *args, **kwargs):
            if program(argv) in forbidden:
                raise AssertionError(f"a test tried to run the real {program(argv)!r}")
            return real(argv, *args, **kwargs)
        return run

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, guard(getattr(subprocess, name)))

    def no_exec(*args, **kwargs):
        raise AssertionError(f"a test tried to replace the process: {args[:2]!r}")

    for name in ("execv", "execve", "execvp", "execvpe", "execl", "execlp"):
        monkeypatch.setattr(os, name, no_exec)

    # The launch path changes into the module folder before exec. A test that reaches it
    # without patching os.chdir must fail, not move the whole test run into a data folder.
    # Only calls made from notes_cli code are refused: pytest's own monkeypatch.chdir (and
    # its undo, which runs after this patch is removed) still work.
    import sys

    real_chdir = os.chdir

    def guarded_chdir(path):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller == "notes_cli" or caller.startswith("notes_cli."):
            raise AssertionError(f"a test let {caller} change the working directory to "
                                 f"{os.fspath(path)!r}; patch os.chdir")
        return real_chdir(path)

    monkeypatch.setattr(os, "chdir", guarded_chdir)


@pytest.fixture
def cfg_dir(tmp_path) -> Path:
    """A temporary data root containing a notes.toml with the standard config."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "notes.toml").write_text(NOTES_TOML, encoding="utf-8")
    return root


@pytest.fixture
def cfg(cfg_dir):
    """The Config loaded from cfg_dir."""
    return load_config(cfg_dir)


@pytest.fixture
def real_data_root() -> Path:
    """The real data root; skips the test when the backup is not present."""
    if not (REAL_DATA_ROOT / "NoteInDataSync").is_dir():
        pytest.skip("real backup data/NoteInDataSync not present")
    return REAL_DATA_ROOT


@pytest.fixture
def real_sync_dir(real_data_root) -> Path:
    """The real (read-only) NoteInDataSync folder."""
    return real_data_root / "NoteInDataSync"


REAL_FACTS_FILE = Path(__file__).resolve().parent / "realdata.local.json"


@pytest.fixture
def real_facts(real_data_root) -> dict:
    """Facts about the user's own backup (note titles, counts) that the real-data tests
    check against. They are kept in `tests/realdata.local.json`, which is not in git, so
    nothing about the real notes is in the repository; without the file these tests skip.
    See `tests/realdata.example.json` for the keys."""
    if not REAL_FACTS_FILE.is_file():
        pytest.skip(f"no {REAL_FACTS_FILE.name}: facts about the real backup are kept out of git")
    return json.loads(REAL_FACTS_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def real_cfg(real_data_root):
    """Config for the real data root (uses data/notes.toml if present)."""
    if not (real_data_root / "notes.toml").is_file():
        pytest.skip("data/notes.toml not present")
    return load_config(real_data_root)
