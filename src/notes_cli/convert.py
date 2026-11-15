"""`notes convert`: write a PDF for every mapped note that is new or changed.

What is written
---------------
Only these, nothing else: the PDF at each converted note's planned destination
(`<data root>/CS Degree/Year N/MODULE/<Lecture Notes|LGT|SGT>/<name>.pdf`, written
atomically by `render_pdf`), the destination folder when it is missing, and the
manifest `.notes-manifest.json` in the data root (written atomically: a temp file in the
data root, then `os.replace`). A manifest that cannot be used is renamed aside, see
below. Nothing is ever deleted, and the backup is only read.

Notes are loaded, rendered and released one at a time.

Unchanged
---------
A note is unchanged, and skipped silently, when its manifest entry has the same
`modified` and `path` as now and a file exists at that path. `force` re-renders every
note in scope (use it after the renderer changes). `force` never overrides the
ownership rule below.

Ownership: which existing files may be overwritten
--------------------------------------------------
Every PDF the tool writes carries a stamp in its document information (creator
`notes-cli`, subject `Notein note <id>`; see `render.stamped_note_id`). The stamp, not
the manifest, decides. When a note's destination already exists, it is overwritten only
if it is a PDF stamped

- with this note's id (its own earlier output), or
- with the id of another note, when all of these hold: the manifest records this exact
  path for that note (so the tool put the file here, it is not a copy the user made);
  that note has a different destination in the current backup; and a PDF stamped with
  that note's id already exists at that destination. The file is then a stale copy of
  a note that was renamed or moved away and whose PDF is safely in its new place.

Otherwise the note is not converted and goes to `failed`, and the file is left as it is:

- anything without the stamp (the user's own PDF, a non-PDF, a folder, a symlink, a
  damaged file): "exists and was not made by notes";
- a stamped PDF of a note that no longer has a destination (deleted in Notein, binned,
  moved out of a mapped folder): it is that note's only PDF, so it is kept;
- a stamped PDF of another note that the manifest does not record at this path (e.g. a
  copy the user made to annotate);
- a stamped PDF of another note whose own PDF is not at its new place yet. Such a note
  is tried again later in the same run, after the other note has been converted; if the
  other note fails, or is outside the `module` filter, it stays failed until a later
  run. Notes that swapped titles block only each other: they are all rendered to
  holding files first and moved into place together (`_swap`).

The user resolves any of these by moving or renaming the file. So a lost or corrupt
manifest never exposes the user's files, and never blocks the tool's own earlier output.

Symlinks are never followed: a destination whose folder is reached through a symlink
(anywhere below the data root) is refused, so a link such as `LGT -> Lecture-Slides`
or one pointing outside the data root cannot redirect a PDF.

Manifest
--------
`{note_id: {"path": <POSIX path relative to the data root>, "modified": int,
"title": str, "orphans": [path, ...]}}`. `path`, `modified` and `title` describe the
last PDF written for the note; the optional `orphans` lists the note's earlier PDFs that
are still on disk (it was renamed or moved). `modified` is -1 when unknown (see
`_drop_path`). The manifest is saved after each converted note, so an interrupted run
keeps everything done so far; PDF and manifest are each replaced atomically, and a note
whose PDF was written but not yet recorded is simply converted again next time (its
file carries its own stamp).

A manifest that cannot be read or is not valid (bad JSON, wrong shape, absolute paths or
`..`) is renamed to `.notes-manifest.json.corrupt-<time>`, a warning names that file,
and the run starts with an empty manifest: every note in scope is converted again, and
orphans recorded only in the old manifest are no longer reported. If it cannot even be
renamed, `ManifestError` is raised before anything is converted. A manifest that cannot
be written also raises `ManifestError` (the run stops).

Orphans
-------
A recorded path is an orphan when its file still exists, it is not the current
destination of any note, and either it is a note's `orphans` path, or it is the `path`
of a note whose destination is now different or that has none (gone from the backup,
binned, moved out of a mapped folder). The recorded paths of a note that failed in this
run are not reported: they may be its only PDF. Orphans are reported, never deleted, on every run
while the file exists; once the file is gone its record is dropped. When a note writes
a path another note's record names (the takeover cases above), that path is removed
from the other record. With `module`, only orphans under that module's folder are
reported, and records outside it are left untouched.

Failures, warnings, skips
-------------------------
Any `Exception` from loading or rendering a note (a note with no pages, MuPDF's own
exception types, OSError...) puts `(title, reason)` in `failed`; the note's previous PDF
and manifest entry are left as they were and the run continues. `KeyboardInterrupt`
propagates; the PDF being written is not left behind (`render_pdf`) and the manifest on
disk is the last complete one. The renderer's and loader's `Note.warnings` go to
`warnings`. Unmapped notes go to `skipped` with the mapping reason; with `module`, only
those filed under a folder named like the module or recorded in its folder. Bad backup
entries go to `bad`, always all of them.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from notes_cli.backup import BadEntry, read_backup
from notes_cli.config import RESERVED_FOLDERS, Config
from notes_cli.mapping import Placement, plan
from notes_cli.notedb import load_note
from notes_cli.render import render_pdf, stamped_note_id

MANIFEST_NAME = ".notes-manifest.json"
UNKNOWN_MODIFIED = -1
DEGREE_FOLDER = "CS Degree"  # `Config.degree_dir` is `<data root>/CS Degree`


class ManifestError(Exception):
    """The manifest cannot be used or written; the message is one line."""


@dataclass
class ConvertReport:
    converted: list[Path] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (title, reason)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (title, reason)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (title, warning)
    orphans: list[Path] = field(default_factory=list)
    bad: list[BadEntry] = field(default_factory=list)  # unreadable backup entries


def one_line(text: str) -> str:
    """Text for a one-line listing: stripped, control characters shown as escapes."""
    return "".join(
        ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii")
        for ch in text.strip()
    )


def convert(config: Config, module: str | None = None, force: bool = False,
            log: Callable[[str], object] = print) -> ConvertReport:
    """Convert new and changed mapped notes (of `module` only, if given) to PDF.

    See the module docstring for the rules. Raises `BackupUnreadable` when the backup
    folder is missing and `ManifestError` when the manifest cannot be set aside or
    written; failures of single notes are reported, not raised.
    """
    if module is not None:
        module = config.resolve_module(module)
    backup = read_backup(config.sync_dir)
    placements = plan(backup, config)
    report = ConvertReport(bad=list(backup.bad))
    root = config.data_root
    scope = None if module is None else _rel(config, config.module_dir(module))

    def in_scope(rel: str) -> bool:
        return scope is None or PurePosixPath(rel).is_relative_to(scope)

    manifest = _Manifest.load(root / MANIFEST_NAME, report, log)
    # Every mapped note's destination, whatever the filter: ownership and orphans depend
    # on the whole backup.
    current = {p.note.id: _rel(config, p.dest) for p in placements if p.dest is not None}

    work: list[Placement] = []
    for p in placements:
        if p.dest is None:
            if scope is None or _filed_under(p, module, backup.folder_path(p.note),
                                             manifest.entries.get(p.note.id), in_scope):
                report.skipped.append((p.note.title, p.reason))
            continue
        rel = current[p.note.id]
        if not in_scope(rel):
            continue
        entry = manifest.entries.get(p.note.id)
        if (not force and entry is not None and entry["path"] == rel
                and entry["modified"] == p.note.modified and _present(p.dest)):
            report.unchanged += 1
            continue
        work.append(p)

    total = len(work)
    failed_ids: set[str] = set()
    queue = [(number, p) for number, p in enumerate(work, 1)]
    while queue:
        waiting: list[tuple[int, Placement, str]] = []
        progressed = False
        for number, p in queue:
            title, rel = p.note.title, current[p.note.id]
            prefix = f"[{number}/{total}] {one_line(title)}"
            try:
                problem, later = _blocked(config, p, rel, current, manifest)
                if problem is None:
                    p.dest.parent.mkdir(parents=True, exist_ok=True)
                    warnings = _render(p)
            except Exception as e:
                problem, later = _describe(e), False
            if problem is not None and later:
                waiting.append((number, p, problem))  # tried again after the others
                continue
            if problem is not None:
                failed_ids.add(p.note.id)
                report.failed.append((title, problem))
                log(f"{prefix} -- failed: {one_line(problem)}")
                continue
            progressed = True
            manifest.record(p.note.id, rel, p.note.modified, title)
            manifest.save()
            report.converted.append(p.dest)
            report.warnings += [(title, w) for w in warnings]
            log(f"{prefix} -> {rel}")
        if not progressed:  # nothing freed a path this round
            swapped = _swap(config, [p for _, p, _ in waiting], current)
            if swapped is not None:  # the rest only blocked each other (swapped titles)
                for (number, p, _), warnings in zip(waiting, swapped):
                    rel = current[p.note.id]
                    manifest.record(p.note.id, rel, p.note.modified, p.note.title)
                    report.converted.append(p.dest)
                    report.warnings += [(p.note.title, w) for w in warnings]
                    log(f"[{number}/{total}] {one_line(p.note.title)} -> {rel}")
                manifest.save()
                break
            for number, p, problem in waiting:
                failed_ids.add(p.note.id)
                report.failed.append((p.note.title, problem))
                log(f"[{number}/{total}] {one_line(p.note.title)} -- failed: {one_line(problem)}")
            break
        queue = [(number, p) for number, p, _ in waiting]

    if manifest.prune(current, in_scope):
        manifest.save()
    report.orphans = [root / rel for rel in manifest.orphans(current, in_scope, failed_ids)]
    return report


def _swap(config: Config, blocked: list[Placement], current: dict[str, str]) -> list[list[str]] | None:
    """Convert notes that only block each other (titles swapped: each one's destination
    holds the PDF of another note in `blocked`). Every note is first rendered completely
    to a holding file beside its destination; only when all of them succeeded are the
    holding files moved into place, so no note is ever left without a PDF. Returns each
    note's warnings, or None (nothing changed) when the notes are not such a closed
    group or any of them cannot be rendered."""
    ids = {p.note.id for p in blocked}
    if len(blocked) < 2 or any(stamped_note_id(p.dest) not in ids - {p.note.id} for p in blocked):
        return None
    holding: list[Path] = []
    try:
        warnings = []
        for p in blocked:
            path = p.dest.with_name(f"{p.dest.name}.{os.getpid()}.swap")
            if os.path.lexists(path):
                return None
            holding.append(path)
            note = load_note(p.note.zip_path)
            render_pdf(note, path)
            warnings.append(list(note.warnings))
        for path, p in zip(holding, blocked):
            os.replace(path, p.dest)
        return warnings
    except Exception:
        return None
    finally:
        for path in holding:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _present(path: Path) -> bool:
    """Whether something exists at `path`; an unreadable folder counts as "no", so the
    note goes on to be tried and fails there with a proper reason."""
    try:
        return os.path.lexists(path) and path.exists()
    except OSError:
        return False


def _render(p: Placement) -> list[str]:
    """Load, render and release one note; its warnings."""
    note = load_note(p.note.zip_path)
    render_pdf(note, p.dest)
    return list(note.warnings)


def _blocked(config: Config, p: Placement, rel: str, current: dict[str, str],
             manifest: _Manifest) -> tuple[str | None, bool]:
    """Why `p.dest` must not be written (or None), and whether that may clear up later
    in the same run, once another note has been converted (see "Ownership")."""
    dest = p.dest
    degree = config.degree_dir
    if (not dest.is_relative_to(degree) or dest.suffix != ".pdf"
            or dest.parent.name not in set(config.subfolder_map.values())):
        return f"refusing to write outside the module folders: {rel}", False  # cannot happen
    # No symlink anywhere between the data root and the destination folder.
    expected = config.data_root.resolve() / dest.parent.relative_to(config.data_root)
    if dest.parent.resolve() != expected:
        return (f"the folder of {rel} is reached through a symbolic link; "
                "notes does not write through links"), False
    if not os.path.lexists(dest):
        return None, False
    owner = stamped_note_id(dest) if not dest.is_symlink() else None
    if owner == p.note.id:
        return None, False
    if owner is None:
        return (f"{rel} exists and was not made by notes; not overwritten. "
                "Move or rename it to convert this note"), False
    other = manifest.entries.get(owner, {}).get("title") or owner
    if owner not in current:
        return (f"{rel} holds the PDF of another note ({one_line(other)!r}) that is no "
                "longer converted; not overwritten. Move or rename that file to convert this note"), False
    if not manifest.records(owner, rel):
        return (f"{rel} is a copy of the PDF of another note ({one_line(other)!r}) that notes "
                "did not put there; not overwritten. Move or rename it to convert this note"), False
    new_home = config.data_root / current[owner]
    if current[owner] != rel and stamped_note_id(new_home) == owner:
        return None, False  # a stale copy: that note's PDF is safely at its new place
    return (f"{rel} still holds the PDF of another note ({one_line(other)!r}) whose own PDF "
            "has not been written to its new place; not overwritten. Convert that note first "
            "(run 'notes convert' again without a module filter)"), True


def _filed_under(p: Placement, module: str, folder_path: tuple[str, ...], entry: dict | None,
                 in_scope: Callable[[str], bool]) -> bool:
    """Whether a skipped note belongs with `module`: one of its Notein folders is named
    like the module, or its last PDF was in the module's folder."""
    wanted = module.casefold()
    return (any(t.casefold() == wanted for t in folder_path)
            or (entry is not None and in_scope(entry["path"])))


