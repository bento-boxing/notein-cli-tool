"""What `notes exam MODULE` does around the Claude session (user request 2026-11-15, "I
NEVER want invalid exams").

- **Snapshot.** When a session starts, the numbers N of every `exam<N>.md`,
  `exam<N>_answers.md` and `exam<N>_full.md` in `Practice Exams/` are recorded, with a
  fingerprint (SHA-256) of each of those files, the (st_dev, st_ino) of every regular file
  under the module folder, `Practice Exams/` included (so a file renamed to a new exam
  number, or moved in from elsewhere in the module, is still known as an earlier one; if
  the folder has more than INODE_SCAN_LIMIT entries the list is incomplete and nothing is
  deleted after that session) and the list of exam-like files that are not named as exams
  (`examtools.stray_exams`). An exam belongs to the session if and only if no
  `exam<N>.md` existed when it started; modification times are never used for that.
  Exams from earlier sessions are never deleted and never block the Stop hook, whatever
  happens to them; if one changed or is invalid, that is reported.
- **Session file.** `Practice Exams/.notes-exam-session.json` holds the snapshot, this
  process's pid and start time, Claude's pid and start time, the module, the folder it
  was written for (real path and (st_dev, st_ino)), and the files this session wrote
  (`recorded`: name -> [st_dev, st_ino, size, mtime_ns]). It is written before Claude
  starts, rewritten every few seconds while Claude runs and once more right before the
  check after the session (always through a temp file and a rename), and removed once
  that check has finished. If `notes` is killed in between, the next `notes exam MODULE`
  finds the file: a live `notes` or a live Claude of that session means it is still
  running (refused); otherwise that session's check never ran, so it is run first, after
  claiming the file (renaming it) so that two runs cannot both act on it. A session file
  that is a link, not a regular file, not this user's, unreadable, or written for another
  module or folder is never acted on.
- **Stop hook** (`hook_problems`): what keeps Claude working. Checks this session's exams
  (`examtools.finished_check`), answers files written without their exam, and new exam-like
  files with the wrong name. Reads only; never writes or deletes.
- **After the session** (`after_session`): the same checks once more; the session's exams
  that are still not valid are deleted (`examtools.delete_exam`) with their answers and
  full files, except a part that already existed when the session started. A file is
  deleted only if it is exactly the file last recorded in the session file (same inode,
  size and modification time) and is not a file that existed when the session started
  under any name; everything else is reported and left in place. An exam that cannot be
  read is left in place and reported. Nothing else is deleted.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from notes_cli import examtools
from notes_cli.examtools import EXAM_DIR, ExamCheck, FolderChanged, file_print, open_folder

SESSION_FILE = ".notes-exam-session.json"
SESSION_TMP = ".notes-exam-session.tmp-{pid}"          # not exam-like, hidden
SESSION_CLAIMED = SESSION_FILE + ".claimed-{pid}"
SESSION_TOOL = "notes exam"
SESSION_VERSION = 2
MAX_SESSION_BYTES = 8 << 20
INODE_SCAN_LIMIT = 100_000      # entries looked at for the snapshot's inodes, at most
PID_MAX = 1 << 22               # Linux's largest pid_max: no pid is above it
RECORD_SECONDS = 5              # how often the session file is rewritten while Claude runs


class UntrustedSession(Exception):
    """The session file exists but cannot be trusted (message says why)."""


def _one_line(text: str) -> str:
    """`text` on one line with control characters escaped (for file names in messages)."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii")
                   for ch in str(text))


