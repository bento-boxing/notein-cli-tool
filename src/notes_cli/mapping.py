"""Decide where each note goes: a PDF path under `CS Degree`, or a skip reason.

Mapping rule (spec, "Mapping rule"): a note is mapped when its Notein folder path is
`<tree_root>/Year N/[Semester M/]MODULE/SUBFOLDER`, MODULE is a configured module of year
N, and SUBFOLDER, with the module prefix and surrounding whitespace removed, is a key of
`config.subfolder_map`. Every other note gets exactly one of the four fixed reasons below.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from notes_cli.backup import Backup, NoteEntry
from notes_cli.config import Config

REASON_TRASH = "in trash"
REASON_OUTSIDE = "outside degree tree"
REASON_UNCONFIGURED = "module not configured"
REASON_NOT_SUBFOLDER = "not in a lecture/LGT/SGT folder"

SHORT_ID = 8  # characters of the note id used to tell apart notes that share a file name
MAX_NAME_BYTES = 200  # leaves room for " (<36-char id>).pdf" within the usual 255-byte limit

_YEAR = re.compile(r"year\s*(\d+)", re.IGNORECASE)
_SEMESTER = re.compile(r"semester\s*\d+", re.IGNORECASE)
# Path separators, ":" (macOS/Windows), and the other characters Windows and exFAT/FAT
# disks refuse, so the PDFs can be copied to a USB stick or synced elsewhere unchanged.
_UNSAFE = set('/\\:*?"<>|')


@dataclass(frozen=True)
class Placement:
    note: NoteEntry
    dest: Path | None  # the PDF path, when mapped
    reason: str | None  # why it is skipped, when not mapped; exactly one of dest/reason is set


def safe_name(title: str) -> str:
    """A file name (without extension) for a note title.

    Stripped; path separators, ":", other characters Windows forbids and control
    characters become "-"; leading dots (and spaces they expose) are removed, so the file
    is never hidden and never "." or ".."; long titles are cut to MAX_NAME_BYTES of UTF-8;
    an empty result becomes "untitled".
    """
    name = "".join(
        # Cc: control characters; Cs: lone surrogates, which cannot be encoded in a file name.
        "-" if ch in _UNSAFE or unicodedata.category(ch) in ("Cc", "Cs") else ch
        for ch in title.strip()
    )
    name = name.lstrip(". ")
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_NAME_BYTES:
        name = encoded[:MAX_NAME_BYTES].decode("utf-8", errors="ignore")
    name = name.strip()
    return name or "untitled"


def plan(backup: Backup, config: Config) -> list[Placement]:
    """One placement per note in `backup.notes`, in the same order.

    Notes whose file names would clash in one folder (equal ignoring case, since the PDFs
    may end up on a case-insensitive disk) all get the note id as a suffix:
    `<name> (<first 8 chars of id>).pdf`, or the whole id if that still clashes.
    """
    targets: dict[str, tuple[Path, str]] = {}  # note id -> (folder, safe name)
    reasons: dict[str, str] = {}
    for note in backup.notes:
        result = _classify(backup.folder_path(note), note, config)
        if isinstance(result, str):
            reasons[note.id] = result
        else:
            targets[note.id] = (result, safe_name(note.title))

    names = _file_names(targets)
    placements = []
    for note in backup.notes:
        if note.id in reasons:
            placements.append(Placement(note, None, reasons[note.id]))
        else:
            folder = targets[note.id][0]
            placements.append(Placement(note, folder / names[note.id], None))
    return placements


def _classify(path: tuple[str, ...], note: NoteEntry, config: Config) -> Path | str:
    """The destination folder for a note at folder `path`, or the reason it is skipped."""
    if note.in_trash:
        return REASON_TRASH
    if len(path) < 2 or path[0] != config.tree_root.strip():
        return REASON_OUTSIDE
    year = _YEAR.fullmatch(path[1])
    if year is None:
        return REASON_OUTSIDE
    rest = list(path[2:])
    if rest and _SEMESTER.fullmatch(rest[0]):
        rest.pop(0)
    if not rest:
        return REASON_NOT_SUBFOLDER  # directly in a year or semester folder
    module = _configured_module(rest[0], config)
    if module is None or config.modules[module] != int(year.group(1)):
        return REASON_UNCONFIGURED
    if len(rest) != 2:
        return REASON_NOT_SUBFOLDER  # directly in the module folder, or nested deeper
    mapped = config.subfolder_map.get(_subfolder_key(rest[1], rest[0]))
    if mapped is None:
        return REASON_NOT_SUBFOLDER
    return config.module_dir(module) / mapped


def _configured_module(folder: str, config: Config) -> str | None:
    wanted = folder.casefold()
    for module in config.modules:
        if module.casefold() == wanted:
            return module
    return None


def _subfolder_key(subfolder: str, module_folder: str) -> str:
    """"DBA LGT Notes" in folder "DBA" -> "lgt notes" (whitespace runs collapsed)."""
    if subfolder.casefold().startswith(module_folder.casefold()):
        subfolder = subfolder[len(module_folder):]
    return " ".join(subfolder.split()).lower()


def _clash_key(folder: Path, file_name: str) -> tuple[str, str]:
    return str(folder), unicodedata.normalize("NFC", file_name).casefold()


def _file_names(targets: dict[str, tuple[Path, str]]) -> dict[str, str]:
    """Distinct PDF file names per folder for the mapped notes (note id -> file name).

    Each note starts at level 0 (`<name>.pdf`). While some names clash in a folder, every
    note in the clash moves up one level: 1 adds the short id, 2 the full id. This also
    separates a short-id clash (two ids with the same first 8 characters) and a title that
    happens to equal another note's suffixed name. Ids are unique within a backup, so
    level 2 is distinct except for ids that differ only by case; a counter settles those.
    """
    level = dict.fromkeys(targets, 0)

    def file_name(note_id: str) -> str:
        name = targets[note_id][1]
        if level[note_id] == 1:
            name = f"{name} ({safe_name(note_id[:SHORT_ID])})"
        elif level[note_id] == 2:
            name = f"{name} ({safe_name(note_id)})"
        return name + ".pdf"

    while True:
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for note_id, (folder, _) in targets.items():
            groups[_clash_key(folder, file_name(note_id))].append(note_id)
        clashes = [ids for ids in groups.values() if len(ids) > 1]
        movable = [i for ids in clashes for i in ids if level[i] < 2]
        if not movable:
            break
        for note_id in movable:
            level[note_id] += 1

    names = {note_id: file_name(note_id) for note_id in targets}
    taken: set[tuple[str, str]] = {
        _clash_key(folder, names[i]) for i, (folder, _) in targets.items()
    }
    for ids in clashes:
        for n, note_id in enumerate(sorted(ids)[1:], start=2):
            folder = targets[note_id][0]
            stem = names[note_id].removesuffix(".pdf")
            while _clash_key(folder, candidate := f"{stem} {n}.pdf") in taken:
                n += 1
            names[note_id] = candidate
            taken.add(_clash_key(folder, candidate))
    return names
