from notes_cli.cli import main


def test_cli_unknown_module_exits_2(cfg_dir, capsys):
    assert main(["--data", str(cfg_dir), "convert", "XYZ"]) == 2
    err = capsys.readouterr().err
    assert "XYZ" in err
    assert "PRG" in err and "FM2" in err
    assert "Traceback" not in err


def test_cli_help_lists_commands(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in ("sync", "organise", "convert", "all", "flashcards", "exam"):
        assert command in out


def test_cli_missing_config_exits_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NOTES_DATA", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["--data", str(tmp_path), "organise"]) == 2
    err = capsys.readouterr().err
    assert "notes.toml" in err
    assert "Traceback" not in err
