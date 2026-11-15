import shutil
import subprocess

import pytest

from notes_cli.cli import main
from notes_cli.config import load_config
from notes_cli.convert import MANIFEST_NAME
from notes_cli.sync import ToolMissing, sync

from factory import make_folder_zip, make_note_zip

MISSING_RCLONE = "notes: rclone is not installed or not on PATH\n"


def fake_rclone(monkeypatch, code: int = 0, installed: bool = True) -> list:
    """Patch `shutil.which` and `subprocess.run`; returns the list of recorded calls."""
    calls: list = []
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if installed else None)

    def run(argv, *args, **kwargs):
        calls.append((argv, args, kwargs))
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def lgt_note(sync_dir, title: str, id: str) -> None:
    sync_dir.mkdir(parents=True, exist_ok=True)
    parent = None
    path = ("Uni CS", "Year 1", "Semester 2", "DBA", "DBA LGT Notes")
    for depth, name in enumerate(path, 1):
        folder_id = "f" + str(depth)
        if not any(p.name.endswith("_" + folder_id) for p in sync_dir.iterdir()):
            make_folder_zip(sync_dir, name, folder_id, parent)
        parent = folder_id
    make_note_zip(sync_dir, title, id, parent)


def test_sync_builds_rclone_command(cfg, monkeypatch):
    calls = fake_rclone(monkeypatch)

    assert sync(cfg) == 0

    assert len(calls) == 1
    argv, args, kwargs = calls[0]
    assert argv == ["rclone", "copy", "my-drive:NoteInDataSync",
                    str(cfg.data_root / "NoteInDataSync"), "--progress"]
    assert args == ()
    assert not {"sync", "move", "moveto", "delete", "deletefile", "purge", "rmdirs"} & set(argv)
    assert not any(a.startswith("--delete") for a in argv)
    assert not kwargs.get("shell")
    # rclone's output streams straight to the terminal.
    assert not kwargs.get("capture_output")
    assert kwargs.get("stdout") is None and kwargs.get("stderr") is None
    # rclone creates the destination itself; the tool does not.
    assert not cfg.sync_dir.exists()


def test_sync_returns_rclone_exit_code(cfg, monkeypatch):
    fake_rclone(monkeypatch, code=7)

    assert sync(cfg) == 7


def test_sync_missing_rclone(cfg, monkeypatch):
    calls = fake_rclone(monkeypatch, installed=False)

    with pytest.raises(ToolMissing) as info:
        sync(cfg)

    assert info.value.program == "rclone"
    assert str(info.value) == "rclone is not installed or not on PATH"
    assert calls == []


def test_cli_sync_missing_rclone_exits_1_one_line(cfg_dir, capsys, monkeypatch):
    fake_rclone(monkeypatch, installed=False)

    assert main(["--data", str(cfg_dir), "sync"]) == 1
    out, err = capsys.readouterr()

    assert err == MISSING_RCLONE
    assert out == ""


def test_cli_sync_returns_rclone_code(cfg_dir, capsys, monkeypatch):
    fake_rclone(monkeypatch, code=3)

    assert main(["--data", str(cfg_dir), "sync"]) == 3
    err = capsys.readouterr().err
    assert "rclone" in err and "3" in err
    assert len(err.strip().splitlines()) == 1


def test_cli_all_stops_if_sync_fails(cfg_dir, capsys, monkeypatch):
    lgt_note(cfg_dir / "NoteInDataSync", "W1", "n-1")
    calls = fake_rclone(monkeypatch, code=3)

    assert main(["--data", str(cfg_dir), "all"]) == 3

    assert len(calls) == 1
    out = capsys.readouterr().out
    assert out == ""  # organise and convert did not run
    assert not (cfg_dir / "CS Degree").exists()
    assert not (cfg_dir / MANIFEST_NAME).exists()


def test_cli_all_missing_rclone(cfg_dir, capsys, monkeypatch):
    lgt_note(cfg_dir / "NoteInDataSync", "W1", "n-1")
    fake_rclone(monkeypatch, installed=False)

    assert main(["--data", str(cfg_dir), "all"]) == 1

    out, err = capsys.readouterr()
    assert err == MISSING_RCLONE and out == ""
    assert not (cfg_dir / "CS Degree").exists()


def test_cli_all_runs_sync_organise_convert(cfg_dir, capsys, monkeypatch):
    lgt_note(cfg_dir / "NoteInDataSync", "W1", "n-1")
    calls = fake_rclone(monkeypatch)

    assert main(["--data", str(cfg_dir), "all"]) == 0

    out = capsys.readouterr().out
    assert len(calls) == 1
    cfg = load_config(cfg_dir)
    assert (cfg.module_dir("PAR") / "SGT").is_dir()  # organise made every module's folders
    assert (cfg.module_dir("DBA") / "LGT" / "W1.pdf").is_file()
    organise_at = out.index("W1 -> CS Degree/Year 1/DBA/LGT/W1.pdf")
    convert_at = out.index("1 converted, 0 unchanged")
    assert organise_at < convert_at

    # Again: nothing to convert.
    assert main(["--data", str(cfg_dir), "all"]) == 0
    assert "0 converted, 1 unchanged" in capsys.readouterr().out


def test_cli_all_missing_backup_same_as_organise(cfg_dir, capsys, monkeypatch):
    fake_rclone(monkeypatch)  # "succeeds" without creating anything
    assert main(["--data", str(cfg_dir), "organise"]) == 1
    organise_err = capsys.readouterr().err

    assert main(["--data", str(cfg_dir), "all"]) == 1

    assert capsys.readouterr().err == organise_err
