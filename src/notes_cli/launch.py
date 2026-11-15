"""Prepare a prompt file in a module folder and replace this process with `claude`.

`prepare_flashcards` fills the packaged template `prompts/flashcards.md` with the module's
sources and writes it to `<module>/Flashcards/FLASHCARDS_PROMPT.md`. It writes nothing
else: it creates `Flashcards/` if needed and replaces only that one file (through a temp
file and a rename, so a symlink at that path is replaced, never written through). Source
folders and existing card files are never touched.

`prepare_exam` does the same for exams with `prompts/exam.md`, writing
`<module>/Practice Exams/EXAM_PROMPT.md`, plus a copy of `prompts/exam_template.md` as
`Practice Exams/exam_template.md` when there is none (an existing one is the user's and is
kept). Existing exams and `concept_matrix.md` are never touched.

`launch_claude` changes into the module folder and execs `claude` with a one-line
instruction to read that file. Paths in the prompt are relative to the module folder,
which is Claude's working directory. File names are shown with control characters
escaped, so a name cannot break out of its list item; shell commands in the prompt quote
names with `shlex.quote`.

Exams are not launched with exec: `run_claude_session` runs claude as a child process
(sharing the terminal) with `--settings` carrying a Stop hook (`exam_session_settings`),
which runs `python -P -m notes_cli exam stop-hook MODULE --snapshot JSON` (this interpreter,
so the same code) each time the session is about to finish a turn and keeps it working
until the exams written in the session (those whose number was not in the snapshot taken
at its start) are valid; when claude exits, `notes exam` checks them again (`examsession`; user request 2026-11-15, "I NEVER want invalid
exams"). Flashcards still use `launch_claude` (exec, no hook).
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import NoReturn

from notes_cli import anki
from notes_cli.config import Config
from notes_cli.examtools import EXAM_DIR, find_exams
from notes_cli.sync import ToolMissing
from notes_cli.weeks import SLIDES_FOLDER, Sources, gather, note_folders, source_week

FLASHCARDS_DIR = "Flashcards"
PROMPT_NAME = "FLASHCARDS_PROMPT.md"
PLACEHOLDERS = ("module", "module_id", "week_scope", "slides", "notes", "unassigned",
                "nearby", "out_dir", "prompt_file", "note_folders", "check_cmd", "backup_cmd")
NEARBY_WEEKS = 2  # with --week N, weeks N-2..N+2 are listed for comparison

EXAM_PROMPT_NAME = "EXAM_PROMPT.md"
EXAM_TEMPLATE_NAME = "exam_template.md"
EXAM_PLACEHOLDERS = ("module", "slides", "notes", "setup_answers", "out_dir", "prompt_file",
                     "note_folders", "slides_heading", "notes_heading", "always_provided",
                     "validate_one_cmd", "validate_cmd", "stats_cmd", "merge_cmd", "pdf_export")


class LaunchError(Exception):
    """The prompt cannot be prepared or claude cannot be started (message is for the user)."""


class NoSources(LaunchError):
    """The module (or the requested week) has no source PDFs."""


def notes_command() -> str:
    """How the launched session should run this tool, as a shell word sequence.

    The absolute path of the running `notes` executable when that is what is running
    (the launched session may not have it on PATH, e.g. in a venv); else `notes` as
    found on PATH; else this interpreter with `-m notes_cli.cli`, which always works.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).name == "notes" and Path(argv0).is_file():
        return shlex.quote(os.path.abspath(argv0))
    found = shutil.which("notes")
    if found:
        return shlex.quote(os.path.abspath(found))
    return f"{shlex.quote(sys.executable)} -m notes_cli.cli"


def session_stamp() -> str:
    """This session's backup stamp, `YYYYMMDD-HHMMSS` (local time)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _printable(text: str) -> str:
    """`text` with control and other non-printable characters shown as escapes."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii")
                   for ch in text)


def _code(text: str) -> str:
    """`text` as Markdown inline code, whatever backticks it contains."""
    fence = "`"
    while fence in text:
        fence += "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _name(module_dir: Path, path: Path) -> tuple[str, bool]:
    """A source's path relative to the module folder, as inline code, and whether control
    characters in it had to be escaped."""
    rel = path.relative_to(module_dir).as_posix()
    shown = _printable(rel)
    return _code(shown), shown != rel


