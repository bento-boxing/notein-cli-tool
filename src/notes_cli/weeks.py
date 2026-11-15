"""Week numbers from file and folder names, and the source PDFs of a module.

`week_of` is best-effort: it recognises the ways weeks are written in the real titles
(`W3`, `Week 3`, `Week_5`, `week10`, `Week 03`, `wk 5`, `S1W2` = semester 1 week 2,
`T2W7` = term 2 week 7, `lgt4` = LGT of week 4) and returns None for everything else,
in particular:

- numbers that are not weeks: module codes (`5abc2fm2`, `4ABC1DSA`), dates
  (`Slides-20261114`), section numbers (`1_1_Overview`), SGT and lab session numbers
  (`SGT_3`, `SGT1-…`, `ELC SGT 1`, `Lab 2`), exercise numbers (`Ex2`);
- names that mention two different weeks (`W3 recap of W2`), or a range of weeks
  (`W1-2`, `Week3-4`, `W3&4`, `week 3 and 4`, `Revision W1-5`, `week1_2`): the file
  spans several weeks, so with `--week` it is handed to Claude as unassigned instead of
  being filed under the first week or dropped. A number after the week that is not
  another week is not a range: `W3 slides 1-20`, `week 3 - 2026-10-05`, `week7_2opt`.

In `S1W2` and `T2W7` the semester or term number is ignored: a second-semester `S2W1`
gets week 1, the same as `S1W1`.

`gather` lists the PDFs in a module's source folders: `Lecture-Slides/` (user-managed)
and the folders converted notes are written to (`Lecture Notes/`, `LGT/`, `SGT/`, taken
from the config). Left out: hidden files and folders, non-PDF files, and anything whose
real location is not inside one of those source folders of this module (symlinks out of
the module, or into `Flashcards/` or `Practice Exams/`). Symlinked folders are not
descended into. Lists are in natural order of the path relative to the module folder
(`W2` before `W10`), case-insensitively, so the result is deterministic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from notes_cli.config import Config

SLIDES_FOLDER = "Lecture-Slides"

_NUM = r"(\d{1,2})(?!\d)"  # one or two digits, not part of a longer number
_WEEK_PATTERNS = [
    # "Week 3", "Week_5", "week10", "Week 03", "wk 5"; not "midweek3"
    re.compile(r"(?<![a-z])(?:week|wk)[\s_-]*" + _NUM, re.IGNORECASE),
    # "W3", "S1W2", "T2W7"; not "SW2", "HW3" or "2W3" (W must start a word, or follow
    # S<n>/T<n>), and not "W3C" (a letter right after the number)
    re.compile(r"(?<![a-z0-9])(?:[st]\d{1,2})?w" + _NUM + r"(?![a-z])", re.IGNORECASE),
    # "lgt4", "_lgt_4": LGT sessions are weekly. SGT sessions are not, so "sgt3" is no week.
    re.compile(r"(?<![a-z])lgt[\s_-]?" + _NUM, re.IGNORECASE),
]


# What turns a week number into a range: a connector, then another one- or two-digit
# number (optionally written as "W4"/"week 4") that is not followed by a digit or a
# letter. "week7_2opt" (letters follow), "slides 1-20" (no connector right after the
# week) and "week 3 - 2026-10-05" (four digits) are not ranges.
_RANGE_AFTER = re.compile(
    r"\s*(?:[-–—+&_]|\band\b|\bto\b)\s*(?:week|wk|w)?\s*\d{1,2}(?![\da-z])", re.IGNORECASE)


def week_of(name: str) -> int | None:
    """The week number written in `name`, or None if there is none, it is ambiguous, or
    the name covers a range of weeks."""
    found = set()
    for pattern in _WEEK_PATTERNS:
        for m in pattern.finditer(name):
            if _RANGE_AFTER.match(name, m.end()):
                return None
            found.add(int(m.group(1)))
    return found.pop() if len(found) == 1 else None


@dataclass
class Sources:
    slides: list[Path] = field(default_factory=list)
    notes: list[Path] = field(default_factory=list)
    unassigned: list[Path] = field(default_factory=list)


def natural_key(rel: str) -> tuple:
    """Sort key for a relative path: per part, case-insensitive, numbers by value."""
    parts = []
    for part in rel.split("/"):
        chunks = re.split(r"(\d+)", part.casefold())
        parts.append(tuple(int(c) if i % 2 else c for i, c in enumerate(chunks)))
    return (tuple(parts), rel)


def note_folders(config: Config) -> list[str]:
    """The folders converted notes go to (`Lecture Notes`, `LGT`, `SGT` by default)."""
    return sorted(set(config.subfolder_map.values()), key=lambda name: (name.casefold(), name))


def source_week(module_dir: Path, path: Path) -> int | None:
    """The week of a source file at `path` under `module_dir` (see `gather`)."""
    parts = path.relative_to(module_dir).parts
    folders_nearest_first = list(reversed(parts[1:-1]))
    if parts[0] == SLIDES_FOLDER:
        candidates = folders_nearest_first + [path.name]
    else:
        candidates = [path.name] + folders_nearest_first
    for name in candidates:
        week = week_of(name)
        if week is not None:
            return week
    return None


def _pdfs_under(module_dir: Path, real_module: Path, folder: str,
                source_folders: set[str]) -> list[Path]:
    """PDFs found under `module_dir/folder` whose real location is in a source folder."""
    top = module_dir / folder
    if not top.is_dir():
        return []
    found = []
    # os.walk follows `top` itself if it is a symlink; the real-location check below
    # then drops everything if it points outside the module.
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not name.lower().endswith(".pdf"):
                continue
            path = Path(dirpath) / name
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):  # broken link or link loop
                continue
            if (real.is_file() and real.is_relative_to(real_module)
                    and real.relative_to(real_module).parts[0] in source_folders):
                found.append(path)
    return found


def gather(config: Config, module: str, week: int | None = None) -> Sources:
    """The module's source PDFs; with `week`, only that week's, plus unassigned ones.

    A slide's week is that of its nearest enclosing folder (below `Lecture-Slides`) that
    has one, else that of its file name: the folders reflect how the user filed them. A
    note's week is that of its file name, else of its nearest enclosing folder. With
    `week`, files of that week are kept, files with no week go to `unassigned`, and
    files of other weeks are left out. Without `week`, `unassigned` is empty.
    """
    module_dir = config.module_dir(module)
    try:
        real_module = module_dir.resolve(strict=True)
    except OSError:
        return Sources()
    folders = note_folders(config)
    allowed = {SLIDES_FOLDER, *folders}

    def ordered(paths: list[Path]) -> list[Path]:
        return sorted(paths, key=lambda p: natural_key(p.relative_to(module_dir).as_posix()))

    slides = ordered(_pdfs_under(module_dir, real_module, SLIDES_FOLDER, allowed))
    notes = ordered([p for f in folders for p in _pdfs_under(module_dir, real_module, f, allowed)])
    if week is None:
        return Sources(slides=slides, notes=notes)

    sources = Sources()
    for paths, kept in ((slides, sources.slides), (notes, sources.notes)):
        for path in paths:
            found = source_week(module_dir, path)
            if found is None:
                sources.unassigned.append(path)
            elif found == week:
                kept.append(path)
    sources.unassigned = ordered(sources.unassigned)
    return sources