def _fingerprint(path: Path) -> str | None:
    """What a part file is now: a regular file's SHA-256, "link:TARGET", "dir" or "other";
    None if there is nothing at `path`."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        try:
            return "link:" + os.readlink(path)
        except OSError:
            return "link:?"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                digest.update(block)
    except OSError:
        return "unreadable"
    return digest.hexdigest()


def proper_name(stray: str) -> str:
    """The name a stray exam-like file probably should have: `exam<N>.md` or
    `exam<N>_answers.md` (N from its name)."""
    base = stray.rsplit("/", 1)[-1]
    m = re.search(r"\d+", base)
    n = int(m.group()) if m and int(m.group()) > 0 else "N"
    kind = "_answers" if re.search(r"answer|key|solution", base, re.IGNORECASE) else ""
    return f"exam{n}{kind}.md"


def _part_names(n: int) -> list[str]:
    return [f"exam{n}.md", f"exam{n}_answers.md", f"exam{n}_full.md"]


def _inodes(folder: Path) -> tuple[frozenset[tuple[int, int]], bool]:
    """(st_dev, st_ino) of every regular file under `folder` (links not followed), and
    whether that list is complete (False if more than INODE_SCAN_LIMIT entries were
    found)."""
    found, seen = set(), 0
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in filenames:
            seen += 1
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                found.add((st.st_dev, st.st_ino))
        seen += len(dirnames)
        if seen > INODE_SCAN_LIMIT:
            return frozenset(found), False
    return frozenset(found), True


@dataclass
class Snapshot:
    """`Practice Exams/` when a session started."""
    exams: frozenset[int] = frozenset()     # N with an exam<N>.md (of any kind)
    answers: frozenset[int] = frozenset()   # N with an exam<N>_answers.md
    full: frozenset[int] = frozenset()      # N with an exam<N>_full.md
    strays: frozenset[str] = frozenset()    # examtools.stray_exams
    fingerprints: dict[str, str] = field(default_factory=dict)  # part file name -> _fingerprint
    inodes: frozenset[tuple[int, int]] = frozenset()  # every regular file under the module
    inodes_complete: bool = True            # False: too many files, `inodes` is not all of them

    def to_json(self) -> dict:
        return {"exams": sorted(self.exams), "answers": sorted(self.answers),
                "full": sorted(self.full), "strays": sorted(self.strays),
                "fingerprints": dict(sorted(self.fingerprints.items())),
                "inodes": [list(i) for i in sorted(self.inodes)],
                "inodes_complete": self.inodes_complete}

    def hook_arg(self) -> str:
        """What the Stop hook needs, as compact ASCII JSON (`--snapshot`)."""
        return json.dumps({"exams": sorted(self.exams), "answers": sorted(self.answers),
                           "strays": sorted(self.strays)}, separators=(",", ":"))

    def is_new(self, name: str) -> bool:
        """Whether part file `name` (exam<N>.md, _answers, _full) was not there at the start."""
        m = examtools._PART.fullmatch(name)
        if not m:
            return False
        numbers = {None: self.exams, "_answers": self.answers, "_full": self.full}[m.group(2)]
        return int(m.group(1)) not in numbers

    @classmethod
    def from_json(cls, data, complete: bool = False) -> "Snapshot":
        """Raises ValueError unless `data` has exactly the expected shape. `complete`: as
        in a session file (with "full" and "inodes"), not just the hook's part."""
        if not isinstance(data, dict):
            raise ValueError("not an object")

        def numbers(key, required=True):
            value = data.get(key, None if required else [])
            if not isinstance(value, list) or not all(
                    type(v) is int and v > 0 for v in value):
                raise ValueError(f"{key!r} is not a list of exam numbers")
            return frozenset(value)

        strays = data.get("strays", [])
        if not isinstance(strays, list) or not all(isinstance(s, str) for s in strays):
            raise ValueError("'strays' is not a list of names")
        prints = data.get("fingerprints", {})
        if not isinstance(prints, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in prints.items()):
            raise ValueError("'fingerprints' is not a map of names")
        inodes = data.get("inodes", None if complete else [])
        if not isinstance(inodes, list) or not all(
                isinstance(i, list) and len(i) == 2 and all(type(v) is int for v in i)
                for i in inodes):
            raise ValueError("'inodes' is not a list of [device, inode]")
        # Missing from a session file: not known to be complete, so nothing is deleted.
        inodes_complete = data.get("inodes_complete", not complete)
        if type(inodes_complete) is not bool:
            raise ValueError("'inodes_complete' is not true or false")
        return cls(numbers("exams"), numbers("answers"), numbers("full", required=complete),
                   frozenset(strays), dict(prints), frozenset(tuple(i) for i in inodes),
                   inodes_complete)