def _weeks_text(weeks: list[int]) -> str:
    if len(weeks) == 1:
        return f"week {weeks[0]}"
    return "weeks " + ", ".join(str(w) for w in weeks)


def _listing(module_dir: Path, paths: list[Path], describe) -> str:
    if not paths:
        return "(none)"
    lines = []
    for path in paths:
        name, escaped = _name(module_dir, path)
        note = describe(path) + ("; name shown with control characters escaped" if escaped else "")
        lines.append(f"- {name} ({note})")
    return "\n".join(lines)


def _fill(template: str, values: dict[str, str]) -> str:
    """Replace `{name}` placeholders in one pass (values are not re-scanned, and other
    braces in the template, such as LaTeX, are left alone)."""
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, values)) + r")\}")
    return pattern.sub(lambda m: values[m.group(1)], template)


def _weeks_with_sources(module_dir: Path, sources: Sources) -> list[int]:
    return sorted({w for p in sources.slides + sources.notes
                   if (w := source_week(module_dir, p)) is not None})


def _folder_list(names: list[str]) -> str:
    shown = [_code(name + "/") for name in names]
    if len(shown) <= 1:
        return "".join(shown)
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _nearby(module_dir: Path, everything: Sources, week: int) -> str:
    """The sources of the weeks around `week`, names only, for comparison."""
    blocks = []
    for w in range(week - NEARBY_WEEKS, week + NEARBY_WEEKS + 1):
        if w == week:
            continue
        paths = [p for p in everything.slides + everything.notes
                 if source_week(module_dir, p) == w]
        if paths:
            names = "\n".join(f"- {_name(module_dir, p)[0]}" for p in paths)
            blocks.append(f"Week {w} (not in scope):\n\n{names}")
    body = "\n\n".join(blocks) if blocks else "(none)"
    return ("\n**Neighbouring weeks, not in scope.** Names only, for comparison: because the "
            "week labels can be off, triage (section 2) looks at the first pages of these too, "
            "so that a mismatch can be spotted and reported. Make no cards from them unless "
            f"the user says so.\n\n{body}\n")


def render_flashcards_prompt(config: Config, module: str, week: int | None,
                             sources: Sources, everything: Sources | None = None,
                             stamp: str | None = None) -> str:
    """The filled-in flashcards prompt for `module` (and `week`, if given).

    `everything` is the module's whole source list (for the neighbouring weeks; gathered
    if not given) and `stamp` the session's backup stamp (now, if not given).
    """
    module_dir = config.module_dir(module)
    if everything is None:
        everything = gather(config, module)
    stamp = stamp or session_stamp()

    def week_note(path: Path) -> str:
        w = source_week(module_dir, path)
        return f"week {w}" if w is not None else "no week in the name"

    def kind(path: Path) -> str:
        if path.relative_to(module_dir).parts[0] == SLIDES_FOLDER:
            return "slides: read as text"
        return "handwritten: read as page images"

    out_dir = FLASHCARDS_DIR
    if week is None:
        found = _weeks_with_sources(module_dir, sources)
        undated = any(source_week(module_dir, p) is None for p in sources.slides + sources.notes)
        if found:
            scope = (f"the whole module: every week that has sources ({_weeks_text(found)}, "
                     "going by the names). Write one card file per week.")
        else:
            scope = ("the whole module. None of the sources has a week in its name, so the "
                     "weeks have to be worked out from their content (section 2); write one "
                     "card file per week.")
        if found and undated:
            scope += (" Sources marked \"no week in the name\" are sorted into weeks by triage "
                      "(section 2).")
        unassigned = ("(none listed separately: in this run the sources without a week are in "
                      "the lists above, marked \"no week in the name\"; this paragraph is about "
                      "them)" if undated else "(none)")
        nearby = ""
    else:
        name = f"{out_dir}/{module} Week {week:02d}.txt"
        scope = (f"week {week} only. Write or update only {_code(_printable(name))} (and its "
                 "topics and last-id files). Sources of other weeks were left out on purpose, "
                 "apart from the neighbouring weeks listed at the end of section 1 for "
                 "comparison.")
        unassigned = _listing(module_dir, sources.unassigned, kind)
        nearby = _nearby(module_dir, everything, week)

    week_file = f"{out_dir}/{module} Week NN.txt"
    backup = f"{out_dir}/{anki.BACKUP_DIR}/{anki.backup_name(module, 'NN', stamp)}"
    q = shlex.quote
    values = {
        "module": _printable(module),
        "module_id": _printable(anki.module_id(module)),
        "week_scope": scope,
        "slides": _listing(module_dir, sources.slides, week_note),
        "notes": _listing(module_dir, sources.notes, week_note),
        "unassigned": unassigned,
        "nearby": nearby,
        "out_dir": out_dir,
        "prompt_file": f"{out_dir}/{PROMPT_NAME}",
        "note_folders": _folder_list(note_folders(config)),
        "check_cmd": f"{notes_command()} flashcards check {q(week_file)}",
        "backup_cmd": (f"mkdir -p {q(out_dir + '/' + anki.BACKUP_DIR)} && "
                       f"{{ [ -e {q(backup)} ] || cp -p {q(week_file)} {q(backup)}; }}"),
    }
    template = (resources.files("notes_cli") / "prompts" / "flashcards.md").read_text("utf-8")
    return _fill(template, values)


