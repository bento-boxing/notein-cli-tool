"""Scan a Notein backup folder (`NoteInDataSync`) into notes, folders and bad entries.

Every entry in the backup is a zip named `<title>_<uuid>` with no extension:

- a note holds `note_meta.json` (plus its database, which is not read here);
- a folder holds `folder_<title>` (JSON), `folder_labels.json` and a PNG `folder_tn_<id>`;
- a template holds only `template_*` members and is ignored.

The scan only reads the central directory and one small JSON member per zip. It never
extracts databases and never writes anything into the backup folder.
"""

from __future__ import annotations

import json
import os
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

NOTE_META = "note_meta.json"
FOLDER_PREFIX = "folder_"
FOLDER_LABELS = "folder_labels.json"
FOLDER_THUMB_PREFIX = "folder_tn_"
TEMPLATE_PREFIX = "template_"


@dataclass(frozen=True)
class FolderEntry:
    id: str
    title: str  # as stored, trailing spaces kept
    parent_id: str | None


@dataclass(frozen=True)
class NoteEntry:
    id: str
    title: str
    parent_id: str | None
    modified: int  # lastModificationTime (ms)
    in_trash: bool
    zip_path: Path


@dataclass(frozen=True)
class BadEntry:
    path: Path
    reason: str


@dataclass
class Backup:
    notes: list[NoteEntry] = field(default_factory=list)
    folders: dict[str, FolderEntry] = field(default_factory=dict)
    bad: list[BadEntry] = field(default_factory=list)

    def folder_path(self, note: NoteEntry) -> tuple[str, ...]:
        """Titles of the note's folders, root first, each stripped.

        Stops at the first parent id that is not in the backup (giving the path of the
        known ancestors) and at the first folder already visited (a parent cycle).
        """
        titles: list[str] = []
        seen: set[str] = set()
        folder_id = note.parent_id
        while folder_id is not None and folder_id not in seen:
            folder = self.folders.get(folder_id)
            if folder is None:
                break
            seen.add(folder_id)
            titles.append(folder.title.strip())
            folder_id = folder.parent_id
        return tuple(reversed(titles))


class BackupUnreadable(Exception):
    """The backup folder itself is missing or cannot be listed. The message is one line,
    ready to show the user (`organise`, `convert` and `all` all show it the same way)."""


def read_backup(sync_dir: Path) -> Backup:
    """`scan(sync_dir)`, with a missing or unreadable backup folder raised as
    `BackupUnreadable` instead of an OSError."""
    try:
        return scan(sync_dir)
    except FileNotFoundError as e:
        raise BackupUnreadable(f"backup folder {sync_dir} not found; run 'notes sync' first") from e
    except NotADirectoryError as e:
        raise BackupUnreadable(f"backup folder {sync_dir} is not a folder") from e
    except OSError as e:
        raise BackupUnreadable(f"cannot read backup folder {sync_dir}: {e.strerror or e}") from e


class _Bad(Exception):
    """An entry that is not a readable note or folder; the message is the reason."""


class _Ignored(Exception):
    """An entry that is skipped silently (a template)."""