def take_snapshot(exam_dir: Path) -> Snapshot:
    exam_dir = Path(exam_dir)
    found = examtools.exam_numbers(exam_dir)
    prints = {}
    for n in found["exam"] | found["answers"] | found["full"]:
        for name in _part_names(n):
            fp = _fingerprint(exam_dir / name)
            if fp is not None:
                prints[name] = fp
    module_dir = exam_dir.parent
    inodes, complete = _inodes(module_dir) if module_dir.is_dir() else (frozenset(), True)
    return Snapshot(frozenset(found["exam"]), frozenset(found["answers"]),
                    frozenset(found["full"]), frozenset(examtools.stray_exams(exam_dir)), prints,
                    inodes, complete)


# --- processes -----------------------------------------------------------------------------


def process_start(pid: int) -> str | None:
    """When process `pid` started (clock ticks since boot, from /proc), or None if that
    cannot be told; "zombie" for a process that has exited but not been reaped."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            text = f.read().decode("ascii", "replace")
    except OSError:
        return None
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) < 20:
        return None
    return "zombie" if fields[0] in ("Z", "X") else fields[19]


def pid_running(pid: int, started: str | None) -> bool:
    """Whether the process recorded in a session file is still running: `pid` exists, is
    not this process, and (where /proc tells) started when it did."""
    if pid == os.getpid() or not 0 < pid <= PID_MAX:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass            # exists, but someone else's
    except OSError:
        return False
    now = process_start(pid)
    if now == "zombie":
        return False
    return not (started is not None and now is not None and now != started)


# --- the session file ----------------------------------------------------------------------


@dataclass
class Session:
    pid: int
    started: float              # epoch seconds
    module: str                 # canonical module name
    snapshot: Snapshot
    process_start: str | None = None
    folder: str = ""            # realpath of Practice Exams/
    folder_id: tuple[int, int] = (0, 0)     # its (st_dev, st_ino)
    claude_pid: int | None = None
    claude_start: str | None = None
    recorded: dict[str, list[int]] = field(default_factory=dict)  # name -> file_print

    def to_json(self) -> dict:
        claude = (None if self.claude_pid is None
                  else {"pid": self.claude_pid, "process_start": self.claude_start})
        return {"tool": SESSION_TOOL, "version": SESSION_VERSION, "pid": self.pid,
                "process_start": self.process_start, "started": self.started,
                "module": self.module,
                "folder": {"path": self.folder, "dev": self.folder_id[0],
                           "ino": self.folder_id[1]},
                "claude": claude, "snapshot": self.snapshot.to_json(),
                "recorded": dict(sorted(self.recorded.items()))}

    def same_owner(self, other: "Session") -> bool:
        return (self.pid, self.started, self.process_start) == \
            (other.pid, other.started, other.process_start)


def _folder_identity(exam_dir: Path) -> tuple[int, int]:
    fd = open_folder(exam_dir)
    try:
        st = os.fstat(fd)
        return st.st_dev, st.st_ino
    finally:
        os.close(fd)


def new_session(exam_dir: Path, module: str) -> Session:
    """A session for `exam_dir` (which must be a plain folder: FolderChanged otherwise),
    starting now. `module` is the canonical module name."""
    identity = _folder_identity(exam_dir)
    return Session(os.getpid(), time.time(), module, take_snapshot(exam_dir),
                   process_start(os.getpid()), os.path.realpath(exam_dir), identity)


def session_path(exam_dir: Path) -> Path:
    return Path(exam_dir) / SESSION_FILE


def _encode(session: Session) -> bytes:
    return (json.dumps(session.to_json(), separators=(",", ":")) + "\n").encode("ascii")


def _write_temp(fd: int, data: bytes) -> str:
    """Write `data` to a new temp file in folder `fd`; its name."""
    tmp = SESSION_TMP.format(pid=os.getpid())
    try:
        os.unlink(tmp, dir_fd=fd)       # left by an interrupted run of this pid: ours
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    out = os.open(tmp, flags, 0o644, dir_fd=fd)
    try:
        with os.fdopen(out, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _unlink_quietly(tmp, fd)
        raise
    return tmp


def _unlink_quietly(name: str, fd: int) -> None:
    try:
        os.unlink(name, dir_fd=fd)
    except OSError:
        pass


def write_session(exam_dir: Path, session: Session) -> Path:
    """Create the session file, complete or not at all; FileExistsError if there is one (or
    anything at that path), FolderChanged if the folder is not the session's, OSError if
    it cannot be written (then no file is left behind)."""
    fd = open_folder(exam_dir, session.folder_id)
    try:
        tmp = _write_temp(fd, _encode(session))
        try:
            os.link(tmp, SESSION_FILE, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            _unlink_quietly(tmp, fd)
    finally:
        os.close(fd)
    return session_path(exam_dir)


def update_session(exam_dir: Path, session: Session) -> None:
    """Replace the session file with `session` (atomically: a temp file and a rename), if
    the file there is still this session's. Raises UntrustedSession if it is not (or is
    gone), FolderChanged or OSError if it cannot be written."""
    fd = open_folder(exam_dir, session.folder_id)
    try:
        current = _read_at(fd, SESSION_FILE, session.module, exam_dir)
        if current is None or not current.same_owner(session):
            raise UntrustedSession("it is no longer this session's file")
        tmp = _write_temp(fd, _encode(session))
        try:
            os.replace(tmp, SESSION_FILE, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            _unlink_quietly(tmp, fd)
            raise
    finally:
        os.close(fd)


def _read_at(fd: int, name: str, module: str, exam_dir: Path) -> Session | None:
    """Read and check session file `name` in folder `fd` (see read_session)."""
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                          dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise UntrustedSession("it is a symlink") from e
        raise UntrustedSession(f"it cannot be read ({e.strerror or e})") from e
    try:
        st = os.fstat(file_fd)
        if not stat.S_ISREG(st.st_mode):
            raise UntrustedSession("it is not a regular file")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise UntrustedSession("it belongs to another user")
        if st.st_size > MAX_SESSION_BYTES:
            raise UntrustedSession("it is too big to be a session file")
        try:
            chunks, size = [], 0
            while size <= MAX_SESSION_BYTES:
                chunk = os.read(file_fd, 1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        except OSError as e:
            raise UntrustedSession(f"it cannot be read ({e.strerror or e})") from e
        raw = b"".join(chunks)
    finally:
        os.close(file_fd)
    try:
        session = _parse(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, TypeError) as e:
        raise UntrustedSession(f"it is not a valid session file ({e})") from e
    here = os.fstat(fd)
    if session.module != module:
        raise UntrustedSession(f"it was written for module {_one_line(session.module)!r}, "
                               f"not {module}")
    if session.folder != os.path.realpath(exam_dir) or \
            session.folder_id != (here.st_dev, here.st_ino):
        raise UntrustedSession(f"it was written for another folder "
                               f"({_one_line(session.folder)}), not this one")
    return session


def _parse(data) -> Session:
    if not isinstance(data, dict) or data.get("tool") != SESSION_TOOL \
            or data.get("version") != SESSION_VERSION:
        raise ValueError("not a notes exam session file of this version")
    pid, started, module = data.get("pid"), data.get("started"), data.get("module")
    proc = data.get("process_start")
    if type(pid) is not int or not 0 < pid <= PID_MAX:
        raise ValueError("no valid pid")
    if type(started) not in (int, float):
        raise ValueError("no valid start time")
    if not isinstance(module, str) or (proc is not None and not isinstance(proc, str)):
        raise ValueError("no valid module")
    folder = data.get("folder")
    if not isinstance(folder, dict) or not isinstance(folder.get("path"), str) \
            or type(folder.get("dev")) is not int or type(folder.get("ino")) is not int:
        raise ValueError("no valid folder")
    claude = data.get("claude")
    claude_pid = claude_start = None
    if claude is not None:
        if not isinstance(claude, dict) or type(claude.get("pid")) is not int \
                or not 0 < claude["pid"] <= PID_MAX or not (claude.get("process_start") is None
                                              or isinstance(claude.get("process_start"), str)):
            raise ValueError("no valid claude process")
        claude_pid, claude_start = claude["pid"], claude.get("process_start")
    recorded = data.get("recorded")
    if not isinstance(recorded, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and len(v) == 4
            and all(type(x) is int for x in v) for k, v in recorded.items()):
        raise ValueError("no valid list of recorded files")
    snapshot = Snapshot.from_json(data.get("snapshot"), complete=True)
    return Session(pid, float(started), module, snapshot, proc, folder["path"],
                   (folder["dev"], folder["ino"]), claude_pid, claude_start, dict(recorded))


def read_session(exam_dir: Path, module: str, name: str = SESSION_FILE) -> Session | None:
    """The session file's contents, or None if there is none. Raises UntrustedSession if
    the folder is a symlink or cannot be opened, or the file is a link, not a regular file
    (a FIFO does not block: it is opened non-blocking and checked with fstat), not this
    user's, too big, unreadable, not exactly what `write_session` writes, or written for
    another module or another folder (real path or (st_dev, st_ino) differ, e.g. a copy
    from another module's folder)."""
    try:
        fd = open_folder(exam_dir)
    except FolderChanged as e:
        raise UntrustedSession(str(e)) from e
    try:
        return _read_at(fd, name, module, exam_dir)
    finally:
        os.close(fd)


def remove_session(exam_dir: Path, name: str = SESSION_FILE) -> str | None:
    """Remove the session file (a link is removed, not followed); why not, or None."""
    try:
        os.unlink(Path(exam_dir) / name)
    except FileNotFoundError:
        return None
    except OSError as e:
        return e.strerror or str(e)
    return None


_CLAIMED = re.compile(re.escape(SESSION_FILE) + r"\.claimed-(\d+)")


def claimed_files(exam_dir: Path) -> list[tuple[str, int]]:
    """Session files claimed by a `notes exam` that is checking a stopped session (or was,
    and was killed while doing it): (name, that process's pid)."""
    try:
        names = os.listdir(exam_dir)
    except OSError:
        return []
    return sorted((name, int(m.group(1))) for name in names
                  if (m := _CLAIMED.fullmatch(name)))


_TEMP = re.compile(re.escape(SESSION_TMP.split("{", 1)[0]) + r"([1-9]\d{0,9})")


def remove_stale_temps(exam_dir: Path) -> list[str]:
    """Remove the session temp files (`.notes-exam-session.tmp-<pid>`, that exact form) left
    by a `notes exam` that is no longer running: regular files only, never a link. The
    names removed."""
    try:
        fd = open_folder(exam_dir)
    except FolderChanged:
        return []
    removed = []
    try:
        for name in os.listdir(fd):
            m = _TEMP.fullmatch(name)
            if not m or pid_running(int(m.group(1)), None) or int(m.group(1)) == os.getpid():
                continue
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode):
                    os.unlink(name, dir_fd=fd)
                    removed.append(name)
            except OSError:
                continue
    finally:
        os.close(fd)
    return sorted(removed)