def prepare_flashcards(config: Config, module: str, week: int | None) -> Path:
    """Write `Flashcards/FLASHCARDS_PROMPT.md` in the module folder and return its path.

    Raises NoSources (writing nothing) when the module has no slides or notes, or, with
    `week`, when no slide and no note is labelled with that week (files without a week
    alone are not enough to start a session), and LaunchError when `Flashcards` would
    lead outside the module folder or the prompt cannot be written.
    """
    module = config.resolve_module(module)
    module_dir = config.module_dir(module)
    everything = gather(config, module)
    if not (everything.slides or everything.notes):
        raise NoSources(f"no converted notes or slides for {module}; "
                        f"run 'notes convert {module}' first")
    sources = everything if week is None else gather(config, module, week)
    if not (sources.slides or sources.notes):
        weeks = _weeks_with_sources(module_dir, everything)
        undated = len(sources.unassigned)
        extra = (f" ({undated} {'file has' if undated == 1 else 'files have'} no week in the "
                 "name)" if undated else "")
        have = (f"; sources exist for {_weeks_text(weeks)}" if weeks
                else "; no source has a week in its name, so run without --week")
        raise NoSources(f"no notes or slides for {module} week {week}{extra}{have}")

    text = render_flashcards_prompt(config, module, week, sources, everything)

    out_dir = module_dir / FLASHCARDS_DIR
    _check_plain_folder(out_dir)
    return _write_atomic(out_dir, PROMPT_NAME, text.encode("utf-8"))


def _check_plain_folder(out_dir: Path) -> None:
    """Refuse an output folder that is a symlink (it could lead outside the module) or
    not a folder at all. A missing folder is fine: it is created when writing."""
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise LaunchError(f"{out_dir} is not a plain folder inside the module folder; "
                          "move it out of the way and try again")


def _write_atomic(out_dir: Path, name: str, data: bytes) -> Path:
    """Write `out_dir/name` through a temp file and a rename (a symlink at that path is
    replaced, never written through), creating `out_dir` if needed."""
    path = out_dir / name
    tmp = out_dir / f".{name}.tmp"
    try:
        out_dir.mkdir(exist_ok=True)
        try:
            with open(tmp, "xb") as f:
                f.write(data)
        except FileExistsError:
            tmp.unlink()  # left by an interrupted run; it is ours
            with open(tmp, "xb") as f:
                f.write(data)
        os.replace(tmp, path)
    except OSError as e:
        raise LaunchError(f"cannot write {path}: {e.strerror or e}") from e
    return path


# --- exams ------------------------------------------------------------------------------


def _source_list(module_dir: Path, paths: list[Path], none: str) -> str:
    """One list item per file, names only: exams sample the whole module, so no week
    labels (which would only be guesses from the names) are attached."""
    if not paths:
        return none
    lines = []
    for path in paths:
        name, escaped = _name(module_dir, path)
        lines.append(f"- {name}" + (" (name shown with control characters escaped)"
                                    if escaped else ""))
    return "\n".join(lines)


def _and(items: list) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _format_split(mcq: int) -> str:
    if mcq == 100:
        return "100% multiple choice (no written questions)"
    if mcq == 0:
        return "0% multiple choice: every question is written"
    return f"{mcq}% multiple choice, {100 - mcq}% written"


