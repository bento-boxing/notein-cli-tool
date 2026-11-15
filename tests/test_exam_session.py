"""`notes exam MODULE` enforcement (user request 2026-11-15, "I NEVER want invalid exams"):
the Stop hook during the session, and the check (and deletion) after it.

Which exams belong to a session is decided by the snapshot taken at its start (exam numbers
that did not exist then), never by modification times (review 2026-11-15)."""

import io
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import NOTES_TOML
from notes_cli import examsession, examtools, launch
from notes_cli.cli import main
from notes_cli.config import load_config
from test_exam_launch import INSTRUCTION, NOTES, SLIDES, fake_session  # noqa: F401  (fixture)
from test_examtools import answer_key, exam_text
from test_launch import snapshot, touch

GOOD = exam_text(total=20, multi=8, code=4)
BAD = exam_text(total=20, multi=2, code=4)          # 2/20 multi-answer: an error
ANSWERS = answer_key(20)
OLD = 1_700_000_000                                   # an mtime long before any session
FUTURE = 4_000_000_000                                # an mtime after any session
DEAD_PID = 4_194_304                                  # 2**22: Linux's pids are all below it


def put(folder, name, text, mtime=None):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def exam(folder, n, questions=GOOD, answers=ANSWERS, mtime=None, full=False):
    put(folder, f"exam{n}.md", questions, mtime)
    if answers is not None:
        put(folder, f"exam{n}_answers.md", answers, mtime)
    if full:
        put(folder, f"exam{n}_full.md", questions + answers, mtime)


@pytest.fixture
def fm2(cfg):
    root = cfg.module_dir("FM2")
    for name in SLIDES + NOTES:
        touch(root / name)
    return root


@pytest.fixture
def exams(fm2):
    return fm2 / "Practice Exams"


def snap_arg(exams_dir):
    """The hook's --snapshot for `exams_dir` as it is now (a session starting now)."""
    return examsession.take_snapshot(exams_dir).hook_arg()


# --- the Stop hook -------------------------------------------------------------------------


def hook(cfg, capsys, monkeypatch, snap, stdin='{"hook_event_name": "Stop"}'):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main(["--data", str(cfg.data_root), "exam", "stop-hook", "FM2", "--snapshot", snap])
    out, err = capsys.readouterr()
    return code, out, err


def test_hook_no_exams_folder_exits_0(cfg, fm2, capsys, monkeypatch):
    assert hook(cfg, capsys, monkeypatch, snap_arg(fm2 / "Practice Exams")) == (0, "", "")


def test_hook_no_exams_yet_exits_0(cfg, exams, capsys, monkeypatch):
    put(exams, "EXAM_PROMPT.md", "prompt")              # written by notes, not an exam
    put(exams, "concept_matrix.md", "matrix")
    put(exams, examsession.SESSION_FILE, "{}")
    assert hook(cfg, capsys, monkeypatch, snap_arg(exams)) == (0, "", "")


@pytest.mark.parametrize("mtime", [OLD, None, FUTURE])
def test_hook_ignores_earlier_exams_whatever_their_mtime(cfg, exams, capsys, monkeypatch, mtime):
    exam(exams, 1)
    exam(exams, 2, questions=BAD)
    exam(exams, 3, answers=None)
    snap = snap_arg(exams)
    for n in (1, 2, 3):                                   # touched, rewritten, edited later
        exam(exams, n, questions=BAD, answers=None if n == 3 else "TODO", mtime=mtime)
    assert hook(cfg, capsys, monkeypatch, snap) == (0, "", "")


def test_hook_new_valid_exam_exits_0(cfg, exams, capsys, monkeypatch):
    exam(exams, 1, questions=BAD)
    snap = snap_arg(exams)
    exam(exams, 2)
    assert hook(cfg, capsys, monkeypatch, snap) == (0, "", "")


def test_hook_new_invalid_exam_blocks(cfg, exams, capsys, monkeypatch):
    exam(exams, 3, questions=BAD)                         # an earlier session's: not mentioned
    snap = snap_arg(exams)
    exam(exams, 1)
    exam(exams, 2, questions=BAD)

    code, out, err = hook(cfg, capsys, monkeypatch, snap)

    assert code == 2 and out == ""
    assert "exam2.md — 1 error(s):\n  - Multi-answer questions: 2/20" in err
    assert "exam1.md" not in err and "exam3.md" not in err
    assert "Have a subagent fix these exams" in err and "Warnings are not errors" in err
    assert "never one from an earlier session" in err
    assert "wait for it to finish" not in err             # no background-task branch


def test_hook_invalid_exam_with_old_mtime_under_new_number_blocks(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    exam(exams, 4, questions=BAD, mtime=OLD)              # e.g. copied in, mtime kept
    code, _, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and "exam4.md — 1 error(s)" in err


@pytest.mark.parametrize("answers, wanted", [
    (None, "exam4_answers.md is missing"),
    ("  \n", "exam4_answers.md is empty"),
    ("TODO\n", "exam4_answers.md has no answer for questions 1, 2, 3"),
    (answer_key(20, skip=(20,)), "exam4_answers.md has no answer for question 20:"),
    (answer_key(20, audit=""), "exam4_answers.md has no Answer Key Distribution Audit section"),
])
def test_hook_unfinished_answer_key_blocks(cfg, exams, capsys, monkeypatch, answers, wanted):
    snap = snap_arg(exams)
    exam(exams, 4, answers=answers)
    code, out, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and out == "" and wanted in err


def test_hook_exam_folder_named_like_an_exam(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    (exams / "exam3.md").mkdir(parents=True)
    put(exams, "exam3_answers.md", ANSWERS)
    code, _, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and "exam3.md is a folder, not a file" in err
    assert "does not exist" not in err


def test_hook_orphan_answers(cfg, exams, capsys, monkeypatch):
    put(exams, "exam5_answers.md", ANSWERS)               # from an earlier session: fine
    snap = snap_arg(exams)
    assert hook(cfg, capsys, monkeypatch, snap) == (0, "", "")
    put(exams, "exam6_answers.md", ANSWERS)
    code, _, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and "exam6_answers.md exists but exam6.md does not" in err
    assert "exam5_answers.md" not in err


def test_hook_new_answers_for_earlier_exam_do_not_block(cfg, exams, capsys, monkeypatch):
    exam(exams, 1, questions=BAD, answers=None)
    snap = snap_arg(exams)
    put(exams, "exam1_answers.md", ANSWERS)
    assert hook(cfg, capsys, monkeypatch, snap) == (0, "", "")


def test_hook_stray_exam_files(cfg, exams, capsys, monkeypatch):
    put(exams, "Exam9.md", BAD)                           # there before the session: not blocking
    snap = snap_arg(exams)
    assert hook(cfg, capsys, monkeypatch, snap)[0] == 0
    put(exams, "exam03.md", BAD)
    put(exams, "batch1/exam4.md", BAD)
    code, out, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and out == ""
    assert "Practice Exams/exam03.md looks like an exam file but is not named" in err
    assert "Practice Exams/batch1/exam4.md looks like an exam file" in err
    assert "Exam9.md" not in err


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"stop_hook_active": true}',
                                   '{"background_tasks": [{"id": "x"}]}'])
def test_hook_tolerates_any_stdin(cfg, exams, capsys, monkeypatch, stdin):
    snap = snap_arg(exams)
    exam(exams, 1)
    assert hook(cfg, capsys, monkeypatch, snap, stdin=stdin) == (0, "", "")
    exam(exams, 2, questions=BAD)
    code, out, err = hook(cfg, capsys, monkeypatch, snap, stdin=stdin)
    assert code == 2 and "wait for it to finish" not in err