def scan(sync_dir: Path) -> Backup:
    """Read every entry in `sync_dir` (sorted by name) into a `Backup`.

    Subdirectories and template zips are ignored. Anything else that is not a readable
    note or folder becomes a `BadEntry` and the scan continues.
    """
    sync_dir = Path(sync_dir)
    backup = Backup()
    # (kind, id) -> (age key, file name, entry, path). Renaming a note or folder in Notein
    # leaves both `<old title>_<id>` and `<new title>_<id>` behind, because the sync never
    # deletes; the most recently modified copy is the current one.
    kept: dict[tuple[str, str], tuple[tuple[int, float, str], str, NoteEntry | FolderEntry, Path]] = {}

    for name in sorted(os.listdir(sync_dir)):
        path = sync_dir / name
        if path.is_dir():
            continue
        try:
            entry, modified = _read_entry(path)
        except _Ignored:
            continue
        except _Bad as e:
            backup.bad.append(BadEntry(path, str(e)))
            continue

        kind = "note" if isinstance(entry, NoteEntry) else "folder"
        age = (modified, _mtime(path), name)
        previous = kept.get((kind, entry.id))
        if previous is None:
            kept[(kind, entry.id)] = (age, name, entry, path)
            continue
        if age > previous[0]:
            kept[(kind, entry.id)] = (age, name, entry, path)
            stale_path, current_name = previous[3], name
        else:
            stale_path, current_name = path, previous[1]
        backup.bad.append(
            BadEntry(stale_path, f"older copy of {kind} {entry.id} (newer copy: {current_name})")
        )

    for _, _, entry, _ in sorted(kept.values(), key=lambda item: item[1]):
        if isinstance(entry, NoteEntry):
            backup.notes.append(entry)
        else:
            backup.folders[entry.id] = entry
    return backup


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_entry(path: Path) -> tuple[NoteEntry | FolderEntry, int]:
    """The note or folder in the zip at `path`, with its `lastModificationTime` (0 if none)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if NOTE_META in names:
                note = _note_from_meta(_read_json(zf, NOTE_META), path)
                return note, note.modified
            folder = _read_folder(zf, names)
            if folder is not None:
                return folder
            if any(n.startswith(TEMPLATE_PREFIX) for n in names):
                raise _Ignored
            raise _Bad(f"zip has neither {NOTE_META} nor a folder_<title> member")
    except (_Bad, _Ignored):
        raise
    except zipfile.BadZipFile as e:
        raise _Bad(f"not a readable zip (corrupt or incomplete download?): {e}") from e
    except (OSError, EOFError, zlib.error, RuntimeError, NotImplementedError) as e:
        # RuntimeError: encrypted member; NotImplementedError: unsupported compression.
        raise _Bad(f"cannot read zip: {e}") from e
    except Exception as e:
        # Anything else a damaged zip can provoke (e.g. UnicodeDecodeError from a corrupt
        # member name). One bad entry must never stop the scan.
        raise _Bad(f"cannot read zip: {type(e).__name__}: {e}") from e


def _read_json(zf: zipfile.ZipFile, member: str) -> object:
    data = zf.read(member)
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise _Bad(f"{member} is not valid JSON: {e}") from e


def _read_folder(zf: zipfile.ZipFile, names: list[str]) -> tuple[FolderEntry, int] | None:
    """The folder described by the zip's `folder_<title>` member, with its
    `lastModificationTime` (0 if absent), or None if there is no such member.

    `folder_labels.json` (a JSON list) and `folder_tn_<id>` (a PNG) share the prefix, so
    candidates are tried with thumbnails last and the first one holding a JSON object wins.
    """
    candidates = [n for n in names if n.startswith(FOLDER_PREFIX) and n != FOLDER_LABELS]
    candidates.sort(key=lambda n: n.startswith(FOLDER_THUMB_PREFIX))  # stable: thumbnails last
    first_error: _Bad | None = None
    for member in candidates:
        try:
            meta = _read_json(zf, member)
        except _Bad as e:
            if not member.startswith(FOLDER_THUMB_PREFIX):
                first_error = first_error or e
            continue
        if isinstance(meta, dict):
            folder = FolderEntry(
                id=_required_str(meta, "id", member),
                title=_required_str(meta, "title", member),
                parent_id=_parent(meta, member),
            )
            modified = meta.get("lastModificationTime")
            if not isinstance(modified, int) or isinstance(modified, bool):
                modified = 0
            return folder, modified
    if first_error is not None:
        raise first_error  # a folder_<title> member that is not JSON
    return None


def _note_from_meta(meta: object, path: Path) -> NoteEntry:
    if not isinstance(meta, dict):
        raise _Bad(f"{NOTE_META} is not a JSON object")
    modified = meta.get("lastModificationTime")
    if not isinstance(modified, int) or isinstance(modified, bool):
        raise _Bad(f"{NOTE_META} has no integer 'lastModificationTime'")
    in_trash = meta.get("inTrashBin", False)
    if not isinstance(in_trash, bool):
        raise _Bad(f"{NOTE_META} has a non-boolean 'inTrashBin'")
    return NoteEntry(
        id=_required_str(meta, "id", NOTE_META),
        title=_required_str(meta, "title", NOTE_META),
        parent_id=_parent(meta, NOTE_META),
        modified=modified,
        in_trash=in_trash,
        zip_path=path,
    )


def _required_str(meta: dict, key: str, member: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or (key == "id" and not value):
        raise _Bad(f"{member} has no {key!r}")
    return value


def _parent(meta: dict, member: str) -> str | None:
    """`parentId`, with an absent key (as in the real backup), null or "" meaning the root."""
    value = meta.get("parentId")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _Bad(f"{member} has a non-string 'parentId'")
    return value