def _setup_answers(module: str, mcq: int | None, questions: int | None,
                   exams: list[int], has_matrix: bool) -> str:
    """The setup questions answered by the command line, and which ones Claude asks."""
    answered = [f"- **Module name** (question 1): **{_printable(module)}**"]
    ask = []
    if mcq is None:
        ask.append(2)
    else:
        answered.append(f"- **Exam format split** (question 2): **{_format_split(mcq)}**")
    if questions is None:
        ask.append(3)
    else:
        line = f"- **Questions per exam** (question 3): **{questions}**"
        if mcq is not None:
            exact = questions * mcq % 100 == 0
            m = (questions * mcq + 50) // 100          # rounded half up
            line += (f", so {'' if exact else 'about '}{m} multiple-choice and {questions - m} "
                     f"written questions per exam{'' if exact else ' (rounded)'}")
        answered.append(line)
    ask += [4, 5]
    text = ("**Already answered by the `notes exam` command** (these count as answered; do not "
            "ask them again):\n\n" + "\n".join(answered)
            + f"\n\nAsk me questions {_and(ask)} and wait for my answers.")

    found = []
    if exams:
        found.append(f"exam{'s' if len(exams) > 1 else ''} {_and(exams)}")
    if has_matrix:
        found.append("`concept_matrix.md`")
    if not found:
        return text
    them = "them" if len(exams) + has_matrix > 1 else "it"
    text += f"\n\n**`{EXAM_DIR}/` already holds {' and '.join(found)} from an earlier session.**"
    if exams:
        nxt = max(exams) + 1
        one = "them" if len(exams) > 1 else "it"
        files = ", ".join(f"`exam{n}.md`, `exam{n}_answers.md`, `exam{n}_full.md`" for n in exams)
        text += (f" Before generating, ask me whether to replace {them} or to keep {them} and "
                 f"number the new exams after {one} ({nxt}-{nxt + 4} and {nxt + 5}-{nxt + 9} "
                 "instead of 1-5 and 6-10); never overwrite an existing exam file without my "
                 f"answer. If I keep {one}, each batch subagent's brief gives its new exam "
                 f"numbers and lists these files as off-limits: {files} (those that exist).")
        gaps = sorted(set(range(1, max(exams) + 1)) - set(exams))
        if gaps:
            text += (f" Exam number{'s' if len(gaps) > 1 else ''} {_and(gaps)} "
                     f"{'are' if len(gaps) > 1 else 'is'} missing, perhaps deleted because a "
                     f"session ended before {'they were' if len(gaps) > 1 else 'it was'} "
                     f"valid: also offer to write just {'those' if len(gaps) > 1 else 'that one'}.")
    if has_matrix:
        existing = _and(exams) if exams else "none"
        text += (" Also ask whether the new exams extend the existing `concept_matrix.md` (so "
                 "they avoid its concepts) or replace it, and tell batch 1 which.\n\n"
                 f"**Exams that exist in `{EXAM_DIR}/` now** (those with an `exam<N>.md`): "
                 f"{existing}. `concept_matrix.md` may still mention exams that no longer "
                 "exist: `notes` deletes the exams of a session that are still not valid "
                 "when the session ends (or, if `notes` itself was stopped, at the start of "
                 "the next `notes exam` run), but it never edits `concept_matrix.md`. So "
                 "unless I choose to replace the matrix, once I have answered the setup "
                 "questions and before batch 1 starts, remove from `concept_matrix.md` every "
                 "reference to an exam number not in that list (where a row names several "
                 "exams, edit the row rather than dropping it); then start batch 1.")
    return text


def _pdf_export_note() -> str:
    if shutil.which("pandoc"):
        return (f"`pandoc` is installed, so a merged file converts with `pandoc "
                f"'{EXAM_DIR}/exam[N]_full.md' -o '{EXAM_DIR}/exam[N]_full.pdf'` (N filled in).")
    return ("Converting them to PDF needs `pandoc`, which is not installed on this computer "
            "(it was not on PATH when this file was written): if I ask for PDFs, tell me that "
            "pandoc is needed instead of exporting them some other way.")