def test_hook_internal_error_fails_closed(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    exam(exams, 1)

    def broken(*args, **kwargs):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(examtools, "validate", broken)
    code, out, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and out == ""
    assert err.count("\n") == 1 and "RuntimeError: validator exploded" in err


def test_hook_failing_stderr_still_exit_2(cfg, exams, monkeypatch):
    class Broken(io.StringIO):
        def write(self, text):
            raise OSError(5, "Input/output error")

    snap = snap_arg(exams)
    exam(exams, 1, questions=BAD)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stderr", Broken())
    assert main(["--data", str(cfg.data_root), "exam", "stop-hook", "FM2", "--snapshot",
                 snap]) == 2


@pytest.mark.parametrize("snap", ["", "not json", "[]", '{"exams": [1]}',
                                  '{"exams": ["1"], "answers": []}',
                                  '{"exams": [0], "answers": []}',
                                  '{"exams": [], "answers": [], "strays": [1]}'])
def test_hook_bad_snapshot_fails_closed(cfg, exams, capsys, monkeypatch, snap):
    code, out, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and out == "" and "the exam check itself failed" in err


def test_hook_config_problem_fails_closed(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    snap = '{"exams":[],"answers":[],"strays":[]}'
    assert main(["--data", str(tmp_path), "exam", "stop-hook", "FM2", "--snapshot", snap]) == 2
    assert main(["exam", "stop-hook", "FM2"]) == 2               # no --snapshot
    assert capsys.readouterr().out == ""


def test_hook_never_writes(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    exam(exams, 1, questions=BAD, full=True)
    exam(exams, 2, answers=None)
    put(exams, "exam03.md", BAD)
    put(exams, "concept_matrix.md", "matrix")
    before = snapshot(cfg.data_root)
    assert hook(cfg, capsys, monkeypatch, snap)[0] == 2
    assert snapshot(cfg.data_root) == before


def test_validate_never_deletes(cfg, exams, capsys):
    exam(exams, 1, questions=BAD, full=True)
    exam(exams, 2, answers=None)
    before = snapshot(cfg.data_root)
    assert main(["--data", str(cfg.data_root), "exam", "validate", "FM2"]) == 1
    assert snapshot(cfg.data_root) == before


def test_stop_hook_is_hidden_and_reserved(cfg, capsys):
    main(["exam", "--help"])
    out = capsys.readouterr().out
    assert "stop-hook" not in out and "--snapshot" not in out
    assert main(["--data", str(cfg.data_root), "exam", "validate", "FM2", "--snapshot", "{}"]) == 2


# --- the launch: argv and settings -----------------------------------------------------------


def test_launch_argv_and_settings(cfg, fm2, exams, fake_session, monkeypatch, tmp_path):
    # A `notes` on PATH or in argv[0] is not used: the hook runs this interpreter with
    # this package, so it always runs the same code (review round 2).
    exe = touch(tmp_path / "bin" / "notes", b"#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(exe)])
    exam(exams, 2)
    put(exams, "exam7_answers.md", ANSWERS)
    put(exams, "Exam9.md", "x")

    assert main(["--data", str(cfg.data_root), "exam", "FM2"]) == 0

    [(argv, kwargs, _)] = fake_session
    assert argv[:2] == ["claude", "--settings"] and argv[3] == INSTRUCTION and len(argv) == 4
    assert kwargs == {"cwd": fm2}                      # terminal shared: nothing captured
    settings = json.loads(argv[2])
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert settings == {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command, "timeout": 120}]}]}}
    words = shlex.split(command)
    assert words[:-1] == [sys.executable, "-P", "-m", "notes_cli", "--data", str(cfg.data_root),
                          "exam", "stop-hook", "FM2", "--snapshot"]
    assert json.loads(words[-1]) == {"exams": [2], "answers": [2, 7], "strays": ["Exam9.md"]}


def test_hook_command_quoting_survives_odd_paths(tmp_path, monkeypatch):
    root = tmp_path / "Sam's notes $HOME `x`" / "data"
    root.mkdir(parents=True)
    (root / "notes.toml").write_text(NOTES_TOML.replace("PAR = 2", "PAR = 2\n\"It's FC 2\" = 2"))
    cfg = load_config(root)
    monkeypatch.setattr(sys, "argv", ["/somewhere/pytest"])
    monkeypatch.setattr(launch.shutil, "which", lambda name, *a, **k: None)
    snap = examsession.Snapshot(strays=frozenset({"it's $x `y`.md"})).hook_arg()

    command = launch.exam_hook_command(cfg, "it's fc 2", snap)

    assert shlex.split(command) == [sys.executable, "-P", "-m", "notes_cli", "--data", str(root),
                                    "exam", "stop-hook", "It's FC 2", "--snapshot", snap]


def test_hook_command_runs_through_a_shell(cfg, exams, monkeypatch):
    """The real thing, minus Claude: the settings' command run by bash, with event JSON on
    stdin, against a bad exam (2) and then a good one (0)."""
    monkeypatch.setattr(sys, "argv", ["/somewhere/pytest"])
    monkeypatch.setattr(launch.shutil, "which", lambda name, *a, **k: None)
    exam(exams, 1, questions=BAD)                         # earlier: never blocks
    command = json.loads(launch.exam_session_settings(cfg, "FM2", snap_arg(exams)))[
        "hooks"]["Stop"][0]["hooks"][0]["command"]
    env = dict(os.environ, PYTHONPATH=str(Path(launch.__file__).parents[1]))
    exam(exams, 2, questions=BAD)

    def run():
        return subprocess.run(["bash", "-c", command], input='{"hook_event_name":"Stop"}',
                              capture_output=True, text=True, cwd=exams.parent, env=env)

    bad = run()
    assert bad.returncode == 2 and bad.stdout == "" and "exam2.md — 1 error(s)" in bad.stderr
    assert "exam1.md" not in bad.stderr
    exam(exams, 2)
    good = run()
    assert (good.returncode, good.stdout, good.stderr) == (0, "", "")


def test_hook_not_fooled_by_json_py_in_module_folder(cfg, exams, monkeypatch):
    """The hook runs with the module folder as its working directory; a `json.py` there
    must not shadow the standard library (which would make the hook exit 0)."""
    monkeypatch.setattr(sys, "argv", ["/somewhere/pytest"])
    monkeypatch.setattr(launch.shutil, "which", lambda name, *a, **k: None)
    command = json.loads(launch.exam_session_settings(cfg, "FM2", snap_arg(exams)))[
        "hooks"]["Stop"][0]["hooks"][0]["command"]
    exam(exams, 2, questions=BAD)
    put(exams.parent, "json.py", "import sys\nsys.exit(0)\n")
    env = dict(os.environ, PYTHONPATH=str(Path(launch.__file__).parents[1]))
    done = subprocess.run(["bash", "-c", command], input='{"hook_event_name":"Stop"}',
                          capture_output=True, text=True, cwd=exams.parent, env=env)
    assert done.returncode == 2 and "exam2.md — 1 error(s)" in done.stderr