def claim_session(exam_dir: Path, session: Session) -> str | None:
    """Rename the session file to a name of this process's own, so that no other run acts
    on it, and check it is still `session`. The claimed name, or None if the file was gone
    (another run claimed it first) or had changed (then it is put back)."""
    claimed = SESSION_CLAIMED.format(pid=os.getpid())
    fd = open_folder(exam_dir, session.folder_id)
    try:
        try:
            os.stat(claimed, dir_fd=fd, follow_symlinks=False)
            return None                 # never rename over another file
        except FileNotFoundError:
            pass
        try:
            os.rename(SESSION_FILE, claimed, src_dir_fd=fd, dst_dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            again = _read_at(fd, claimed, session.module, exam_dir)
        except UntrustedSession:
            again = None
        if again != session:
            _restore(fd, claimed)
            return None
        return claimed
    finally:
        os.close(fd)


def _restore(fd: int, claimed: str) -> bool:
    """Put a claimed session file back under its own name (never over another one)."""
    try:
        os.link(claimed, SESSION_FILE, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    except OSError:
        return False
    _unlink_quietly(claimed, fd)
    return True


def release_claim(exam_dir: Path, session: Session, claimed: str, finished: bool) -> str | None:
    """After the check of a claimed session: remove the claimed file if the check
    finished, else put it back so that the next run checks again. Why not, or None."""
    try:
        fd = open_folder(exam_dir, session.folder_id)
    except FolderChanged as e:
        return str(e)
    try:
        if finished:
            try:
                os.unlink(claimed, dir_fd=fd)
            except FileNotFoundError:
                pass
            except OSError as e:
                return e.strerror or str(e)
            return None
        return None if _restore(fd, claimed) else "a session file is already there"
    finally:
        os.close(fd)


def record_new_files(exam_dir: Path, session: Session) -> dict[str, list[int]]:
    """The session's own files now: every `exam<N>.md`, `exam<N>_answers.md` and
    `exam<N>_full.md` that is new by the snapshot, a regular file, and not a file that
    existed when the session started (by inode), with its `file_print`. Names come from
    the folder's listing (exact matches). Raises FolderChanged if the folder is not the
    session's."""
    fd = open_folder(exam_dir, session.folder_id)
    try:
        recorded = {}
        for name in os.listdir(fd):
            if not session.snapshot.is_new(name):
                continue
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) not in session.snapshot.inodes:
                recorded[name] = file_print(st)
        return recorded
    finally:
        os.close(fd)


def record(exam_dir: Path, session: Session) -> str | None:
    """Record the session's files now (`record_new_files`) in `session` and in its session
    file. Why it could not be done, or None; never raises. When the files cannot be listed,
    `session.recorded` keeps what was recorded last."""
    try:
        session.recorded = record_new_files(exam_dir, session)
        update_session(exam_dir, session)
    except (OSError, FolderChanged, UntrustedSession) as e:
        return str(e)
    except Exception as e:  # recording must never stop the session
        return f"{type(e).__name__}: {e}"
    return None


# --- what belongs to the session -------------------------------------------------------------


@dataclass
class Found:
    """`Practice Exams/` now, compared with the snapshot."""
    exams: list[int]          # exam<N>.md now, none at the start: the session's exams
    orphans: list[int]        # exam<N>_answers.md written in the session, no exam<N>.md
    earlier: list[int]        # exam<N>.md at the start (whatever there is now)
    strays: list[str]         # exam-like files with the wrong name, new in the session


def compare(exam_dir: Path, snapshot: Snapshot) -> Found:
    now = examtools.exam_numbers(exam_dir)
    exams = sorted(now["exam"] - snapshot.exams)
    orphans = sorted(n for n in now["answers"] - snapshot.answers
                     if n not in now["exam"] and n not in snapshot.exams)
    strays = sorted(set(examtools.stray_exams(exam_dir)) - snapshot.strays)
    return Found(exams, orphans, sorted(snapshot.exams), strays)


def changed_parts(exam_dir: Path, snapshot: Snapshot, n: int) -> list[str]:
    """The files of earlier exam `n` that are not what they were when the session started
    (written, replaced, deleted or created)."""
    return [name for name in _part_names(n)
            if _fingerprint(Path(exam_dir) / name) != snapshot.fingerprints.get(name)]


# --- the Stop hook ---------------------------------------------------------------------------


def hook_problems(exam_dir: Path, snapshot: Snapshot, strays_block: bool = True) -> list[str]:
    """The Stop hook's message (lines), or [] when this session's exams are all valid and
    finished. Stray exam-like files block only if `strays_block` (Claude Code sets
    stop_hook_active once a hook has already blocked this stop: then a stray file that
    Claude may not own must not hold it forever). Never writes anything, and never tells
    Claude to delete anything."""
    found = compare(exam_dir, snapshot)
    failing = [c for c in (examtools.finished_check(exam_dir, n) for n in found.exams)
               if not c.ok]
    if not failing and not found.orphans and not (found.strays and strays_block):
        return []
    lines = ["The practice exams written in this session are not all valid yet, so the "
             "session cannot finish. Errors:", ""]
    for check in failing:
        lines += [examtools.error_report(check), ""]
    for n in found.orphans:
        lines += [f"exam{n}_answers.md exists but exam{n}.md does not: if you wrote it, write "
                  f"its questions file exam{n}.md; if you did not write it, leave it alone and "
                  "tell the user.", ""]
    for name in found.strays:
        lines += [f"{EXAM_DIR}/{_one_line(name)} looks like an exam file but is not named "
                  f"exam<N>.md or exam<N>_answers.md directly in {EXAM_DIR}/, so it is not "
                  f"checked. If you wrote this file as an exam, rename it to "
                  f"{EXAM_DIR}/{proper_name(name)} (if that number is free); if you did not "
                  "write it, leave it alone and tell the user.", ""]
    lines.append("Have a subagent fix these exams (the same brief as their batch, plus "
                 "these errors), as Generation Strategy says; change only this session's "
                 "exams, never one from an earlier session. Warnings are not errors and do "
                 "not block. Then finish again: the check runs each time you finish.")
    return lines


# --- after the session -------------------------------------------------------------------------


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _errors_summary(check: ExamCheck) -> str:
    return f"{_plural(len(check.errors), 'error')}: " + "; ".join(check.errors)


@dataclass
class Outcome:
    new: int = 0                 # this session's exams
    valid: int = 0               # ... of which valid
    deleted: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)   # invalid, not (fully) deleted
    problems: int = 0            # invalid, deleted, not deletable, uncheckable, strays, ...

    @property
    def nothing(self) -> bool:
        return not self.new and not self.problems