def render_exam_prompt(config: Config, module: str, mcq: int | None, questions: int | None,
                       sources: Sources) -> str:
    """The filled-in exam prompt for `module`. Reads `Practice Exams/` (to mention exams
    left from an earlier session) but writes nothing."""
    _check_flags(mcq, questions)
    module = config.resolve_module(module)
    module_dir = config.module_dir(module)
    out_dir = module_dir / EXAM_DIR
    q = shlex.quote
    notes = notes_command()
    folders = _folder_list(note_folders(config))
    if sources.slides:
        slides_heading = (f"**Primary source: the lecture slides** (`{SLIDES_FOLDER}/`; typed "
                          "PDFs, read as text). Questions are based on these.")
        notes_heading = ("**Secondary source: the handwritten notes** from lectures and "
                         "tutorials (read as page images). Use them for extra content: what the "
                         "lecturer added, worked examples, tutorial and lab exercises.")
        always = (f"1. **Lecture slides** — the primary source of all question content. Stored "
                  f"in directory `{SLIDES_FOLDER}` and listed under Source Material. The "
                  f"handwritten notes listed there ({folders}) are a secondary source for extra "
                  "content.")
    else:
        slides_heading = f"**Lecture slides** (`{SLIDES_FOLDER}/`):"
        notes_heading = ("**Primary source: the handwritten notes** from lectures and tutorials "
                         "(read as page images). This module has no lecture slides, so questions "
                         "are based on these notes: what the lecturer presented and said, worked "
                         "examples, tutorial and lab exercises.")
        always = ("1. **Handwritten notes** — the primary source of all question content, as "
                  f"this module has no lecture slides (`{SLIDES_FOLDER}` holds no PDFs). Stored "
                  f"in {folders} and listed under Source Material.")
    values = {
        "module": _printable(module),
        "slides": _source_list(module_dir, sources.slides,
                               "(none: this module has no lecture slides)"),
        "notes": _source_list(module_dir, sources.notes,
                              "(none: there are no converted handwritten notes for this module)"),
        "slides_heading": slides_heading,
        "notes_heading": notes_heading,
        "always_provided": always,
        "validate_one_cmd": f"{notes} exam validate {q(module)} N",
        "setup_answers": _setup_answers(module, mcq, questions, find_exams(out_dir),
                                        (out_dir / "concept_matrix.md").is_file()),
        "out_dir": EXAM_DIR,
        "prompt_file": f"{EXAM_DIR}/{EXAM_PROMPT_NAME}",
        "note_folders": folders,
        "validate_cmd": f"{notes} exam validate {q(module)} all",
        "stats_cmd": f"{notes} exam stats {q(module)}",
        "merge_cmd": _code(f"{notes} exam merge {q(module)} all"),
        "pdf_export": _pdf_export_note(),
    }
    template = (resources.files("notes_cli") / "prompts" / "exam.md").read_text("utf-8")
    return _fill(template, values)


def _check_flags(mcq: int | None, questions: int | None) -> None:
    if mcq is not None and not 0 <= mcq <= 100:
        raise ValueError(f"--mcq must be a percentage from 0 to 100, not {mcq}")
    if questions is not None and questions < 1:
        raise ValueError(f"--questions must be a positive number, not {questions}")


def prepare_exam(config: Config, module: str, mcq: int | None, questions: int | None,
                 log=None) -> Path:
    """Write `Practice Exams/EXAM_PROMPT.md` and `Practice Exams/exam_template.md` in the
    module folder and return the prompt's path.

    Writes nothing else: creates `Practice Exams/` if needed, always replaces the prompt
    (it is generated), and writes the template only if there is none. An existing
    `exam_template.md` that differs from the packaged one (the user may have edited it), or
    is a link, is kept as it is and `log` is told so; an identical one is left alone.
    Existing exams, `concept_matrix.md` and the sources are never touched.

    Raises ValueError for out-of-range `mcq`/`questions`, NoSources (writing nothing) when
    the module has no slides and no notes, and LaunchError when `Practice Exams` is not a
    plain folder, `exam_template.md` is a folder, or a file cannot be written.
    """
    _check_flags(mcq, questions)
    module = config.resolve_module(module)
    module_dir = config.module_dir(module)
    sources = gather(config, module)
    if not (sources.slides or sources.notes):
        raise NoSources(f"no converted notes or slides for {module}; "
                        f"run 'notes convert {module}' first")

    out_dir = module_dir / EXAM_DIR
    _check_plain_folder(out_dir)
    template = out_dir / EXAM_TEMPLATE_NAME
    packaged = (resources.files("notes_cli") / "prompts" / EXAM_TEMPLATE_NAME).read_bytes()
    shown = f"{EXAM_DIR}/{EXAM_TEMPLATE_NAME}"
    if template.is_symlink():
        keep = f"kept {shown}: it is a link, so it was left as it is"
    elif template.exists() and not template.is_file():
        raise LaunchError(f"{template} is not a file; move it out of the way and try again")
    elif template.is_file():
        try:
            same = template.read_bytes() == packaged
        except OSError as e:
            raise LaunchError(f"cannot read {template}: {e.strerror or e}") from e
        keep = None if same else (f"kept your edited {shown} (it differs from the packaged "
                                  "template); delete it to get the packaged one next time")
    else:
        keep = None

    text = render_exam_prompt(config, module, mcq, questions, sources)
    if not template.is_symlink() and not template.exists():
        _write_atomic(out_dir, EXAM_TEMPLATE_NAME, packaged)
    elif keep and log:
        log(keep)
    return _write_atomic(out_dir, EXAM_PROMPT_NAME, text.encode("utf-8"))