def _rel(config: Config, path: Path) -> str:
    return path.relative_to(config.data_root).as_posix()


def _describe(error: Exception) -> str:
    text = str(error).strip()
    text = text.splitlines()[0] if text else ""
    text = text if len(text) <= 200 else text[:197] + "..."
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


# -- manifest -------------------------------------------------------------------------------


class _Manifest:
    def __init__(self, path: Path, entries: dict[str, dict]):
        self.path = path
        self.root = path.parent
        self.entries = entries

    @classmethod
    def load(cls, path: Path, report: ConvertReport, log) -> _Manifest:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return cls(path, {})
        except OSError as e:
            problem = f"cannot be read ({e.strerror or e})"
        else:
            try:
                return cls(path, _validate(json.loads(data.decode("utf-8"))))
            except (UnicodeDecodeError, ValueError) as e:
                problem = f"is not a valid manifest ({_describe(e)})"
        aside = _set_aside(path)
        message = (f"{problem}; kept as {aside.name}. Starting with an empty manifest: "
                   "every note is converted again, and orphans recorded only in the old "
                   "manifest are no longer reported")
        report.warnings.append((MANIFEST_NAME, message))
        log(f"warning: {MANIFEST_NAME} {message}")
        return cls(path, {})

    def _exists(self, rel: str) -> bool:
        return os.path.lexists(self.root / rel)

    def records(self, note_id: str, rel: str) -> bool:
        """Whether the manifest says the tool wrote `rel` for note `note_id`."""
        entry = self.entries.get(note_id)
        return entry is not None and (entry["path"] == rel or rel in entry.get("orphans", []))

    def record(self, note_id: str, rel: str, modified: int, title: str) -> None:
        """Note `note_id` has just been written to `rel`."""
        for other_id in list(self.entries):
            if other_id == note_id:
                continue
            other = self.entries[other_id]
            if rel in other.get("orphans", []):
                other["orphans"].remove(rel)
                if not other["orphans"]:
                    del other["orphans"]
            if other["path"] == rel:
                self._drop_path(other_id)
        old = self.entries.get(note_id)
        previous = [old["path"], *old.get("orphans", [])] if old else []
        orphans = [x for x in dict.fromkeys(previous) if x != rel and self._exists(x)]
        entry = {"path": rel, "modified": modified, "title": title}
        if orphans:
            entry["orphans"] = orphans
        self.entries[note_id] = entry

    def _drop_path(self, note_id: str) -> None:
        """Forget `path` for a note: its next recorded orphan takes its place (with an
        unknown `modified`), or the entry goes when it has none."""
        entry = self.entries[note_id]
        orphans = entry.get("orphans", [])
        if not orphans:
            del self.entries[note_id]
            return
        entry["path"] = orphans.pop(0)
        entry["modified"] = UNKNOWN_MODIFIED
        if not orphans:
            del entry["orphans"]

    def prune(self, current: dict[str, str], in_scope: Callable[[str], bool]) -> bool:
        """Drop records of files that are gone (and orphan records of paths another note
        now holds), for paths in scope only. Returns whether anything changed."""
        before = json.dumps(self.entries, sort_keys=True)
        held = {e["path"] for e in self.entries.values()}
        for note_id in list(self.entries):
            entry = self.entries[note_id]
            if "orphans" in entry:
                entry["orphans"] = [
                    x for x in entry["orphans"]
                    if not in_scope(x) or (self._exists(x) and x not in held)
                ]
                if not entry["orphans"]:
                    del entry["orphans"]
            path = entry["path"]
            if in_scope(path) and path != current.get(note_id) and not self._exists(path):
                self._drop_path(note_id)
        return json.dumps(self.entries, sort_keys=True) != before

    def orphans(self, current: dict[str, str], in_scope: Callable[[str], bool],
                failed: set[str] = frozenset()) -> list[str]:
        """Recorded files no note is converted to any more. The files of notes in
        `failed` are left out: an old path may be such a note's only PDF."""
        destinations = set(current.values())
        found = set()
        for note_id, entry in self.entries.items():
            if note_id in failed:
                continue
            if entry["path"] != current.get(note_id):
                found.add(entry["path"])
            found.update(entry.get("orphans", []))
        return sorted(x for x in found
                      if x not in destinations and in_scope(x) and self._exists(x))

    def save(self) -> None:
        text = json.dumps(self.entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        try:
            fd, tmp = tempfile.mkstemp(prefix=MANIFEST_NAME + ".", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise ManifestError(f"cannot write the manifest {self.path}: {e.strerror or e}") from e


def _valid_rel(value: object) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    # Only paths the tool itself writes: a PDF inside a module folder under "CS Degree".
    return (not path.is_absolute() and ".." not in path.parts and path.suffix == ".pdf"
            and len(path.parts) == 5 and path.parts[0] == DEGREE_FOLDER
            and path.parts[3].casefold() not in RESERVED_FOLDERS)


def _validate(data: object) -> dict[str, dict]:
    """The manifest's entries, or ValueError naming the first problem."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    entries = {}
    for note_id, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"entry {note_id!r} is not an object")
        if not _valid_rel(entry.get("path")):
            raise ValueError(f"entry {note_id!r} has no valid relative 'path'")
        modified = entry.get("modified")
        if not isinstance(modified, int) or isinstance(modified, bool):
            raise ValueError(f"entry {note_id!r} has no integer 'modified'")
        if not isinstance(entry.get("title"), str):
            raise ValueError(f"entry {note_id!r} has no 'title'")
        orphans = entry.get("orphans", [])
        if not isinstance(orphans, list) or not all(_valid_rel(x) for x in orphans):
            raise ValueError(f"entry {note_id!r} has an invalid 'orphans' list")
        clean = {"path": entry["path"], "modified": modified, "title": entry["title"]}
        if orphans:
            clean["orphans"] = list(dict.fromkeys(orphans))
        entries[note_id] = clean
    return entries


def _set_aside(path: Path) -> Path:
    """Rename an unusable manifest to a new `<name>.corrupt-<time>` beside it."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):
        aside = path.with_name(f"{path.name}.corrupt-{stamp}" + ("" if n == 1 else f"-{n}"))
        if os.path.lexists(aside):
            continue
        try:
            os.rename(path, aside)
        except OSError as e:
            raise ManifestError(
                f"the manifest {path} cannot be used and cannot be moved aside "
                f"({e.strerror or e}); move it away and run again") from e
        return aside
    raise ManifestError(f"the manifest {path} cannot be used and there is no free name to move it to")