def test_signals_swallowed_during_session_and_check_then_restored(cfg, exams, fake_session,
                                                                  monkeypatch):
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP,
                                                signal.SIGQUIT)}
    seen = []
    real = examsession.after_session

    def after(*args, **kwargs):
        seen.append(signal.getsignal(signal.SIGINT))
        return real(*args, **kwargs)

    monkeypatch.setattr(examsession, "after_session", after)
    main(["--data", str(cfg.data_root), "exam", "FM2"])

    during = fake_session[0][2]
    assert signal.getsignal(signal.SIGQUIT) is not None
    assert during not in (before[signal.SIGINT], signal.SIG_IGN, signal.SIG_DFL)
    assert callable(during)
    during(signal.SIGINT, None)                                  # does nothing, raises nothing
    assert seen == [during]                                      # still swallowed in the check
    assert {s: signal.getsignal(s) for s in before} == before


FAKE_CLAUDE = """\
import os, shutil, signal, sys, time
source, ready = os.environ["FAKE_EXAMS"], os.environ["FAKE_READY"]
for name in sorted(os.listdir(source)):
    shutil.copy(os.path.join(source, name), os.path.join("Practice Exams", name))

def stop(signum, frame):
    with open(ready + ".signal", "w") as f:
        f.write(str(signum))
    sys.exit(0)

for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
    signal.signal(sig, stop)
with open(ready, "w") as f:
    f.write("ready")
time.sleep(60)
sys.exit(5)
"""


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT])
def test_signal_to_notes_passed_on_then_checked(cfg, exams, tmp_path, signum):
    """`notes exam` in a subprocess (so a regression cannot kill the test run), with a fake
    claude on PATH that writes a valid and an invalid exam and waits for a signal: the
    signal sent to notes reaches claude, and the check after the session still runs."""
    source = tmp_path / "written"
    exam(source, 1)
    exam(source, 2, questions=BAD)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE)
    fake.chmod(0o755)
    ready = tmp_path / "ready"
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               PYTHONPATH=str(Path(launch.__file__).parents[1]), FAKE_EXAMS=str(source),
               FAKE_READY=str(ready))
    proc = subprocess.Popen([sys.executable, "-m", "notes_cli", "--data", str(cfg.data_root),
                             "exam", "FM2"], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(300):
            if ready.exists() or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert ready.exists(), proc.communicate(timeout=30)[0]
        os.kill(proc.pid, signum)                      # to notes, while "claude" runs
        out, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert (tmp_path / "ready.signal").read_text() == str(int(signum))
    assert proc.returncode == 1, out
    assert not (exams / "exam2.md").exists() and (exams / "exam1.md").exists()
    assert "notes: deleted exam2.md and exam2_answers.md" in out
    assert not (exams / examsession.SESSION_FILE).exists()
    assert not [n for n in os.listdir(exams) if n.startswith(".notes-exam-session")]


def test_claude_killed_if_it_ignores_sigterm(cfg, exams, fake_session, capsys, monkeypatch):
    monkeypatch.setattr(launch, "KILL_GRACE", 0)

    def during():  # what a SIGTERM to notes does, without sending one to the test run
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    fake_session.during = during
    fake_session.until_signal = "kill"
    main(["--data", str(cfg.data_root), "exam", "FM2"])
    assert fake_session.signals == [signal.SIGTERM, "kill"]


def test_missing_claude_one_line_prompt_written(cfg, fm2, fake_session, capsys):
    fake_session.installed = False
    assert main(["--data", str(cfg.data_root), "exam", "FM2"]) == 1
    assert capsys.readouterr().err == "notes: claude is not installed or not on PATH\n"
    assert fake_session == []
    assert (fm2 / "Practice Exams" / "EXAM_PROMPT.md").is_file()
    assert not (fm2 / "Practice Exams" / examsession.SESSION_FILE).exists()


def test_flashcards_launch_unchanged(cfg, fm2, monkeypatch):
    calls = []
    monkeypatch.setattr(launch.shutil, "which", lambda name, *a, **k: "/fake/bin/claude")
    monkeypatch.setattr(os, "chdir", lambda path: calls.append(("chdir", path)))

    def execvp(file, argv):
        calls.append(("execvp", argv))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvp", execvp)
    with pytest.raises(SystemExit):
        main(["--data", str(cfg.data_root), "flashcards", "FM2"])
    assert calls[-1][1][0] == "claude" and "--settings" not in calls[-1][1]


# --- after the session ---------------------------------------------------------------------


def session(cfg, fake_session, capsys, during, code=0):
    """Run `notes exam FM2` with a fake claude that calls `during()` and exits `code`."""
    fake_session.during = during
    fake_session.code = code
    result = main(["--data", str(cfg.data_root), "exam", "FM2"])
    out, err = capsys.readouterr()
    return result, out + err


def test_after_session_all_valid(cfg, exams, fake_session, capsys):
    rc, out = session(cfg, fake_session, capsys, lambda: (exam(exams, 1), exam(exams, 2)))
    assert rc == 0
    assert out.rstrip().endswith("notes: 2 exams written in this session, all valid.")
    assert "exam1.md — All checks passed" in out


def test_after_session_valid_even_if_claude_failed(cfg, exams, fake_session, capsys):
    rc, _ = session(cfg, fake_session, capsys, lambda: exam(exams, 1), code=-signal.SIGTERM)
    assert rc == 0


def test_after_session_no_exams_returns_claude_status(cfg, exams, fake_session, capsys):
    rc, out = session(cfg, fake_session, capsys, None, code=3)
    assert rc == 3 and out.rstrip().endswith("notes: no exam was written in this session.")
    rc, _ = session(cfg, fake_session, capsys, None, code=-signal.SIGINT)
    assert rc == 130


def test_after_session_deletes_invalid_exam_and_nothing_else(cfg, exams, fake_session, capsys):
    exam(exams, 1)
    put(exams, "concept_matrix.md", "matrix")

    def during():
        exam(exams, 2)
        exam(exams, 3, questions=BAD, full=True)
        put(exams, "exam3_notes.md", "mine")
        put(exams, "exam03.md", BAD)
        put(exams, "concept_matrix.md", "matrix v2")

    before = {}

    def during_and_snapshot():
        during()
        before.update(snapshot(cfg.data_root))

    rc, out = session(cfg, fake_session, capsys, during_and_snapshot)

    assert rc == 1
    after = snapshot(cfg.data_root)
    gone = {os.path.relpath(exams / f"exam3{s}.md", cfg.data_root) for s in ("", "_answers", "_full")}
    gone.add(os.path.relpath(exams / examsession.SESSION_FILE, cfg.data_root))
    assert set(before) - set(after) == gone
    assert all(after[k] == before[k] for k in after)
    assert ("notes: deleted exam3.md, exam3_answers.md and exam3_full.md: not valid when the "
            "session ended (1 error: Multi-answer questions: 2/20") in out
    assert ("notes: warning: Practice Exams/exam03.md looks like an exam but is not named "
            "exam<N>.md directly in Practice Exams/, so it was not checked; left in place.") in out
    assert ("notes: concept_matrix.md was left as it is and may still mention the deleted exam 3; "
            "the next 'notes exam FM2' run will have Claude remove them from it.") in out
    assert out.rstrip().endswith("notes: 1 exam written in this session is valid; 1 invalid exam "
                                 "was deleted. Run 'notes exam FM2' again to regenerate it.")


@pytest.mark.parametrize("answers, wanted", [
    (None, "exam3_answers.md is missing"),
    ("TODO\n", "exam3_answers.md has no answer for questions 1, 2"),
    (answer_key(20, audit=""), "no Answer Key Distribution Audit"),
])
def test_after_session_deletes_unfinished_exam(cfg, exams, fake_session, capsys, answers, wanted):
    rc, out = session(cfg, fake_session, capsys,
                      lambda: (exam(exams, 1), exam(exams, 2), exam(exams, 3, answers=answers)))
    assert rc == 1
    assert not (exams / "exam3.md").exists() and not (exams / "exam3_answers.md").exists()
    assert (exams / "exam2.md").exists()
    assert f"not valid when the session ended (" in out and wanted in out
    assert "2 exams written in this session are valid; 1 invalid exam was deleted" in out


@pytest.mark.parametrize("change", ["touched", "future", "hand-edited", "rewritten", "answers"])
def test_after_session_earlier_exams_never_deleted(cfg, exams, fake_session, capsys, change):
    exam(exams, 1, full=True)
    exam(exams, 2, questions=BAD, mtime=OLD)            # invalid already, unchanged
    files = {name: (exams / name).read_bytes() for name in os.listdir(exams)
             if name.startswith("exam")}

    def during():
        path = exams / "exam1.md"
        if change == "touched":
            os.utime(path)
        elif change == "future":
            os.utime(path, (FUTURE, FUTURE))
        elif change == "hand-edited":
            path.write_text(BAD)
        elif change == "rewritten":
            path.unlink()
            (exams / "exam1_answers.md").unlink()
            exam(exams, 1, questions=BAD, answers=None)
        elif change == "answers":
            put(exams, "exam1_answers.md", "TODO")

    rc, out = session(cfg, fake_session, capsys, during)

    for n in (1, 2):
        assert (exams / f"exam{n}.md").exists()
    assert (exams / "exam1_full.md").read_bytes() == files["exam1_full.md"]
    assert (exams / "exam2_answers.md").read_bytes() == files["exam2_answers.md"]
    assert "deleted" not in out.replace("nothing was deleted", "")
    assert "notes: notice: exams from earlier sessions that are not valid" in out
    assert str(exams / "exam2.md") in out
    if change in ("touched", "future"):
        assert rc == 0 and "changed in this session" not in out
        assert "no exam was written in this session" in out
    else:
        assert rc == 1
        assert "(exam 1 is from an earlier session, so nothing was deleted)" in out
        assert "it is NOT valid now" in out or "no longer exists" in out


def test_after_session_catches_invalid_copy_with_old_mtime(cfg, exams, fake_session, capsys):
    rc, out = session(cfg, fake_session, capsys,
                      lambda: exam(exams, 5, questions=BAD, mtime=OLD))
    assert rc == 1 and not (exams / "exam5.md").exists()
    assert "notes: deleted exam5.md and exam5_answers.md" in out


def test_after_session_earlier_answers_file_kept(cfg, exams, fake_session, capsys):
    """exam4_answers.md was there before (no exam4.md): exam 4 is new, but only its new
    questions file is deleted."""
    put(exams, "exam4_answers.md", "old answers")
    rc, out = session(cfg, fake_session, capsys, lambda: put(exams, "exam4.md", BAD))
    assert rc == 1
    assert not (exams / "exam4.md").exists()
    assert (exams / "exam4_answers.md").read_text() == "old answers"
    assert ("notes: deleted exam4.md: not valid when the session ended" in out
            and "exam4_answers.md was there before this session and left in place" in out)


def test_after_session_symlinked_exam_not_deleted(cfg, exams, fake_session, capsys, tmp_path):
    target = put(tmp_path / "elsewhere", "bad.md", BAD)

    def during():
        exams.mkdir(exist_ok=True)
        (exams / "exam3.md").symlink_to(target)
        put(exams, "exam3_answers.md", ANSWERS)

    rc, out = session(cfg, fake_session, capsys, during)
    assert rc == 1
    assert (exams / "exam3.md").is_symlink() and (exams / "exam3_answers.md").exists()
    assert target.read_text() == BAD
    assert "exam3.md is NOT valid" in out and "was NOT deleted: exam3.md is a symlink" in out


def test_after_session_symlinked_folder_nothing_deleted(cfg, exams, fake_session, capsys, tmp_path):
    real = tmp_path / "elsewhere"

    def during():
        exam(real, 3, questions=BAD)
        os.rename(exams, tmp_path / "moved")
        exams.symlink_to(real)

    rc, out = session(cfg, fake_session, capsys, during)
    assert rc == 1
    assert sorted(p.name for p in real.iterdir()) == ["exam3.md", "exam3_answers.md"]
    assert "was NOT deleted: Practice Exams is a symlink" in out


def test_after_session_delete_failure_does_not_stop_others(cfg, exams, fake_session, capsys,
                                                         monkeypatch):
    real_unlink = os.unlink

    def refuse(path, *args, **kwargs):
        if os.fsdecode(path).rsplit("/", 1)[-1].startswith("exam3"):
            raise PermissionError(13, "Permission denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse)
    rc, out = session(cfg, fake_session, capsys,
                      lambda: (exam(exams, 3, questions=BAD), exam(exams, 4, questions=BAD)))
    assert rc == 1
    line = next(line for line in out.splitlines() if "exam3.md is NOT valid" in line)
    assert "was NOT deleted: cannot delete" in line and "Permission denied" in line
    assert (exams / "exam3.md").exists() and not (exams / "exam4.md").exists()
    assert out.rstrip().endswith(
        "notes: 0 exams written in this session are valid; 1 invalid exam was deleted; 1 "
        "invalid exam could not be deleted and was left in place (see above). Run 'notes exam "
        "FM2' again to regenerate it.")


def test_after_session_summary_counts_invalid_exams_left_in_place(cfg, exams, fake_session,
                                                                 capsys, tmp_path):
    real = tmp_path / "elsewhere"

    def during():
        exam(real, 3, questions=BAD)
        exam(real, 4, questions=BAD)
        os.rename(exams, tmp_path / "moved")
        exams.symlink_to(real)

    rc, out = session(cfg, fake_session, capsys, during)
    assert rc == 1
    assert ("notes: 0 exams written in this session are valid; 2 invalid exams could not be "
            "deleted and were left in place (see above).\n") in out


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything")
def test_after_session_unreadable_exam_does_not_stop_others(cfg, exams, fake_session, capsys):
    def during():
        exam(exams, 3)
        (exams / "exam3.md").chmod(0)
        exam(exams, 4, questions=BAD)
        exam(exams, 5)

    rc, out = session(cfg, fake_session, capsys, during)
    assert rc == 1
    # Review round 2: an exam that cannot be read cannot be checked, so it is not deleted.
    assert ("notes: exam 3 could not be checked (1 error: exam3.md cannot be read: Permission "
            "denied), so it was left in place") in out
    assert (exams / "exam3.md").exists() and not (exams / "exam4.md").exists()
    assert (exams / "exam5.md").exists()
    (exams / "exam3.md").chmod(0o644)


def test_after_session_check_crash_on_one_exam(cfg, exams, fake_session, capsys, monkeypatch):
    real = examtools.finished_check

    def check(exam_dir, n):
        if n == 3:
            raise RuntimeError("boom")
        return real(exam_dir, n)

    monkeypatch.setattr(examtools, "finished_check", check)
    rc, out = session(cfg, fake_session, capsys,
                      lambda: (exam(exams, 3), exam(exams, 4, questions=BAD)))
    assert rc == 1
    assert "notes: exam3.md could not be checked (RuntimeError: boom); it was left in place" in out
    assert (exams / "exam3.md").exists() and not (exams / "exam4.md").exists()


def test_after_session_broken_stdout_still_deletes(cfg, exams, fake_session, monkeypatch):
    class Broken(io.StringIO):
        def write(self, text):
            raise OSError(5, "Input/output error")

    def during():
        exam(exams, 3, questions=BAD)
        monkeypatch.setattr(sys, "stdout", Broken())      # e.g. the terminal was closed
        monkeypatch.setattr(sys, "stderr", Broken())

    fake_session.during = during
    assert main(["--data", str(cfg.data_root), "exam", "FM2"]) == 1
    assert not (exams / "exam3.md").exists()
    assert not (exams / examsession.SESSION_FILE).exists()


def test_after_session_whole_check_crash_keeps_session_file(cfg, exams, fake_session, capsys,
                                                           monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(examsession, "after_session", boom)
    rc, out = session(cfg, fake_session, capsys, lambda: exam(exams, 3, questions=BAD))
    assert rc == 1
    assert "notes: the check after the session failed (RuntimeError: check exploded)" in out
    assert "Traceback" not in out
    assert (exams / examsession.SESSION_FILE).is_file()


def test_after_session_orphan_answers_left_and_reported(cfg, exams, fake_session, capsys):
    rc, out = session(cfg, fake_session, capsys,
                      lambda: (exam(exams, 1), put(exams, "exam7_answers.md", ANSWERS)))
    assert rc == 1 and (exams / "exam7_answers.md").exists()
    assert "exam7_answers.md was written in this session but there is no exam7.md; left in place" in out


# --- the session file ----------------------------------------------------------------------


def test_session_file_during_session_ignored_by_tools(cfg, exams, fake_session, capsys):
    exam(exams, 1, questions=BAD)
    seen = {}

    def during():
        path = exams / examsession.SESSION_FILE
        seen["data"] = json.loads(path.read_text())
        exam(exams, 2)
        seen["stats"] = main(["--data", str(cfg.data_root), "exam", "stats", "FM2"])
        seen["merge"] = main(["--data", str(cfg.data_root), "exam", "merge", "FM2", "2"])
        seen["out"] = capsys.readouterr().out

    rc, _ = session(cfg, fake_session, capsys, during)
    data = seen["data"]
    assert data["tool"] == "notes exam" and data["pid"] == os.getpid() and data["module"] == "FM2"
    assert data["snapshot"]["exams"] == [1] and data["snapshot"]["answers"] == [1]
    assert set(data["snapshot"]["fingerprints"]) == {"exam1.md", "exam1_answers.md"}
    assert "Found 1 exam(s): [1]" not in seen["out"] and "Found 2 exam(s): [1, 2]" in seen["out"]
    assert (seen["stats"], seen["merge"]) == (0, 0)
    assert rc == 0 and (exams / "exam2_full.md").exists()
    assert not (exams / examsession.SESSION_FILE).exists()


def write_stale(exams_dir, pid=DEAD_PID, exams_before=(), answers_before=(), recorded=None,
                claude=None, **extra):
    """A session file as left by a `notes exam FM2` that was killed: the exams in
    `exams_before` existed when it started; `recorded` (default: the exam files there now)
    is what it last recorded as its own."""
    exams_dir.mkdir(parents=True, exist_ok=True)
    s = examsession.new_session(exams_dir, "FM2")
    before = {f"exam{n}{k}.md" for n in exams_before for k in ("", "_answers", "_full")}
    inodes = {(st.st_dev, st.st_ino) for name in before
              if (st := os.lstat(exams_dir / name) if (exams_dir / name).exists() else None)}
    s.pid, s.started, s.process_start = pid, OLD, None
    s.snapshot = examsession.Snapshot(frozenset(exams_before), frozenset(answers_before),
                                      inodes=frozenset(inodes))
    if claude is not None:
        s.claude_pid, s.claude_start = claude
    s.recorded = examsession.record_new_files(exams_dir, s) if recorded is None else recorded
    data = dict(s.to_json(), **extra)
    (exams_dir / examsession.SESSION_FILE).write_text(json.dumps(data))
    return s


def test_second_session_refused_while_first_runs(cfg, exams, fake_session, capsys):
    exam(exams, 3, questions=BAD)
    write_stale(exams, pid=os.getppid())              # a live process
    put(exams, "EXAM_PROMPT.md", "the running session's brief")
    before = snapshot(cfg.data_root)

    rc = main(["--data", str(cfg.data_root), "exam", "FM2"])

    err = capsys.readouterr().err
    assert rc == 1 and fake_session == []
    assert err.count("\n") == 1
    assert f"another 'notes exam FM2' session is running (pid {os.getppid()}" in err
    assert "If you are sure none is running, delete" in err
    assert snapshot(cfg.data_root) == before


def test_stale_session_checked_before_new_one(cfg, exams, fake_session, capsys):
    exam(exams, 1, questions=BAD)                     # before the crashed session
    exam(exams, 2, questions=BAD, full=True)          # written by it, never checked
    exam(exams, 3)
    write_stale(exams, exams_before=[1], answers_before=[1])

    rc, out = session(cfg, fake_session, capsys, lambda: exam(exams, 4))

    assert "ended before notes could check its exams (notes was stopped); checking them now." in out
    assert "notes: deleted exam2.md, exam2_answers.md and exam2_full.md" in out
    assert (exams / "exam1.md").exists() and (exams / "exam3.md").exists()
    assert (exams / "exam4.md").exists() and len(fake_session) == 1
    # The new session's brief was written after the clean-up, so it does not list exam 2.
    assert "exams 1, 3 and 4" not in out
    prompt = (exams / "EXAM_PROMPT.md").read_text()
    assert "already holds exams 1 and 3 from an earlier session" in prompt
    assert rc == 1                                    # the stale session had an invalid exam
    assert not (exams / examsession.SESSION_FILE).exists()


def test_stale_session_all_valid_then_new_session(cfg, exams, fake_session, capsys):
    exam(exams, 2)
    write_stale(exams)
    rc, out = session(cfg, fake_session, capsys, lambda: exam(exams, 3))
    assert rc == 0 and "1 exam written in this session, all valid." in out
    assert len(fake_session) == 1


@pytest.mark.parametrize("kind", ["symlink", "folder", "not json", "wrong tool", "bad pid",
                                  "huge pid", "pid above pid_max", "huge claude pid",
                                  "bad snapshot", "string numbers"])
def test_untrusted_session_file_refused_nothing_deleted(cfg, exams, fake_session, capsys,
                                                        tmp_path, kind):
    exam(exams, 2, questions=BAD)
    path = exams / examsession.SESSION_FILE
    if kind == "symlink":
        write_stale(tmp_path / "elsewhere")
        path.symlink_to(tmp_path / "elsewhere" / examsession.SESSION_FILE)
    elif kind == "folder":
        path.mkdir()
    elif kind == "not json":
        path.write_text("{")
    elif kind == "wrong tool":
        write_stale(exams, tool="something else")
    elif kind == "bad pid":
        write_stale(exams, pid=-1)
    elif kind == "huge pid":
        write_stale(exams, pid=2 ** 70)                     # os.kill would raise OverflowError
    elif kind == "pid above pid_max":
        write_stale(exams, pid=2 ** 22 + 1)
    elif kind == "huge claude pid":
        write_stale(exams, claude=(2 ** 70, None))
    elif kind == "bad snapshot":
        write_stale(exams, snapshot=[1, 2])
    else:
        write_stale(exams, snapshot={"exams": ["1"], "answers": []})
    before = snapshot(cfg.data_root)

    rc = main(["--data", str(cfg.data_root), "exam", "FM2"])

    err = capsys.readouterr().err
    assert rc == 1 and fake_session == []
    assert "cannot be trusted" in err and "nothing was deleted and no session was started" in err
    assert err.count("\n") == 1 and "Traceback" not in err
    assert snapshot(cfg.data_root) == before


def test_stale_check_crash_refuses_new_session(cfg, exams, fake_session, capsys, monkeypatch):
    write_stale(exams)
    monkeypatch.setattr(examsession, "after_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rc, out = session(cfg, fake_session, capsys, None)
    assert rc == 1 and fake_session == []
    assert "no new session was started" in out
    assert (exams / examsession.SESSION_FILE).exists()


def test_session_file_cannot_be_written(cfg, exams, fake_session, capsys, monkeypatch):
    def refuse(*args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(examsession, "write_session", refuse)
    rc = main(["--data", str(cfg.data_root), "exam", "FM2"])
    assert rc == 1 and fake_session == []
    assert "no session was started" in capsys.readouterr().err


def test_pid_running():
    assert examsession.pid_running(os.getppid(), None)
    assert examsession.pid_running(os.getppid(), examsession.process_start(os.getppid()))
    assert not examsession.pid_running(DEAD_PID, None)
    assert not examsession.pid_running(2 ** 70, None)                # no OverflowError
    assert not examsession.pid_running(os.getpid(), None)         # not another session
    if examsession.process_start(os.getppid()) is not None:       # pid reused: not ours
        assert not examsession.pid_running(os.getppid(), "1")


def test_write_session_is_exclusive(tmp_path):
    s = examsession.new_session(tmp_path, "FM2")
    examsession.write_session(tmp_path, s)
    with pytest.raises(FileExistsError):
        examsession.write_session(tmp_path, s)
    assert examsession.read_session(tmp_path, "FM2") == s
    assert not [n for n in os.listdir(tmp_path) if n != examsession.SESSION_FILE]


# --- examtools pieces ----------------------------------------------------------------------


def test_delete_exam_only_its_three_files(tmp_path):
    exam(tmp_path, 3, full=True)
    for name in ("exam3_notes.md", "exam03.md", "exam30.md", "exam13.md", "concept_matrix.md",
                 examsession.SESSION_FILE):
        put(tmp_path, name, "x")
    deleted, problem = examtools.delete_exam(tmp_path, 3)
    assert problem is None
    assert sorted(p.name for p in deleted) == ["exam3.md", "exam3_answers.md", "exam3_full.md"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        examsession.SESSION_FILE, "concept_matrix.md", "exam03.md", "exam13.md", "exam30.md",
        "exam3_notes.md"]


def test_delete_exam_keeps_named_parts(tmp_path):
    exam(tmp_path, 3, full=True)
    deleted, problem = examtools.delete_exam(tmp_path, 3, keep=("answers",))
    assert problem is None and [p.name for p in deleted] == ["exam3.md", "exam3_full.md"]
    assert (tmp_path / "exam3_answers.md").exists()


def test_delete_exam_refuses_directory(tmp_path):
    exam(tmp_path, 3)
    (tmp_path / "exam3_full.md").mkdir()
    assert examtools.delete_exam(tmp_path, 3) == ([], "exam3_full.md is not a regular file")
    assert (tmp_path / "exam3.md").exists()


@pytest.mark.parametrize("stray, name", [
    ("Exam3.md", "exam3.md"), ("exam03.md", "exam3.md"), ("batch1/exam12_answers.md",
                                                          "exam12_answers.md"),
    ("practice exam 4 key.md", "exam4_answers.md"), ("exam0.md", "examN.md"),
])
def test_proper_name(stray, name):
    assert examsession.proper_name(stray) == name


# --- review round 2 --------------------------------------------------------------------------


def no_temp_files(exams_dir):
    return not [n for n in os.listdir(exams_dir)
                if n.startswith(".notes-exam-session") and n != examsession.SESSION_FILE]


@pytest.mark.parametrize("forge", ["copied", "module rewritten", "folder path rewritten"])
def test_session_file_from_another_module_deletes_nothing(cfg, exams, fake_session, capsys,
                                                         forge):
    exam(exams, 2, questions=BAD)
    write_stale(exams)                                     # FM2's, recording exam 2
    par = cfg.module_dir("PAR") / "Practice Exams"
    exam(par, 2, questions=BAD)
    data = json.loads((exams / examsession.SESSION_FILE).read_text())
    if forge == "module rewritten":
        data["module"] = "PAR"
    elif forge == "folder path rewritten":
        data["module"] = "PAR"
        data["folder"]["path"] = os.path.realpath(par)
    (par / examsession.SESSION_FILE).write_text(json.dumps(data))
    before = snapshot(cfg.data_root)

    rc = main(["--data", str(cfg.data_root), "exam", "PAR"])

    err = capsys.readouterr().err
    assert rc == 1 and fake_session == []
    assert err.count("\n") == 1 and "cannot be trusted" in err
    assert "written for" in err and "delete that file by hand" in err
    assert snapshot(cfg.data_root) == before


def test_stale_recovery_keeps_exam_written_after_it(cfg, exams, fake_session, capsys):
    exam(exams, 2, questions=BAD)
    write_stale(exams)                                     # recorded: exam 2
    exam(exams, 5, questions=BAD)                          # by hand, after notes was killed

    rc, out = session(cfg, fake_session, capsys, None)

    assert rc == 1
    assert not (exams / "exam2.md").exists()               # recorded and unchanged: deleted
    assert (exams / "exam5.md").read_text() == BAD and (exams / "exam5_answers.md").exists()
    assert "exam5.md is NOT valid" in out and "is not the file notes recorded" in out
    assert "notes: deleted exam2.md and exam2_answers.md" in out


def test_stale_recovery_reports_recorded_exam_changed_since(cfg, exams, fake_session, capsys):
    exam(exams, 2, questions=BAD)
    write_stale(exams)
    with open(exams / "exam2.md", "a") as f:               # edited after notes was killed
        f.write("\nmy notes\n")

    rc, out = session(cfg, fake_session, capsys, None)

    assert rc == 1 and (exams / "exam2.md").read_text().endswith("my notes\n")
    assert (exams / "exam2_answers.md").exists()
    assert "exam2.md is NOT valid" in out and "was NOT deleted: exam2.md is not the file" in out


def test_stale_recovery_nothing_recorded_nothing_deleted(cfg, exams, fake_session, capsys):
    exam(exams, 2, questions=BAD)
    write_stale(exams, recorded={})                        # killed before its first record
    rc, out = session(cfg, fake_session, capsys, None)
    assert rc == 1 and (exams / "exam2.md").exists()


def test_earlier_exam_renamed_to_new_number_survives(cfg, exams, fake_session, capsys):
    exam(exams, 3, questions=BAD)                          # from an earlier session
    content = {n: (exams / n).read_bytes() for n in ("exam3.md", "exam3_answers.md")}

    def during():
        os.rename(exams / "exam3.md", exams / "exam13.md")
        os.rename(exams / "exam3_answers.md", exams / "exam13_answers.md")

    rc, out = session(cfg, fake_session, capsys, during)

    assert rc == 1
    assert (exams / "exam13.md").read_bytes() == content["exam3.md"]
    assert (exams / "exam13_answers.md").read_bytes() == content["exam3_answers.md"]
    assert "exam13.md is NOT valid" in out
    assert "was already in the module folder or Practice Exams/ when the session started" in out
    assert "notes: deleted" not in out


def test_module_file_moved_into_exams_survives(cfg, exams, fm2, fake_session, capsys):
    exams.mkdir(exist_ok=True)
    put(fm2, "exam5.md", BAD)                               # in the module folder, not an exam
    content = (fm2 / "exam5.md").read_bytes()

    rc, out = session(cfg, fake_session, capsys,
                      lambda: os.rename(fm2 / "exam5.md", exams / "exam5.md"))

    assert rc == 1
    assert (exams / "exam5.md").read_bytes() == content
    assert "exam5.md is NOT valid" in out and "was NOT deleted" in out
    assert "already in the module folder" in out and "notes: deleted" not in out


def test_snapshot_records_module_files_compactly(fm2, exams):
    exams.mkdir(exist_ok=True)
    hook_before = examsession.take_snapshot(exams).hook_arg()
    for k in range(300):
        put(fm2, f"Week {k % 12 + 1}/slide{k}.md", "x")
    snap = examsession.take_snapshot(exams)
    st = os.lstat(fm2 / "Week 1" / "slide0.md")
    assert (st.st_dev, st.st_ino) in snap.inodes and snap.inodes_complete
    assert snap.hook_arg() == hook_before                  # the hook gets no inodes
    s = examsession.new_session(exams, "FM2")
    assert len(examsession._encode(s)) < 20_000            # ~25 bytes per file


def test_too_many_module_files_disables_deletion(cfg, exams, fm2, fake_session, capsys,
                                                 monkeypatch):
    monkeypatch.setattr(examsession, "INODE_SCAN_LIMIT", 2)
    rc, out = session(cfg, fake_session, capsys, lambda: (exam(exams, 3, questions=BAD),
                                                          exam(exams, 4)))
    assert rc == 1 and (exams / "exam3.md").exists() and (exams / "exam3_answers.md").exists()
    assert "too many for notes to record which files existed before this session" in out
    assert ("exam3.md is NOT valid (1 error: Multi-answer questions: 2/20 = 10% of the "
            "multiple-choice questions (need >=40%)) and was NOT deleted: the module folder had "
            "too many files (over 2)") in out
    assert "1 invalid exam could not be deleted and was left in place" in out


def test_session_file_without_inodes_complete_deletes_nothing(cfg, exams, fake_session, capsys):
    exam(exams, 2, questions=BAD)
    s = write_stale(exams)
    data = json.loads((exams / examsession.SESSION_FILE).read_text())
    del data["snapshot"]["inodes_complete"]
    (exams / examsession.SESSION_FILE).write_text(json.dumps(data))
    assert not examsession.read_session(exams, "FM2").snapshot.inodes_complete
    assert s.snapshot.inodes_complete
    rc, out = session(cfg, fake_session, capsys, None)
    assert rc == 1 and (exams / "exam2.md").exists() and "was NOT deleted" in out


def test_earlier_answers_hard_linked_into_new_exam_survive(cfg, exams, fake_session, capsys):
    put(exams, "notes.md", "my own file, not an exam")

    def during():
        put(exams, "exam4.md", BAD)
        os.link(exams / "notes.md", exams / "exam4_answers.md")

    rc, out = session(cfg, fake_session, capsys, during)
    assert rc == 1 and (exams / "exam4.md").exists() and (exams / "notes.md").exists()


def test_delete_exam_uses_exact_names_from_the_listing(tmp_path, monkeypatch):
    """On a case-insensitive file system "exam3_answers.md" would also name
    "Exam3_answers.md". Simulated: stat and unlink of a name fold case, as there."""
    exam(tmp_path, 3, answers=None)
    put(tmp_path, "Exam3_answers.md", "a stray, not exam 3's")
    real_stat, real_unlink = os.stat, os.unlink

    def folded(name, dir_fd):
        for real in os.listdir(dir_fd):
            if real.casefold() == os.fsdecode(name).casefold():
                return real
        return name

    monkeypatch.setattr(os, "stat", lambda name, *a, dir_fd=None, **k: real_stat(
        folded(name, dir_fd) if dir_fd is not None else name, *a, dir_fd=dir_fd, **k))
    monkeypatch.setattr(os, "unlink", lambda name, *a, dir_fd=None, **k: real_unlink(
        folded(name, dir_fd) if dir_fd is not None else name, *a, dir_fd=dir_fd, **k))
    monkeypatch.setattr(os.path, "lexists", lambda path: os.path.basename(path).casefold() in {
        n.casefold() for n in os.listdir(os.path.dirname(path) or ".")})

    deleted, problem = examtools.delete_exam(tmp_path, 3)

    assert problem is None and [p.name for p in deleted] == ["exam3.md"]
    assert (tmp_path / "Exam3_answers.md").read_text() == "a stray, not exam 3's"


def test_delete_exam_refuses_swapped_folder(tmp_path):
    folder = tmp_path / "exams"
    exam(folder, 3, questions=BAD)
    st = os.stat(folder)
    os.rename(folder, tmp_path / "old")
    exam(folder, 3, questions=BAD)                         # another folder, same path
    deleted, problem = examtools.delete_exam(folder, 3, folder=(st.st_dev, st.st_ino))
    assert deleted == [] and "is not the folder the session started in" in problem
    assert (folder / "exam3.md").exists()
    (tmp_path / "link").symlink_to(tmp_path / "old", target_is_directory=True)
    assert examtools.delete_exam(tmp_path / "link", 3) == ([], "link is a symlink")


def test_delete_exam_refuses_protected_or_unrecorded(tmp_path):
    exam(tmp_path, 3)
    st = os.lstat(tmp_path / "exam3_answers.md")
    deleted, problem = examtools.delete_exam(tmp_path, 3, protected={(st.st_dev, st.st_ino)})
    assert deleted == [] and "exam3_answers.md is a file that was already" in problem
    prints = {name: examtools.file_print(os.lstat(tmp_path / name))
              for name in ("exam3.md", "exam3_answers.md")}
    deleted, problem = examtools.delete_exam(tmp_path, 3, recorded={"exam3.md": prints["exam3.md"]})
    assert deleted == [] and "exam3_answers.md is not the file notes recorded" in problem
    deleted, problem = examtools.delete_exam(tmp_path, 3, recorded=prints)
    assert problem is None and len(deleted) == 2


def test_session_file_rewritten_while_claude_runs(cfg, exams, fake_session, capsys, monkeypatch):
    exam(exams, 1)
    seen = []
    real = examsession.after_session

    def during():
        seen.append(json.loads((exams / examsession.SESSION_FILE).read_text()))
        exam(exams, 2)
        exam(exams, 3, questions=BAD)

    def after(exam_dir, sess, *args, **kwargs):
        seen.append(json.loads((exams / examsession.SESSION_FILE).read_text()))
        return real(exam_dir, sess, *args, **kwargs)

    monkeypatch.setattr(examsession, "after_session", after)
    rc, _ = session(cfg, fake_session, capsys, during)

    started, before_check = seen
    assert started["claude"] == {"pid": 999_999, "process_start": None}
    assert started["recorded"] == {}
    assert started["folder"]["path"] == os.path.realpath(exams)
    assert sorted(before_check["recorded"]) == ["exam2.md", "exam2_answers.md", "exam3.md",
                                                "exam3_answers.md"]
    assert rc == 1 and not (exams / "exam3.md").exists() and (exams / "exam2.md").exists()
    assert no_temp_files(exams) and not (exams / examsession.SESSION_FILE).exists()


def test_wait_ticks_while_claude_runs(monkeypatch):
    monkeypatch.setattr(launch, "POLL_SECONDS", 0)

    class Proc:
        polls = 0

        def wait(self, timeout=None):
            self.polls += 1
            if self.polls < 5:
                raise subprocess.TimeoutExpired("claude", timeout)
            return 0

    ticks = []

    def tick():
        ticks.append(1)
        raise RuntimeError("a failing tick does not stop the wait")

    assert launch._wait(Proc(), launch.SessionSignals(), tick, tick_seconds=0) == 0
    assert len(ticks) == 4


def test_update_session_only_replaces_its_own_file(tmp_path):
    s = examsession.new_session(tmp_path, "FM2")
    examsession.write_session(tmp_path, s)
    s.recorded = {"exam1.md": [1, 2, 3, 4]}
    examsession.update_session(tmp_path, s)
    assert examsession.read_session(tmp_path, "FM2").recorded == s.recorded
    other = examsession.new_session(tmp_path, "FM2")
    other.pid = DEAD_PID
    with pytest.raises(examsession.UntrustedSession):
        examsession.update_session(tmp_path, other)
    examsession.remove_session(tmp_path)
    with pytest.raises(examsession.UntrustedSession):
        examsession.update_session(tmp_path, s)            # gone: not recreated
    assert os.listdir(tmp_path) == []


def test_read_session_fifo_does_not_hang(tmp_path):
    os.mkfifo(tmp_path / examsession.SESSION_FILE)
    with pytest.raises(examsession.UntrustedSession, match="not a regular file"):
        examsession.read_session(tmp_path, "FM2")


def test_read_session_symlink(tmp_path):
    (tmp_path / examsession.SESSION_FILE).symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(examsession.UntrustedSession, match="symlink"):
        examsession.read_session(tmp_path, "FM2")


def test_new_session_refused_while_earlier_claude_runs(cfg, exams, fake_session, capsys):
    exam(exams, 2, questions=BAD)
    write_stale(exams, claude=(os.getppid(), examsession.process_start(os.getppid())))
    before = snapshot(cfg.data_root)
    rc = main(["--data", str(cfg.data_root), "exam", "FM2"])
    err = capsys.readouterr().err
    assert rc == 1 and fake_session == [] and err.count("\n") == 1
    assert f"(pid {os.getppid()}" in err and "is still running, although that notes has stopped" in err
    assert snapshot(cfg.data_root) == before


def test_claim_is_exclusive(cfg, exams):
    exam(exams, 2, questions=BAD)
    stale = write_stale(exams)
    stale = examsession.read_session(exams, "FM2")
    claimed = examsession.claim_session(exams, stale)
    assert claimed == f"{examsession.SESSION_FILE}.claimed-{os.getpid()}"
    assert examsession.claim_session(exams, stale) is None   # a second recovery: nothing
    assert examsession.release_claim(exams, stale, claimed, finished=False) is None
    assert examsession.read_session(exams, "FM2") == stale    # put back for the next run


def test_claim_refuses_changed_file(cfg, exams):
    stale = write_stale(exams)
    stale = examsession.read_session(exams, "FM2")
    write_stale(exams, pid=DEAD_PID - 1)                      # replaced in between
    assert examsession.claim_session(exams, stale) is None
    assert examsession.read_session(exams, "FM2").pid == DEAD_PID - 1


@pytest.mark.parametrize("alive", [True, False])
def test_claimed_file_refuses_new_session(cfg, exams, fake_session, capsys, alive):
    exam(exams, 2, questions=BAD)
    write_stale(exams)
    pid = os.getppid() if alive else DEAD_PID
    os.rename(exams / examsession.SESSION_FILE,
              exams / f"{examsession.SESSION_FILE}.claimed-{pid}")
    before = snapshot(cfg.data_root)
    rc = main(["--data", str(cfg.data_root), "exam", "FM2"])
    err = capsys.readouterr().err
    assert rc == 1 and fake_session == [] and err.count("\n") == 1
    assert ("is checking the exams of a stopped session" if alive
            else "delete that file by hand") in err
    assert alive is False or "If you are sure nothing is running, delete" in err
    assert snapshot(cfg.data_root) == before


def test_stale_temp_files_of_dead_notes_removed(cfg, exams, fake_session, capsys, tmp_path):
    dead = examsession.SESSION_TMP.format(pid=DEAD_PID)
    live = examsession.SESSION_TMP.format(pid=os.getppid())
    linked = examsession.SESSION_TMP.format(pid=DEAD_PID - 2)
    other = examsession.SESSION_TMP.format(pid=DEAD_PID) + ".bak"
    put(exams, dead, "partial")
    put(exams, live, "someone's")
    put(exams, other, "mine")
    target = put(tmp_path, "target", "keep me")
    (exams / linked).symlink_to(target)

    rc, out = session(cfg, fake_session, capsys, None)

    assert not os.path.lexists(exams / dead)
    assert (exams / live).exists() and (exams / other).exists()
    assert (exams / linked).is_symlink() and target.read_text() == "keep me"
    assert f"notes: removed {exams / dead}, a temp file left by a 'notes exam'" in out


def test_hook_stray_message_never_suggests_deleting(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    put(exams, "exam03.md", BAD)
    put(exams, "exam6_answers.md", ANSWERS)
    code, _, err = hook(cfg, capsys, monkeypatch, snap)
    assert code == 2 and "delete" not in err.lower()
    assert ("If you wrote this file as an exam, rename it to Practice Exams/exam3.md (if that "
            "number is free); if you did not write it, leave it alone and tell the user.") in err
    assert ("exam6_answers.md exists but exam6.md does not: if you wrote it, write its "
            "questions file exam6.md; if you did not write it, leave it alone and tell the "
            "user.") in err


@pytest.mark.parametrize("stdin, code", [
    ('{"hook_event_name": "Stop", "stop_hook_active": true}', 0),
    ('{"stop_hook_active": false}', 2),
    ('{"stop_hook_active": "true"}', 2),
    ('{"stop_hook_active": true', 2),                       # not JSON: nothing believed
    ('[{"stop_hook_active": true}]', 2),
    ("", 2),
])
def test_hook_stop_hook_active_lets_strays_through(cfg, exams, capsys, monkeypatch, stdin, code):
    snap = snap_arg(exams)
    exam(exams, 1)
    put(exams, "Exam9.md", BAD)
    assert hook(cfg, capsys, monkeypatch, snap, stdin=stdin)[0] == code


def test_hook_stop_hook_active_still_blocks_invalid_exams(cfg, exams, capsys, monkeypatch):
    snap = snap_arg(exams)
    exam(exams, 1, questions=BAD)
    put(exams, "Exam9.md", BAD)
    code, _, err = hook(cfg, capsys, monkeypatch, snap, stdin='{"stop_hook_active": true}')
    assert code == 2 and "exam1.md — 1 error(s)" in err and "Exam9.md" in err
    put(exams, "exam1.md", GOOD)
    put(exams, "exam7_answers.md", ANSWERS)                # an orphan still blocks
    assert hook(cfg, capsys, monkeypatch, snap, stdin='{"stop_hook_active": true}')[0] == 2


def test_strays_reported_after_session_even_if_hook_let_them_through(cfg, exams, fake_session,
                                                                    capsys):
    rc, out = session(cfg, fake_session, capsys, lambda: (exam(exams, 1), put(exams, "Exam9.md",
                                                                              BAD)))
    assert rc == 1 and "Practice Exams/Exam9.md looks like an exam" in out
    assert (exams / "Exam9.md").exists()


def test_python_m_notes_cli_runs_the_cli(cfg):
    env = dict(os.environ, PYTHONPATH=str(Path(launch.__file__).parents[1]))
    done = subprocess.run([sys.executable, "-m", "notes_cli", "exam", "--help"],
                          capture_output=True, text=True, env=env, cwd=cfg.data_root)
    assert done.returncode == 0 and "usage: notes exam" in done.stdout