def _quoted(path: str) -> str:
    """`path` between quote marks that do not occur in it (single quotes preferred)."""
    for left, right in (("'", "'"), ('"', '"'), ("`", "`")):
        if left not in path:
            return f"{left}{path}{right}"
    return f"'{path}'"


def _instruction(cwd: Path, prompt_file: Path) -> str:
    """The one-line instruction for claude; `prompt_file` must be inside `cwd` (a relative
    path is taken relative to `cwd`) and is given relative to it."""
    cwd = Path(cwd)
    prompt_file = Path(prompt_file)
    if not prompt_file.is_absolute():
        prompt_file = cwd / prompt_file
    rel = Path(os.path.relpath(prompt_file, cwd))
    if rel.parts and rel.parts[0] == "..":
        raise ValueError(f"prompt file {prompt_file} is not inside {cwd}")
    return f"Read the file {_quoted(rel.as_posix())} and follow it exactly."


def _flush() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass


def _model_args(model: str | None) -> list[str]:
    return ["--model", model] if model else []


def launch_claude(cwd: Path, prompt_file: Path, model: str | None = None) -> NoReturn:
    """Replace this process with `claude`, in `cwd`, told to read and follow `prompt_file`.

    `prompt_file` must be inside `cwd` (a relative path is taken relative to `cwd`); Claude
    is given it relative to `cwd`. Raises ToolMissing("claude") if claude is not on PATH.
    """
    cwd = Path(cwd)
    instruction = _instruction(cwd, prompt_file)
    if shutil.which("claude") is None:
        raise ToolMissing("claude")
    _flush()
    try:
        os.chdir(cwd)
    except OSError as e:
        raise LaunchError(f"cannot change into {cwd}: {e.strerror or e}") from e
    try:
        os.execvp("claude", ["claude", *_model_args(model), instruction])
    except FileNotFoundError:
        raise ToolMissing("claude") from None
    except OSError as e:
        raise LaunchError(f"cannot start claude: {e.strerror or e}") from e
    raise AssertionError("os.execvp returned")  # pragma: no cover


# --- exam sessions: claude as a child process, with a Stop hook ----------------------------

HOOK_ACTION = "stop-hook"
HOOK_TIMEOUT = 120  # seconds; validation takes well under one
POLL_SECONDS = 0.2  # how often the wait for claude looks for a signal to pass on
KILL_GRACE = 10     # seconds claude has to exit after SIGTERM/SIGHUP/SIGQUIT is passed on


def hook_program() -> str:
    """This interpreter running this package (`python -P -m notes_cli`), as shell words: the
    hook runs the same code as the `notes exam` that started the session, whatever
    `notes` is on PATH. `-P` (Python 3.11+; 3.12 is required) keeps the hook's working
    folder (the module folder) off `sys.path`, so a `json.py` there cannot shadow the
    standard library and let the hook exit 0."""
    return f"{shlex.quote(sys.executable)} -P -m notes_cli"


def exam_hook_command(config: Config, module: str, snapshot: str) -> str:
    """The shell command the Stop hook runs: `hook_program`, the data root and the module
    given explicitly (it does not depend on the session's directory), and the snapshot of
    `Practice Exams/` taken when the session started (`examsession.Snapshot.hook_arg`)."""
    q = shlex.quote
    module = config.resolve_module(module)
    return (f"{hook_program()} --data {q(str(config.data_root))} exam {HOOK_ACTION} "
            f"{q(module)} --snapshot {q(snapshot)}")


