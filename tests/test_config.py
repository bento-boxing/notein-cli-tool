import pytest

from notes_cli.config import ConfigError, UnknownModule, load_config

from conftest import NOTES_TOML


def _write(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.toml").write_text(NOTES_TOML, encoding="utf-8")
    return root


def test_load_config_from_argument(tmp_path):
    _write(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.data_root == tmp_path
    assert cfg.modules["DBA"] == 1
    assert cfg.module_dir("FM2") == tmp_path / "CS Degree" / "Year 2" / "FM2"
    assert cfg.remote == "my-drive:NoteInDataSync"
    assert cfg.tree_root == "Uni CS"
    assert cfg.subfolder_map["lecture notes"] == "Lecture Notes"
    assert cfg.sync_dir == tmp_path / "NoteInDataSync"
    assert cfg.degree_dir == tmp_path / "CS Degree"


def test_env_var_used_when_no_argument(tmp_path, monkeypatch):
    root = _write(tmp_path / "elsewhere")
    monkeypatch.setenv("NOTES_DATA", str(root))
    monkeypatch.chdir(tmp_path)
    assert load_config().data_root == root


def test_found_by_walking_up(tmp_path, monkeypatch):
    root = _write(tmp_path / "data")
    cwd = root / "CS Degree"
    cwd.mkdir()
    monkeypatch.delenv("NOTES_DATA", raising=False)
    monkeypatch.chdir(cwd)
    assert load_config().data_root == root


def test_found_via_data_subfolder(tmp_path, monkeypatch):
    root = _write(tmp_path / "data")
    repo = tmp_path / "notes-cli" / "src"
    repo.mkdir(parents=True)
    monkeypatch.delenv("NOTES_DATA", raising=False)
    monkeypatch.chdir(repo)
    assert load_config().data_root == root


def test_missing_config_raises(tmp_path, monkeypatch):
    # The search walks up from the current folder. When the temp folder lives inside the
    # project (TMPDIR=notes-cli/.tmp), the real data/notes.toml is an ancestor's, so look
    # for a file name that exists nowhere.
    import notes_cli.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_NAME", "notes-absent-for-test.toml")
    monkeypatch.delenv("NOTES_DATA", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        load_config(tmp_path)
    with pytest.raises(ConfigError):
        load_config()


def test_resolve_module_case_insensitive(cfg):
    assert cfg.resolve_module("dba") == "DBA"
    assert cfg.resolve_module(" Fm2 ") == "FM2"


def test_unknown_module_lists_valid(cfg):
    with pytest.raises(UnknownModule) as e:
        cfg.resolve_module("XYZ")
    assert "PRG" in str(e.value)
    assert "FM2" in str(e.value)


@pytest.mark.parametrize(
    "line",
    [
        '"lab" = "/elsewhere"',
        '"lab" = "../../x"',
        '"lab" = "a/b"',
        '"lab" = ".."',
        '"lab" = ""',
        '"lab" = "Lecture-Slides"',
        '"lab" = "flashcards"',
        '"lab" = "Practice Exams"',
    ],
)
def test_unsafe_subfolder_destination_rejected(tmp_path, line):
    from conftest import NOTES_TOML

    (tmp_path / "notes.toml").write_text(NOTES_TOML + line + "\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_unsafe_module_name_rejected(tmp_path):
    from conftest import NOTES_TOML

    text = NOTES_TOML.replace("PAR = 2", '"../PAR" = 2')
    (tmp_path / "notes.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_extra_subfolder_destination_allowed(tmp_path):
    from conftest import NOTES_TOML

    (tmp_path / "notes.toml").write_text(NOTES_TOML + '"lab" = "Labs"\n', encoding="utf-8")
    assert load_config(tmp_path).subfolder_map["lab"] == "Labs"


@pytest.mark.parametrize("remote", ["--delete-excluded", "-v", "", "  "])
def test_remote_that_looks_like_an_option_rejected(tmp_path, remote):
    from conftest import NOTES_TOML

    text = NOTES_TOML.replace('"my-drive:NoteInDataSync"', f'"{remote}"')
    (tmp_path / "notes.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_model_defaults_to_none(tmp_path):
    _write(tmp_path)
    assert load_config(tmp_path).model is None


@pytest.mark.parametrize("model", ["sonnet", "opus", "claude-sonnet-5-5", "sonnet[1m]"])
def test_model_read_from_config(tmp_path, model):
    (tmp_path / "notes.toml").write_text(f'model = "{model}"\n' + NOTES_TOML, encoding="utf-8")
    assert load_config(tmp_path).model == model


@pytest.mark.parametrize("value", ['""', '"--dangerously-skip-permissions"', '"a b"', "3", '"x;y"'])
def test_bad_model_rejected(tmp_path, value):
    (tmp_path / "notes.toml").write_text(f"model = {value}\n" + NOTES_TOML, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)
