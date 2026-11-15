"""Load the user config (`notes.toml` in the data root)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "notes.toml"
ENV_VAR = "NOTES_DATA"


class UnknownModule(Exception):
    """A module name that is not in the config's module list."""


class ConfigError(Exception):
    """The config file is missing or invalid."""


@dataclass(frozen=True)
class Config:
    data_root: Path
    modules: dict[str, int]  # "DBA" -> 1 (year)
    subfolder_map: dict[str, str]  # "lecture notes" -> "Lecture Notes" (keys lower-case)
    remote: str  # "my-drive:NoteInDataSync"
    tree_root: str  # "Uni CS"
    model: str | None = None  # passed to `claude --model`; None = Claude Code's default

    @property
    def sync_dir(self) -> Path:
        return self.data_root / "NoteInDataSync"

    @property
    def degree_dir(self) -> Path:
        return self.data_root / "CS Degree"

    def resolve_module(self, name: str) -> str:
        """Return the canonical module name for `name`, ignoring case."""
        wanted = name.strip().casefold()
        for module in self.modules:
            if module.casefold() == wanted:
                return module
        valid = ", ".join(self.modules)
        raise UnknownModule(f"unknown module {name!r}; valid modules: {valid}")

    def module_dir(self, module: str) -> Path:
        module = self.resolve_module(module)
        return self.degree_dir / f"Year {self.modules[module]}" / module


def _find_data_root(data: Path | None) -> Path:
    if data is not None:
        root = Path(data).expanduser()
        if not (root / CONFIG_NAME).is_file():
            raise ConfigError(f"no {CONFIG_NAME} in {root}")
        return root.resolve()

    env = os.environ.get(ENV_VAR)
    if env:
        root = Path(env).expanduser()
        if not (root / CONFIG_NAME).is_file():
            raise ConfigError(f"no {CONFIG_NAME} in {root} (from ${ENV_VAR})")
        return root.resolve()

    cwd = Path.cwd().resolve()
    for directory in (cwd, *cwd.parents):
        if (directory / CONFIG_NAME).is_file():
            return directory
        if (directory / "data" / CONFIG_NAME).is_file():
            return directory / "data"
    raise ConfigError(
        f"no {CONFIG_NAME} found: pass --data, set ${ENV_VAR}, or run from inside the data folder"
    )


# Folders inside a module that converted notes must never be written into: the first is
# the user's own slides (read-only to the tool), the others belong to Claude sessions.
RESERVED_FOLDERS = {"lecture-slides", "flashcards", "practice exams"}


def _plain_folder_name(name: str) -> bool:
    """True for a single folder name: no separators, not "." or "..", not blank."""
    return (
        name == name.strip()
        and name not in ("", ".", "..")
        and not any(ch in name for ch in "/\\\0")
    )


def load_config(data: Path | None = None) -> Config:
    """Find the data root (--data, $NOTES_DATA, then walking up from cwd) and load its config."""
    root = _find_data_root(data)
    path = root / CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e

    try:
        remote = raw["remote"]
        tree_root = raw["tree_root"]
        modules_raw = raw["modules"]
        subfolders_raw = raw["subfolders"]
    except KeyError as e:
        raise ConfigError(f"{path}: missing key {e.args[0]!r}") from e

    if not isinstance(remote, str) or not isinstance(tree_root, str):
        raise ConfigError(f"{path}: 'remote' and 'tree_root' must be strings")
    if not remote.strip() or remote.startswith("-"):
        # It is passed to rclone as an argument; a leading "-" would be read as an option.
        raise ConfigError(f"{path}: 'remote' must be an rclone remote such as "
                          '"my-drive:NoteInDataSync"')
    if not isinstance(modules_raw, dict) or not isinstance(subfolders_raw, dict):
        raise ConfigError(f"{path}: [modules] and [subfolders] must be tables")

    model = raw.get("model")
    if model is not None:
        # It is passed to claude as an argument; a leading "-" would be read as an option.
        if (not isinstance(model, str) or not model or model.startswith("-")
                or not all(ch.isalnum() or ch in "-._[]:/" for ch in model)):
            raise ConfigError(f"{path}: 'model' must be a Claude model name or alias "
                              'such as "sonnet" or "opus"')

    modules: dict[str, int] = {}
    for name, year in modules_raw.items():
        if not isinstance(year, int) or isinstance(year, bool):
            raise ConfigError(f"{path}: year for module {name!r} must be an integer")
        if not _plain_folder_name(name):
            raise ConfigError(f"{path}: module name {name!r} must be a plain folder name")
        modules[name] = year

    subfolder_map: dict[str, str] = {}
    for key, dest in subfolders_raw.items():
        if not isinstance(dest, str):
            raise ConfigError(f"{path}: subfolder destination for {key!r} must be a string")
        if not _plain_folder_name(dest):
            raise ConfigError(
                f"{path}: subfolder destination {dest!r} for {key!r} must be a plain folder name"
            )
        if dest.casefold() in RESERVED_FOLDERS:
            raise ConfigError(
                f"{path}: subfolder destination {dest!r} for {key!r} is reserved; "
                "generated PDFs may not go into that folder"
            )
        subfolder_map[key.strip().lower()] = dest

    return Config(
        data_root=root,
        modules=modules,
        subfolder_map=subfolder_map,
        remote=remote,
        tree_root=tree_root,
        model=model,
    )
