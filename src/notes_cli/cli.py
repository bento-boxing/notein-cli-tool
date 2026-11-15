"""Command-line entry point: `notes <command>`.

Every command from the spec is defined here. `flashcards` and `exam` take a module name,
or an action word first (`flashcards check FILE`, `exam validate|merge|stats MODULE`), so a
module cannot be called `check`, `validate`, `merge` or `stats`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from notes_cli import examsession, examtools
from notes_cli.anki import check_file, count_cards
from notes_cli.backup import BackupUnreadable, read_backup
from notes_cli.config import Config, ConfigError, UnknownModule, load_config
from notes_cli.convert import ConvertReport, ManifestError, convert
from notes_cli.convert import one_line as _display
from notes_cli.examtools import EXAM_DIR, ExamDirError, ExamNotFound, check_plain_folder, find_exams
from notes_cli.examtools import merge as exam_merge
from notes_cli.examtools import report as exam_report
from notes_cli.examtools import stats as exam_stats
from notes_cli.examtools import validate as exam_validate
from notes_cli.launch import (HOOK_ACTION, LaunchError, SessionSignals, exam_session_settings,
                              launch_claude, prepare_exam, prepare_flashcards, run_claude_session)
from notes_cli.mapping import Placement, plan
from notes_cli.sync import ToolMissing, sync

EXAM_ACTIONS = ("validate", "merge", "stats", HOOK_ACTION)  # stop-hook: hidden, for claude
FLASHCARD_ACTIONS = ("check",)


def _folders_to_create(config: Config) -> list[Path]:
    subfolders = sorted(set(config.subfolder_map.values()))
    return [config.module_dir(m) / sub for m in config.modules for sub in subfolders]


def cmd_organise(args: argparse.Namespace, config: Config) -> int:
    """Print where every note goes (or why it is skipped) and create the module folders.

    Reads the backup only; writes no PDFs. A missing backup folder raises
    BackupUnreadable, reported by `main`.
    """
    backup = read_backup(config.sync_dir)
    placements = plan(backup, config)

    for folder in _folders_to_create(config):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"notes: cannot create folder {folder}: {e.strerror or e}", file=sys.stderr)
            return 1

    # Grouped by Notein folder, then title, so the listing reads like the app's tree.
    def order(p: Placement) -> tuple:
        return (tuple(t.casefold() for t in backup.folder_path(p.note)),
                p.note.title.strip().casefold(), p.note.id)

    for p in sorted(placements, key=order):
        title = _display(p.note.title)
        if p.dest is not None:
            print(f"{title} -> {p.dest.relative_to(config.data_root).as_posix()}")
        else:
            where = "/".join(_display(t) for t in backup.folder_path(p.note)) or "(top level)"
            print(f"{title} -- skipped: {p.reason} (in {where})")

    for bad in backup.bad:
        print(f"bad entry: {_display(bad.path.name)} -- {_display(bad.reason)}")

    mapped = sum(p.dest is not None for p in placements)
    skipped = len(placements) - mapped
    reasons = Counter(p.reason for p in placements if p.reason is not None)
    by_reason = "".join(f"\n  skipped, {reason}: {n}" for reason, n in sorted(reasons.items()))
    bad = len(backup.bad)
    print(
        f"\n{len(placements)} notes: {mapped} mapped, {skipped} skipped; "
        f"{bad} bad {'entry' if bad == 1 else 'entries'}{by_reason}"
    )
    return 0


def cmd_sync(args: argparse.Namespace, config: Config) -> int:
    """Run rclone (ToolMissing, if it is not installed, is reported by `main`)."""
    code = sync(config)
    if code != 0:
        print(f"notes: rclone exited with code {code}", file=sys.stderr)
    return code if code > 0 else (128 - code if code < 0 else 0)  # < 0: killed by a signal


def cmd_convert(args: argparse.Namespace, config: Config) -> int:
    """Convert new and changed notes. Exit 0 when no note failed, 1 otherwise."""
    report = convert(config, module=getattr(args, "module", None),
                     force=getattr(args, "force", False), log=print)
    _print_report(report, config)
    return 1 if report.failed else 0


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _print_report(report: ConvertReport, config: Config) -> None:
    """Counts, then skipped counts per reason, then one line per failure, warning, orphan
    and bad entry. Skipped notes are counted, not listed (by design most notes are
    skipped; `notes organise` lists every one)."""
    print(
        f"\n{len(report.converted)} converted, {report.unchanged} unchanged, "
        f"{len(report.skipped)} skipped, {len(report.failed)} failed; "
        f"{_plural(len(report.warnings), 'warning')}, {_plural(len(report.orphans), 'orphan')}, "
        f"{_plural(len(report.bad), 'bad entry', 'bad entries')}"
    )
    reasons = Counter(reason for _, reason in report.skipped)
    for reason, n in sorted(reasons.items()):
        print(f"  skipped, {reason}: {n}")
    if reasons:
        print("  ('notes organise' lists every skipped note)")
    for title, reason in report.failed:
        print(f"failed: {_display(title)} -- {_display(reason)}")
    for title, warning in report.warnings:
        print(f"warning: {_display(title)} -- {_display(warning)}")
    for path in report.orphans:
        rel = path.relative_to(config.data_root).as_posix()
        print(f"orphan: {_display(rel)} -- no note is converted to this file any more; left in place")
    for bad in report.bad:
        print(f"bad entry: {_display(bad.path.name)} -- {_display(bad.reason)}")


def cmd_all(args: argparse.Namespace, config: Config) -> int:
    """sync, then organise, then convert; stops at the first step that fails."""
    for step in (cmd_sync, cmd_organise, cmd_convert):
        code = step(args, config)
        if code != 0:
            return code
    return 0


def _usage_error(message: str) -> int:
    print(f"notes: {message}", file=sys.stderr)
    return 2


def cmd_flashcards(args: argparse.Namespace, config: Config) -> int:
    """Write the flashcards prompt into the module folder and replace this process with
    claude there. Returns only on failure: no sources (NoSources) or no claude
    (ToolMissing) are reported by `main` with exit 1; usage errors return 2."""
    if args.module is None:
        return _usage_error("usage: notes flashcards MODULE [--week N] | "
                            "notes flashcards check FILE")
    if args.rest:
        return _usage_error(f"unexpected argument {args.rest[0]!r}; "
                            "usage: notes flashcards MODULE [--week N]")
    if args.week is not None and not 0 <= args.week <= 99:
        return _usage_error(f"--week must be between 0 and 99, not {args.week}")

    prompt = prepare_flashcards(config, args.module, args.week)
    module_dir = config.module_dir(args.module)
    print(f"wrote {prompt.relative_to(config.data_root).as_posix()}")
    launch_claude(module_dir, prompt, config.model)


def cmd_flashcards_check(args: argparse.Namespace) -> int:
    """`notes flashcards check FILE`: print each problem on its own line.

    Exit 0 when the file is importable, 1 when it has problems, 2 for usage errors
    (no file, more than one, --week given, file missing or unreadable). Needs no config.
    """
    if args.week is not None:
        return _usage_error("--week does not apply to 'flashcards check'")
    if len(args.rest) != 1:
        return _usage_error("usage: notes flashcards check FILE")
    path = Path(args.rest[0])
    if path.is_dir():
        return _usage_error(f"{_display(str(path))} is a folder; give the path of one "
                            "'<MODULE> Week NN.txt' file")
    if not path.is_file():
        return _usage_error(f"no such file: {_display(str(path))}")
    try:
        problems = check_file(path)
    except OSError as e:
        return _usage_error(f"cannot read {_display(str(path))}: {e.strerror or e}")

    name = _display(str(path))
    for problem in problems:
        print(problem)
    if problems:
        print(f"{_plural(len(problems), 'problem')} in {name}")
        return 1
    print(f"{name}: no problems found ({_plural(count_cards(path), 'card')})")
    return 0


EXAM_USAGE = ("usage: notes exam MODULE [--mcq PCT] [--questions N] | "
              "notes exam validate|merge MODULE [N|all] | notes exam stats MODULE")


def cmd_exam(args: argparse.Namespace, config: Config) -> int:
    """`notes exam MODULE`: write the exam prompt and template into the module folder, run
    claude there as a child process with a Stop hook that keeps it working until this
    session's exams are valid, then check those exams again and delete the invalid ones
    (`examsession`). `notes exam validate|merge|stats MODULE [N|all]`: run the exam tools on
    `MODULE/Practice Exams/`. No sources or no claude → exit 1 (via `main`); usage errors
    return 2."""
    if args.module is None:
        return _usage_error(EXAM_USAGE)
    if args.snapshot is not None:
        return _usage_error(f"--snapshot applies only to 'exam {HOOK_ACTION}'")
    if args.action is not None:
        return _exam_tool(args, config)
    if args.rest:
        return _usage_error(f"unexpected argument {args.rest[0]!r}; "
                            "usage: notes exam MODULE [--mcq PCT] [--questions N]")
    if args.mcq is not None and not 0 <= args.mcq <= 100:
        return _usage_error(f"--mcq must be a percentage from 0 to 100, not {args.mcq}")
    if args.questions is not None and args.questions < 1:
        return _usage_error(f"--questions must be a positive number, not {args.questions}")

    module = config.resolve_module(args.module)
    module_dir = config.module_dir(module)
    exam_dir = module_dir / EXAM_DIR
    stale = _finish_stale_session(exam_dir, module)
    if stale is None:
        return 1
    for name in examsession.remove_stale_temps(exam_dir):
        _say(f"notes: removed {_display(str(exam_dir / name))}, a temp file left by a 'notes "
             "exam' that is no longer running.")
    prompt = prepare_exam(config, module, args.mcq, args.questions, log=print)
    print(f"wrote {prompt.relative_to(config.data_root).as_posix()}")

    shown = _display(str(examsession.session_path(exam_dir)))
    try:
        session = examsession.new_session(exam_dir, module)
        examsession.write_session(exam_dir, session)
    except FileExistsError:
        _say(f"notes: {shown} appeared just now: another 'notes exam {module}' seems to "
             "have started; no session was started.", err=True)
        return 1
    except (OSError, examtools.FolderChanged) as e:
        why = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        _say(f"notes: cannot write {shown}: {_display(why)}; no session was started "
             "(without it an interrupted session's exams could not be checked).", err=True)
        return 1

    def started(proc) -> None:
        session.claude_pid = proc.pid
        session.claude_start = examsession.process_start(proc.pid)
        examsession.record(exam_dir, session)

    if not session.snapshot.inodes_complete:
        _say(f"notes: warning: {_display(str(module_dir))} has more than "
             f"{examsession.INODE_SCAN_LIMIT} files and folders, too many for notes to record "
             "which files existed before this session; invalid exams written in it will be "
             "reported but not deleted.", err=True)
    settings = exam_session_settings(config, module, session.snapshot.hook_arg())
    with SessionSignals() as signals:
        try:
            code = run_claude_session(module_dir, prompt, settings, signals, on_start=started,
                                      on_tick=lambda: examsession.record(exam_dir, session),
                                      tick_seconds=examsession.RECORD_SECONDS,
                                      model=config.model)
        except (ToolMissing, LaunchError):
            examsession.remove_session(exam_dir)    # claude never ran: nothing to check
            raise
        except Exception as e:      # claude may have run: check what it left
            _say(f"notes: the Claude session failed ({type(e).__name__}: {_display(str(e))})",
                 err=True)
            code = 1
        examsession.record(exam_dir, session)       # right before the check
        result, _ = _check_session(exam_dir, session, code)
    return result if result != 0 else stale


def _say(text: str = "", err: bool = False) -> None:
    """Print one message; never raises (a closed terminal must not stop the check)."""
    try:
        print(text, file=sys.stderr if err else sys.stdout, flush=True)
    except Exception:
        pass


def _when(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "at an unknown time"


def _check_session(exam_dir: Path, session: examsession.Session, claude_code: int,
                   claimed: str | None = None) -> tuple[int, bool]:
    """Run the check after a session, then remove its session file (`claimed`: the name a
    stopped session's file was claimed under). Returns the exit status (claude's when the
    session wrote nothing; else 1 if anything was invalid, deleted, not deletable or
    uncheckable, 0 if not) and whether the check finished (if not, the session file is
    kept so that the next run checks again). Never raises."""
    module = session.module
    shown = _display(str(examsession.session_path(exam_dir)))
    try:
        outcome = examsession.after_session(exam_dir, session, _say, stopped=claimed is not None)
    except Exception as e:
        if claimed is not None:
            examsession.release_claim(exam_dir, session, claimed, finished=False)
        _say(f"notes: the check after the session failed ({type(e).__name__}: "
             f"{_display(str(e))}); {shown} was kept, so the next 'notes exam {module}' run "
             "checks this session's exams again.", err=True)
        return 1, False
    try:
        if claimed is not None:
            problem = examsession.release_claim(exam_dir, session, claimed, finished=True)
            if problem:
                _say(f"notes: cannot remove {_display(str(exam_dir / claimed))}: "
                     f"{_display(problem)}; delete it yourself.", err=True)
        else:
            _remove_session_file(exam_dir, session)
    except Exception as e:
        _say(f"notes: could not remove {shown} ({type(e).__name__}: {_display(str(e))}); "
             "delete it yourself.", err=True)
    if outcome.nothing:
        return claude_code, True
    return (1 if outcome.problems else 0), True


def _remove_session_file(exam_dir: Path, session: examsession.Session) -> None:
    """Remove the session file written by `session`, and only that one."""
    path = _display(str(examsession.session_path(exam_dir)))
    if exam_dir.is_symlink():
        _say(f"notes: {EXAM_DIR} is now a symlink, so {path} was left as it is; delete it "
             "yourself if no 'notes exam' is running.", err=True)
        return
    try:
        current = examsession.read_session(exam_dir, session.module)
    except examsession.UntrustedSession as e:
        _say(f"notes: {path} was changed during the session ({_display(str(e))}); it was left "
             "as it is. Delete it yourself if no 'notes exam' is running.", err=True)
        return
    if current is None or not current.same_owner(session):
        return
    problem = examsession.remove_session(exam_dir)
    if problem:
        _say(f"notes: cannot remove {path}: {_display(problem)}; delete it yourself.", err=True)


def _finish_stale_session(exam_dir: Path, module: str) -> int | None:
    """Before a session starts: a session file left in `exam_dir` means either a session is
    still running (its `notes` or its Claude: refuse, None) or `notes` was stopped before
    it could check that session's exams (claim the file, check them now; returns 1 if
    anything was wrong, else 0). A session file that cannot be trusted is never acted on
    (refuse: None). No file: 0."""
    if exam_dir.is_symlink() or not exam_dir.is_dir():
        return 0            # nothing there (or prepare_exam refuses the folder)
    path = _display(str(examsession.session_path(exam_dir)))
    for name, pid in examsession.claimed_files(exam_dir):
        shown = _display(str(exam_dir / name))
        if examsession.pid_running(pid, None):
            _say(f"notes: another 'notes exam {module}' (pid {pid}) is checking the exams of "
                 "a stopped session; wait for it to finish, then run this again. If you are "
                 f"sure nothing is running, delete {shown} by hand.", err=True)
        else:
            _say(f"notes: {shown} is left from a check of a stopped session that was itself "
                 "stopped; nothing was deleted and no session was started. Check the exams "
                 f"with 'notes exam validate {module}', delete that file by hand, and run "
                 f"'notes exam {module}' again.", err=True)
        return None
    try:
        stale = examsession.read_session(exam_dir, module)
    except examsession.UntrustedSession as e:
        _say(f"notes: {path} cannot be trusted ({_display(str(e))}), so notes cannot tell "
             "which exams its session wrote; nothing was deleted and no session was started. "
             f"If no 'notes exam' is running: check the exams with 'notes exam validate "
             f"{module}', delete that file by hand, and run 'notes exam {module}' again.",
             err=True)
        return None
    if stale is None:
        return 0
    if examsession.pid_running(stale.pid, stale.process_start):
        _say(f"notes: another 'notes exam {module}' session is running (pid {stale.pid}, "
             f"started {_when(stale.started)}); finish it first. If you are sure none is "
             f"running, delete {path} and run this again.", err=True)
        return None
    if stale.claude_pid is not None and examsession.pid_running(stale.claude_pid,
                                                                stale.claude_start):
        _say(f"notes: the Claude session of an earlier 'notes exam {module}' (pid "
             f"{stale.claude_pid}, started {_when(stale.started)}) is still running, although "
             "that notes has stopped; quit that Claude first, then run this again (its exams "
             "are checked then). If you are sure it is not running, delete "
             f"{path} and run this again.", err=True)
        return None
    try:
        claimed = examsession.claim_session(exam_dir, stale)
    except (OSError, examtools.FolderChanged) as e:
        _say(f"notes: cannot take over {path} ({_display(str(e))}); nothing was deleted and "
             "no session was started.", err=True)
        return None
    if claimed is None:
        _say(f"notes: {path} was taken or changed by another 'notes exam {module}' just now; "
             "nothing was deleted and no session was started. Run this again.", err=True)
        return None
    _say(f"notes: the 'notes exam {module}' session started {_when(stale.started)} ended "
         "before notes could check its exams (notes was stopped); checking them now. Only "
         "the files that session recorded as its own can be deleted.")
    with SessionSignals():
        code, finished = _check_session(exam_dir, stale, 0, claimed=claimed)
    if not finished or os.path.lexists(examsession.session_path(exam_dir)) \
            or os.path.lexists(exam_dir / claimed):
        _say(f"notes: no new session was started, because {path} is still there (see above).",
             err=True)
        return None
    return 1 if code else 0


def cmd_exam_stop_hook(args: argparse.Namespace) -> int:
    """`notes exam stop-hook MODULE --snapshot JSON` (hidden): Claude Code's Stop hook for
    an exam session. Exit 0, silent, when every exam written in this session (its number is
    not in the snapshot) is valid and finished and there are no stray exam-like files or
    orphaned answers; exit 2 with the errors on stderr otherwise, which keeps Claude
    working. Exams from earlier sessions never block. When Claude Code says a Stop hook has
    already blocked this stop (`stop_hook_active` true in the event JSON on stdin), stray
    exam-like files alone no longer block: they are reported after the session. Every
    failure of the check itself is exit 2 too (Claude Code treats any other status as "go
    ahead and stop"), except a missing exams folder (exit 0). Writes nothing on stdout and
    no file."""
    try:
        event = _read_hook_event()
        if args.module is None or args.rest or args.snapshot is None:
            _say(f"notes exam {HOOK_ACTION}: usage: notes exam {HOOK_ACTION} MODULE --snapshot "
                 "JSON", err=True)
            return 2
        snapshot = examsession.Snapshot.from_json(json.loads(args.snapshot))
        config = load_config(args.data)
        exam_dir = config.module_dir(config.resolve_module(args.module)) / EXAM_DIR
        if not os.path.lexists(exam_dir):
            return 0
        lines = examsession.hook_problems(exam_dir, snapshot,
                                          strays_block=event.get("stop_hook_active") is not True)
        if not lines:
            return 0
        _say("\n".join(lines), err=True)
        return 2
    except BaseException as e:  # fail closed: a broken check must not let exams through
        _say(f"notes exam {HOOK_ACTION}: the exam check itself failed "
             f"({type(e).__name__}: {_display(str(e))}); finishing is blocked until it works. "
             "Tell the user.", err=True)
        return 2


HOOK_EVENT_LIMIT = 1 << 20


def _read_hook_event() -> dict:
    """The hook's stdin as a JSON object, or {} if it is empty, not JSON, not an object,
    too big, unreadable or a terminal (then nothing in it is believed)."""
    try:
        stream = sys.stdin
        if stream is None or stream.isatty():
            return {}
        text = stream.read(HOOK_EVENT_LIMIT + 1)
        if len(text) > HOOK_EVENT_LIMIT:
            return {}
        data = json.loads(text or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _exam_numbers(rest: list[str]) -> list[int] | str | None:
    """`[]` or `["all"]` → "all"; `["N"]` with N a positive integer → [N]; else None."""
    if not rest:
        return "all"
    if len(rest) == 1:
        if rest[0].lower() == "all":
            return "all"
        if rest[0].isdigit() and rest[0].isascii() and int(rest[0]) > 0:
            return [int(rest[0])]
    return None


def _exam_tool(args: argparse.Namespace, config: Config) -> int:
    """validate / merge / stats. Exit 0 when done (validate: no exam has errors), 1 when an
    exam has errors, the exam or any exam is missing, or a file cannot be read or written,
    2 for usage errors."""
    action = args.action
    if args.mcq is not None or args.questions is not None:
        return _usage_error(f"--mcq and --questions do not apply to 'exam {action}'")
    exam_dir = config.module_dir(args.module) / EXAM_DIR
    shown = _display(exam_dir.relative_to(config.data_root).as_posix())

    if action == "stats":
        if args.rest:
            return _usage_error(f"unexpected argument {args.rest[0]!r}; "
                                "usage: notes exam stats MODULE")
        try:
            print(exam_stats(exam_dir))
        except OSError as e:
            print(f"notes: cannot read {shown}: {e.strerror or e}", file=sys.stderr)
            return 1
        return 0

    numbers = _exam_numbers(args.rest)
    if numbers is None:
        return _usage_error(f"usage: notes exam {action} MODULE [N|all], "
                            "where N is a positive whole number")
    if numbers == "all":
        numbers = find_exams(exam_dir)
        if not numbers:
            print(f"notes: no exams found in {shown} (looked for exam<N>.md files)",
                  file=sys.stderr)
            return 1

    try:
        if action == "validate":
            return _exam_validate(exam_dir, numbers)
        return _exam_merge(exam_dir, numbers)
    except (ExamNotFound, ExamDirError) as e:
        print(f"notes: {_display(str(e))}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"notes: cannot {action} exams in {shown}: {e.strerror or e}", file=sys.stderr)
        return 1


def _exam_validate(exam_dir: Path, numbers: list[int]) -> int:
    checks = []
    for n in numbers:
        checks.append(exam_validate(exam_dir, n))
        print(exam_report(checks[-1]))
        print()
    failed = sum(not check.ok for check in checks)
    print(f"{_plural(len(checks), 'exam')} checked: {len(checks) - failed} passed, "
          f"{failed} with errors")
    return 1 if failed else 0


def _exam_merge(exam_dir: Path, numbers: list[int]) -> int:
    check_plain_folder(exam_dir)
    merged = []
    for n in numbers:
        if not (exam_dir / f"exam{n}.md").is_file():
            raise ExamNotFound(f"exam{n}.md not found in {exam_dir}")
        if not (exam_dir / f"exam{n}_answers.md").is_file():
            print(f"  exam{n}_answers.md not found, using questions only")
        out = exam_merge(exam_dir, n)
        merged.append(out)
        chars = len(out.read_text(encoding="utf-8", errors="surrogateescape"))
        print(f"  exam{n}_full.md created ({chars:,} chars)")
    if shutil.which("pandoc") is None:
        print("pandoc is not installed (not on PATH), so the merged exams cannot be exported "
              "to PDF here; they are ready for it once pandoc is installed")
    else:
        first = merged[0].stem
        line = (f"to export as PDF: cd {shlex.quote(str(exam_dir))} && "
                f"pandoc '{first}.md' -o '{first}.pdf'")
        if len(merged) > 1:
            line += ", and the same for " + ", ".join(p.name for p in merged[1:])
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notes",
        description="File Notein handwritten-note backups into module folders and convert them to PDF.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        help="data root containing notes.toml (default: $NOTES_DATA, then search upwards from cwd)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p = sub.add_parser("sync", help="copy the Notein backup from the rclone remote (never deletes)")
    p.set_defaults(func=lambda a, c: cmd_sync(a, c))

    p = sub.add_parser(
        "organise", help="rebuild the Notein folder tree, print the mapping, create destination folders"
    )
    p.set_defaults(func=lambda a, c: cmd_organise(a, c))

    p = sub.add_parser("convert", help="convert mapped notes to PDF")
    p.add_argument("module", nargs="?", help="only convert this module")
    p.add_argument("--force", action="store_true", help="re-render even if unchanged")
    p.set_defaults(func=lambda a, c: cmd_convert(a, c))

    p = sub.add_parser("all", help="sync, then organise, then convert")
    p.set_defaults(func=lambda a, c: cmd_all(a, c))

    p = sub.add_parser(
        "flashcards",
        help="prepare the flashcard prompt and launch claude in the module folder "
        "(or 'flashcards check FILE')",
    )
    p.add_argument("target", nargs="+", metavar="ARG", help="MODULE, or 'check FILE'")
    p.add_argument("--week", type=int, help="only use sources from this week")
    p.set_defaults(func=lambda a, c: cmd_flashcards(a, c))

    p = sub.add_parser(
        "exam",
        help="prepare the exam prompt and launch claude in the module folder "
        "(or 'exam validate|merge MODULE [N|all]', 'exam stats MODULE')",
    )
    p.add_argument(
        "target", nargs="+", metavar="ARG", help="MODULE, or 'validate|merge MODULE [N|all]', or 'stats MODULE'"
    )
    p.add_argument("--mcq", type=int, metavar="PCT", help="percentage of multiple-choice questions")
    p.add_argument("--questions", type=int, metavar="N", help="questions per exam")
    p.add_argument("--snapshot", help=argparse.SUPPRESS)  # for 'exam stop-hook' only
    p.set_defaults(func=lambda a, c: cmd_exam(a, c))

    return parser


def _split_target(args: argparse.Namespace, actions: tuple[str, ...]) -> None:
    """Split `target` into `action`, `module` and `rest` for flashcards/exam."""
    target = list(args.target)
    args.action = None
    if target[0].lower() in actions:
        args.action = target.pop(0).lower()
    if args.action == "check":
        args.module = None
    else:
        args.module = target.pop(0) if target else None
    args.rest = target


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # --help, or a usage error (argparse already printed)
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 2)

    if args.command == "exam":
        _split_target(args, EXAM_ACTIONS)
    elif args.command == "flashcards":
        _split_target(args, FLASHCARD_ACTIONS)

    if args.command == "exam" and args.action == HOOK_ACTION:
        return cmd_exam_stop_hook(args)  # loads the config itself; every failure is exit 2

    try:
        if args.command == "flashcards" and args.action == "check":
            return cmd_flashcards_check(args)  # reads only the given file; no config needed
        config = load_config(args.data)
        if getattr(args, "module", None) is not None:
            args.module = config.resolve_module(args.module)
        return args.func(args, config)
    except (ConfigError, UnknownModule) as e:
        print(f"notes: {e}", file=sys.stderr)
        return 2
    except (BackupUnreadable, ManifestError, ToolMissing, LaunchError) as e:
        print(f"notes: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nnotes: interrupted; everything finished so far is kept", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