def after_session(exam_dir: Path, session: Session, say: Callable[[str], None],
                  stopped: bool = False) -> Outcome:
    """Check the exams the session wrote, delete the ones that are not valid, report the
    rest (see the module docstring). A file is deleted only if it is exactly as in
    `session.recorded`. `stopped`: this is the check of a session whose `notes` was
    stopped (the messages say so). `say` prints one message and never raises. Every exam
    is handled on its own: a failure with one does not stop the others."""
    exam_dir = Path(exam_dir)
    module, snapshot = session.module, session.snapshot
    shown = lambda name: _one_line(str(exam_dir / name))   # noqa: E731
    out = Outcome()
    say("")
    if not exam_dir.is_dir():
        say("notes: no exam was written in this session.")
        return out
    found = compare(exam_dir, snapshot)
    out.new = len(found.exams)
    folder_linked = exam_dir.is_symlink()

    for n in found.exams:
        try:
            check = examtools.finished_check(exam_dir, n)
        except Exception as e:  # a bug in a check must not stop the others
            out.problems += 1
            say(f"notes: exam{n}.md could not be checked ({type(e).__name__}: "
                f"{_one_line(e)}); it was left in place: {shown(f'exam{n}.md')}. Run 'notes "
                f"exam validate {module} {n}' and delete it if it is not valid.")
            continue
        if check.uncheckable:
            out.problems += 1
            say(f"notes: exam {n} could not be checked ({_errors_summary(check)}), so it was "
                f"left in place: {shown(f'exam{n}.md')}. Make its files readable and run "
                f"'notes exam validate {module} {n}'.")
            continue
        say(examtools.report(check))
        say("")
        if check.ok:
            out.valid += 1
            continue
        out.problems += 1
        if folder_linked:
            out.kept.append(n)
            say(f"notes: exam{n}.md is NOT valid ({_errors_summary(check)}) and was NOT "
                f"deleted: {EXAM_DIR} is a symlink, so nothing is deleted through it: "
                f"{shown(f'exam{n}.md')}")
            continue
        if not snapshot.inodes_complete:
            out.kept.append(n)
            say(f"notes: exam{n}.md is NOT valid ({_errors_summary(check)}) and was NOT "
                f"deleted: the module folder had too many files (over {INODE_SCAN_LIMIT}) for "
                "notes to record them all when the session started, so nothing is deleted "
                f"after this session; delete or fix it yourself: {shown(f'exam{n}.md')}")
            continue
        keep = tuple(kind for kind, numbers in (("answers", snapshot.answers),
                                                 ("full", snapshot.full)) if n in numbers)
        try:
            deleted, problem = examtools.delete_exam(
                exam_dir, n, keep=keep, folder=session.folder_id, protected=snapshot.inodes,
                recorded=session.recorded)
        except Exception as e:
            deleted, problem = [], f"{type(e).__name__}: {e}"
        if deleted:
            out.deleted.append(n)
            kept = [f"exam{n}_{kind}.md" for kind in keep
                    if os.path.lexists(exam_dir / f"exam{n}_{kind}.md")]
            note = (f"; {_and(kept)} {'was' if len(kept) == 1 else 'were'} there before "
                    "this session and left in place" if kept else "")
            say(f"notes: deleted {_and([p.name for p in deleted])}: not valid when the "
                f"session ended ({_errors_summary(check)}){note}")
        if problem:
            out.kept.append(n)
            say(f"notes: exam{n}.md is NOT valid ({_errors_summary(check)}) and "
                f"{'was only partly' if deleted else 'was NOT'} deleted: {_one_line(problem)}; "
                f"delete or fix it yourself: {shown(f'exam{n}.md')}")

    for n in found.orphans:
        out.problems += 1
        say(f"notes: exam{n}_answers.md was written in this session but there is no "
            f"exam{n}.md; left in place: {shown(f'exam{n}_answers.md')}")

    for name in found.strays:
        out.problems += 1
        say(f"notes: warning: {EXAM_DIR}/{_one_line(name)} looks like an exam but is not named "
            f"exam<N>.md directly in {EXAM_DIR}/, so it was not checked; left in place. "
            f"Rename it ({EXAM_DIR}/{proper_name(name)}, if that number is free) and run "
            f"'notes exam validate {module}', or delete it.")

    earlier_invalid = []
    for n in found.earlier:
        try:
            changed = changed_parts(exam_dir, snapshot, n)
            check = examtools.finished_check(exam_dir, n) \
                if os.path.lexists(exam_dir / f"exam{n}.md") else None
        except Exception as e:
            out.problems += 1
            say(f"notes: exam{n}.md (from an earlier session) could not be checked "
                f"({type(e).__name__}: {_one_line(e)}); it was left in place.")
            continue
        if changed:
            what = (f"{_and(changed)} {'was' if len(changed) == 1 else 'were'} changed in this "
                    f"session (exam {n} is from an earlier session, so nothing was deleted)")
            if check is None:
                out.problems += 1
                say(f"notes: {what}; exam{n}.md no longer exists.")
            elif not check.ok:
                out.problems += 1
                say(f"notes: {what}; it is NOT valid now ({_errors_summary(check)}): "
                    f"{shown(f'exam{n}.md')}")
            else:
                say(f"notes: {what}; it is still valid.")
        elif check is not None and not check.ok:
            earlier_invalid.append(f"exam{n}.md ({_plural(len(check.errors), 'error')}): "
                                   f"{shown(f'exam{n}.md')}")
    if earlier_invalid:
        say("notes: notice: exams from earlier sessions that are not valid (left in place; "
            "this does not change the exit status): " + "; ".join(earlier_invalid))

    if out.deleted and (exam_dir / "concept_matrix.md").is_file():
        numbers = [str(n) for n in out.deleted]
        which = f"exam{'s' if len(numbers) > 1 else ''} " + _and(numbers)
        say(f"notes: concept_matrix.md was left as it is and may still mention the deleted "
            f"{which}; the next 'notes exam {module}' run will have Claude remove them from it.")

    session_word = "that session" if stopped else "this session"
    if out.nothing:
        say(f"notes: no exam was written in {session_word}.")
    elif not out.problems:
        say(f"notes: {_plural(out.new, 'exam')} written in {session_word}, all valid.")
    else:
        v = out.valid
        summary = (f"notes: {v} exam{'s' if v != 1 else ''} written in {session_word} "
                   f"{'is' if v == 1 else 'are'} valid")
        d = len([n for n in out.deleted if n not in out.kept])
        k = len(out.kept)
        if d:
            summary += f"; {d} invalid exam{'s were' if d != 1 else ' was'} deleted"
        if k:
            summary += (f"; {k} invalid exam{'s' if k != 1 else ''} could not be deleted and "
                        f"{'were' if k != 1 else 'was'} left in place (see above)")
        if d:
            summary += (f". Run 'notes exam {module}' again to regenerate "
                        f"{'them' if d != 1 else 'it'}.")
        elif k:
            summary += "."
        else:
            summary += "; see above for what is not."
        say(summary)
    return out