def exam_session_settings(config: Config, module: str, snapshot: str) -> str:
    """Inline `--settings` JSON for claude: one Stop hook running `exam_hook_command`."""
    hook = {"type": "command", "command": exam_hook_command(config, module, snapshot),
            "timeout": HOOK_TIMEOUT}
    return json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}})


class SessionSignals:
    """While active (a `with` block): SIGINT does nothing (Ctrl-C is Claude's, and the
    check after the session must not be cut short), and SIGTERM, SIGHUP and SIGQUIT are recorded
    instead of ending this process; `run_claude_session` passes them on to claude and
    waits for it, so the check still runs. The previous handlers are put back on exit.

    Handlers, not SIG_IGN: an ignored signal stays ignored in the child across exec, a
    handler is reset to the default there."""

    SIGNALS = tuple(s for s in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None),
                                getattr(signal, "SIGHUP", None), getattr(signal, "SIGQUIT", None))
                    if s is not None)

    def __init__(self):
        self.received: list[int] = []
        self._previous: dict[int, object] = {}

    def _handler(self, signum, frame) -> None:
        if signum != signal.SIGINT:
            self.received.append(signum)

    def __enter__(self) -> "SessionSignals":
        for sig in self.SIGNALS:
            try:
                self._previous[sig] = signal.signal(sig, self._handler)
            except (ValueError, OSError):   # not the main thread: leave it as it is
                pass
        return self

    def __exit__(self, *exc) -> None:
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError, TypeError):
                pass
        self._previous.clear()


def _call(callback, *args) -> None:
    """Run a caller's callback; its failure must never stop the wait for claude."""
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        pass


def _wait(proc, signals: SessionSignals, on_tick=None, tick_seconds: float = 5) -> int:
    """Wait for `proc`, passing on each signal `signals` records; kill it if it is still
    running KILL_GRACE seconds after the first one. `on_tick()` is called every
    `tick_seconds` while it runs."""
    passed, kill_at = 0, None
    next_tick = time.monotonic() + tick_seconds
    while True:
        try:
            return proc.wait(timeout=POLL_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= next_tick:
            _call(on_tick)
            next_tick = time.monotonic() + tick_seconds
        while passed < len(signals.received):
            try:
                proc.send_signal(signals.received[passed])
            except OSError:
                pass
            passed += 1
            if kill_at is None:
                kill_at = time.monotonic() + KILL_GRACE
        if kill_at is not None and time.monotonic() >= kill_at:
            try:
                proc.kill()
            except OSError:
                pass
            kill_at = math.inf


def run_claude_session(cwd: Path, prompt_file: Path, settings: str,
                       signals: SessionSignals | None = None, on_start=None, on_tick=None,
                       tick_seconds: float = 5, model: str | None = None) -> int:
    """Run `claude --settings SETTINGS "<read PROMPT_FILE>"` as a child process in `cwd`,
    sharing this terminal (nothing captured), wait for it and return its exit status
    (128 + N if it was killed by signal N).

    `signals` is the caller's active SessionSignals (one is used just for the run if not
    given): SIGINT is swallowed, SIGTERM, SIGHUP and SIGQUIT are passed on to claude.
    `on_start(proc)` is called once claude has started, `on_tick()` every `tick_seconds`
    while it runs (their failures are ignored). Raises ToolMissing("claude") if claude is not on PATH, LaunchError if it cannot start.
    """
    cwd = Path(cwd)
    argv = ["claude", *_model_args(model), "--settings", settings,
            _instruction(cwd, prompt_file)]
    if shutil.which("claude") is None:
        raise ToolMissing("claude")
    _flush()
    if signals is None:
        with SessionSignals() as own:
            return run_claude_session(cwd, prompt_file, settings, own, on_start, on_tick,
                                      tick_seconds, model)
    try:
        proc = subprocess.Popen(argv, cwd=cwd)
    except FileNotFoundError:
        raise ToolMissing("claude") from None
    except OSError as e:
        raise LaunchError(f"cannot start claude: {e.strerror or e}") from e
    _call(on_start, proc)
    code = _wait(proc, signals, on_tick, tick_seconds)
    return 128 - code if code < 0 else code
