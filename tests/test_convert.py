import json
import os
from pathlib import Path

import pymupdf
import pytest

from notes_cli import convert as convert_module
from notes_cli.backup import BackupUnreadable
from notes_cli.cli import main
from notes_cli.convert import MANIFEST_NAME, ConvertReport, convert
from notes_cli.mapping import REASON_NOT_SUBFOLDER, REASON_OUTSIDE, REASON_TRASH
from notes_cli.render import stamped_note_id

from factory import make_folder_zip, make_note_zip

DBA = ("Uni CS", "Year 1", "Semester 2", "DBA")
DSA = ("Uni CS", "Year 1", "Semester 1", "DSA")
FM2 = ("Uni CS", "Year 2", "Semester 1", "FM2")
DBA_LGT = (*DBA, "DBA LGT Notes")
DBA_LEC = (*DBA, "DBA Lecture Notes")
DSA_LGT = (*DSA, "DSA LGT")
LGT_DIR = "CS Degree/Year 1/DBA/LGT"
LEC_DIR = "CS Degree/Year 1/DBA/Lecture Notes"
DSA_DIR = "CS Degree/Year 1/DSA/LGT"


# -- helpers ------------------------------------------------------------------------------


def folders(sync: Path, *path: str) -> str:
    """Folder zips for each level of `path` (reused when present); the leaf folder's id."""
    sync.mkdir(parents=True, exist_ok=True)
    parent = None
    for depth in range(1, len(path) + 1):
        folder_id = "f:" + "/".join(path[:depth])
        if not any(p.name.endswith("_" + folder_id.replace("/", "_")) for p in sync.iterdir()):
            make_folder_zip(sync, path[depth - 1], folder_id, parent)
        parent = folder_id
    return parent


def note(cfg, title: str, id: str, *path: str, replace: bool = True, **kw) -> Path:
    """Write a note zip into the backup. With `replace`, earlier zips of the same note id
    are removed first (a clean rename/edit); without, they stay behind as in the real
    backup, where the sync never deletes."""
    sync = cfg.sync_dir
    sync.mkdir(parents=True, exist_ok=True)
    if replace:
        remove_note(cfg, id)
    parent = folders(sync, *path) if path else None
    return make_note_zip(sync, title, id, parent, **kw)


def remove_note(cfg, id: str) -> None:
    for p in cfg.sync_dir.iterdir():
        if p.name.endswith("_" + id):
            p.unlink()


def pdf(cfg, rel: str) -> Path:
    return cfg.data_root / rel


def run(cfg, **kw) -> tuple[ConvertReport, list[str]]:
    lines: list[str] = []
    report = convert(cfg, log=lines.append, **kw)
    return report, lines


def manifest(cfg) -> dict:
    return json.loads((cfg.data_root / MANIFEST_NAME).read_text(encoding="utf-8"))


def rels(cfg, paths) -> set[str]:
    return {Path(p).relative_to(cfg.data_root).as_posix() for p in paths}


def snapshot(root: Path, exclude=()) -> dict[str, tuple[int, int, bytes]]:
    """Every file under `root`: (size, mtime_ns, content)."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not any(rel == e or rel.startswith(e + "/") for e in exclude):
            st = p.stat()
            out[rel] = (st.st_size, st.st_mtime_ns, p.read_bytes())
    return out


def user_pdf(path: Path, text: str = "mine") -> bytes:
    """A PDF made by someone else (no notes-cli stamp) at `path`; returns its bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path.read_bytes()


# -- the plan's tests -----------------------------------------------------------------------


def test_converts_mapped_notes_only(cfg):
    note(cfg, "DBA LGT W1", "n-1", *DBA_LGT, modified=11)
    note(cfg, "DBA Lecture 1", "n-2", *DBA_LEC, modified=12)
    note(cfg, "Personal", "n-3", "Other Notes")
    note(cfg, "Old", "n-4", *DBA_LGT, in_trash=True)
    note(cfg, "Textbook", "n-5", *DBA, "DBA Textbook Notes")

    report, _ = run(cfg)

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/DBA LGT W1.pdf", f"{LEC_DIR}/DBA Lecture 1.pdf"}
    assert report.unchanged == 0
    assert report.failed == [] and report.orphans == [] and report.bad == []
    assert sorted(report.skipped) == [("Old", REASON_TRASH), ("Personal", REASON_OUTSIDE),
                                      ("Textbook", REASON_NOT_SUBFOLDER)]
    assert rels(cfg, cfg.data_root.rglob("*.pdf")) == rels(cfg, report.converted)
    for path in report.converted:
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/DBA LGT W1.pdf")) == "n-1"
    assert manifest(cfg) == {
        "n-1": {"path": f"{LGT_DIR}/DBA LGT W1.pdf", "modified": 11, "title": "DBA LGT W1"},
        "n-2": {"path": f"{LEC_DIR}/DBA Lecture 1.pdf", "modified": 12, "title": "DBA Lecture 1"},
    }


def test_second_run_converts_nothing(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    first, _ = run(cfg)
    before = snapshot(cfg.data_root)

    report, lines = run(cfg)

    assert report.converted == [] and report.unchanged == 2
    assert lines == []  # nothing logged for unchanged notes
    assert snapshot(cfg.data_root) == before  # not even the manifest is rewritten
    assert len(first.converted) == 2


def test_modified_note_reconverted(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    note(cfg, "W2", "n-2", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W1", "n-1", *DBA_LGT, modified=2, pages=[{}, {}])

    report, _ = run(cfg)

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf"}
    assert report.unchanged == 1
    assert manifest(cfg)["n-1"]["modified"] == 2
    with pymupdf.open(pdf(cfg, f"{LGT_DIR}/W1.pdf")) as doc:
        assert doc.page_count == 2


def test_missing_pdf_reconverted(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    run(cfg)
    pdf(cfg, f"{LGT_DIR}/W1.pdf").unlink()

    report, _ = run(cfg)

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf"}
    assert report.unchanged == 1
    assert pdf(cfg, f"{LGT_DIR}/W1.pdf").is_file()


def test_force_reconverts(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    run(cfg)

    report, lines = run(cfg, force=True)

    assert len(report.converted) == 2 and report.unchanged == 0
    assert len(lines) == 2


def test_module_filter(cfg):
    note(cfg, "DBA W1", "n-1", *DBA_LGT)
    note(cfg, "DBA W2", "n-2", *DBA_LGT)
    note(cfg, "DSA W1", "n-3", *DSA_LGT)
    note(cfg, "DBA Rev", "n-4", "Uni CS", "Year 1", "Semester 2", "Revision", "DBA", "DBA LGT")
    note(cfg, "DSA Text", "n-5", *DSA, "DSA Textbook")

    report, _ = run(cfg, module="dba")  # any case

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/DBA W1.pdf", f"{LGT_DIR}/DBA W2.pdf"}
    assert not pdf(cfg, DSA_DIR).exists()
    # Only skipped notes that belong with DBA are listed.
    assert report.skipped == [("DBA Rev", "module not configured")]

    # Change DBA behind the filter's back: rename one note, edit the other.
    note(cfg, "DBA W1 renamed", "n-1", *DBA_LGT, modified=5)
    note(cfg, "DBA W2", "n-2", *DBA_LGT, modified=5)
    dba_entries = {k: v for k, v in manifest(cfg).items() if k in ("n-1", "n-2")}

    report, _ = run(cfg, module="DSA")

    assert rels(cfg, report.converted) == {f"{DSA_DIR}/DSA W1.pdf"}
    assert report.orphans == []  # the DBA orphan is not DSA's business
    assert report.skipped == [("DSA Text", REASON_NOT_SUBFOLDER)]
    after = manifest(cfg)
    assert {k: after[k] for k in ("n-1", "n-2")} == dba_entries  # untouched

    report, _ = run(cfg, module="DBA")

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/DBA W1 renamed.pdf", f"{LGT_DIR}/DBA W2.pdf"}
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/DBA W1.pdf"}
    assert manifest(cfg)["n-3"]["path"] == f"{DSA_DIR}/DSA W1.pdf"


def test_one_bad_note_does_not_stop_run(cfg, monkeypatch):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    note(cfg, "W3", "n-3", *DBA_LGT)
    real = convert_module.load_note

    def load(zip_path):
        if zip_path.name.endswith("_n-2"):
            raise RuntimeError("database disk image is malformed")
        return real(zip_path)

    monkeypatch.setattr(convert_module, "load_note", load)

    report, lines = run(cfg)

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf", f"{LGT_DIR}/W3.pdf"}
    assert report.failed == [("W2", "RuntimeError: database disk image is malformed")]
    assert not pdf(cfg, f"{LGT_DIR}/W2.pdf").exists()
    assert "n-2" not in manifest(cfg)
    assert any("W2" in line and "failed" in line for line in lines)


def test_renamed_note_reports_orphan_and_keeps_file(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    run(cfg)
    old = pdf(cfg, f"{LGT_DIR}/W1.pdf")
    old_bytes = old.read_bytes()
    # A rename in Notein: the sync leaves the old zip behind next to the new one.
    note(cfg, "W1 intro", "n-1", *DBA_LGT, modified=2, replace=False)

    report, _ = run(cfg)

    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1 intro.pdf"}
    assert report.orphans == [old]
    assert old.read_bytes() == old_bytes  # reported, never deleted
    assert len(report.bad) == 1 and "older copy" in report.bad[0].reason
    assert manifest(cfg)["n-1"] == {"path": f"{LGT_DIR}/W1 intro.pdf", "modified": 2,
                                    "title": "W1 intro", "orphans": [f"{LGT_DIR}/W1.pdf"]}


def test_sync_dir_untouched(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "Empty", "n-2", *DBA_LGT, pages=[])  # fails: no pages
    note(cfg, "Personal", "n-3", "Other Notes")
    (cfg.sync_dir / "Broken_xyz").write_bytes(b"not a zip")
    (cfg.sync_dir / "sub").mkdir()
    before = snapshot(cfg.sync_dir)
    entries_before = sorted(p.name for p in cfg.sync_dir.rglob("*"))

    run(cfg)
    run(cfg, force=True)

    assert snapshot(cfg.sync_dir) == before
    assert sorted(p.name for p in cfg.sync_dir.rglob("*")) == entries_before


# -- failures -------------------------------------------------------------------------------


def test_note_without_pages_fails_and_others_convert(cfg):
    note(cfg, "Empty", "n-1", *DBA_LGT, pages=[])
    note(cfg, "W2", "n-2", *DBA_LGT)

    report, _ = run(cfg)

    assert [t for t, _ in report.failed] == ["Empty"]
    assert "no pages" in report.failed[0][1]
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W2.pdf"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_unwritable_folder_fails_note_not_run(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "L1", "n-2", *DBA_LEC)
    lgt = pdf(cfg, LGT_DIR)
    lgt.mkdir(parents=True)
    lgt.chmod(0o555)
    try:
        report, _ = run(cfg)  # MuPDF raises its own (non-OSError) exception here
    finally:
        lgt.chmod(0o755)

    assert [t for t, _ in report.failed] == ["W1"]
    assert rels(cfg, report.converted) == {f"{LEC_DIR}/L1.pdf"}
    assert list(lgt.iterdir()) == []


def test_failed_note_keeps_previous_pdf_and_entry(cfg, monkeypatch):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    run(cfg)
    path = pdf(cfg, f"{LGT_DIR}/W1.pdf")
    before_pdf, before_entry = path.read_bytes(), manifest(cfg)["n-1"]
    note(cfg, "W1", "n-1", *DBA_LGT, modified=2)

    def broken_render(note, out):
        raise ValueError("boom")

    monkeypatch.setattr(convert_module, "render_pdf", broken_render)
    report, _ = run(cfg)

    assert report.failed == [("W1", "ValueError: boom")]
    assert path.read_bytes() == before_pdf
    assert manifest(cfg)["n-1"] == before_entry
    assert report.orphans == []

    monkeypatch.undo()
    report, _ = run(cfg)
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf"}
    assert manifest(cfg)["n-1"]["modified"] == 2


def test_keyboard_interrupt_leaves_consistent_manifest_and_no_tmp(cfg, monkeypatch):
    for n in (1, 2, 3):
        note(cfg, f"W{n}", f"n-{n}", *DBA_LGT)
    real_replace = os.replace

    def replace(src, dst):
        if str(dst).endswith("/W2.pdf"):
            raise KeyboardInterrupt
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        run(cfg)
    monkeypatch.undo()

    assert not [p for p in cfg.data_root.rglob("*") if p.name.endswith(".tmp")]
    assert set(manifest(cfg)) == {"n-1"}
    assert pdf(cfg, f"{LGT_DIR}/W1.pdf").is_file()
    assert not pdf(cfg, f"{LGT_DIR}/W2.pdf").exists()

    report, _ = run(cfg)
    assert report.unchanged == 1
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W2.pdf", f"{LGT_DIR}/W3.pdf"}


def test_interrupt_while_writing_manifest(cfg, monkeypatch):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    real_replace = os.replace
    manifest_writes = []

    def replace(src, dst):
        if Path(dst).name == MANIFEST_NAME:
            manifest_writes.append(dst)
            if len(manifest_writes) == 2:
                raise KeyboardInterrupt
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        run(cfg)
    monkeypatch.undo()

    assert not [p for p in cfg.data_root.rglob("*") if p.name.endswith(".tmp")]
    assert set(manifest(cfg)) == {"n-1"}  # the previous version, intact
    # W2's PDF was written but not recorded; the next run converts it again (it is ours).
    report, _ = run(cfg)
    assert report.failed == []
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W2.pdf"}


# -- report contents ---------------------------------------------------------------------------


def test_warnings_reported(cfg):
    note(cfg, "With image", "n-1", *DBA_LGT, pages=[{"images": [{"id": "img-1"}]}])

    report, _ = run(cfg)

    assert len(report.converted) == 1
    assert report.warnings == [("With image", "1 image(s) not in backup; skipped")]


def test_bad_entries_reported(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    (cfg.sync_dir / "Broken_xyz").write_bytes(b"not a zip")

    report, _ = run(cfg)

    assert [b.path.name for b in report.bad] == ["Broken_xyz"]
    assert len(report.converted) == 1


def test_log_one_line_per_converted_note(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "Two\nlines", "n-2", *DBA_LGT)
    note(cfg, "Personal", "n-3", "Other Notes")

    _, lines = run(cfg)

    assert lines == [f"[1/2] Two\\nlines -> {LGT_DIR}/Two-lines.pdf",
                     f"[2/2] W1 -> {LGT_DIR}/W1.pdf"]


def test_manifest_written_after_each_note(cfg):
    for n in (1, 2, 3):
        note(cfg, f"W{n}", f"n-{n}", *DBA_LGT)
    seen = []

    def log(line):
        seen.append(set(manifest(cfg)))

    convert(cfg, log=log)

    assert seen == [{"n-1"}, {"n-1", "n-2"}, {"n-1", "n-2", "n-3"}]


def test_missing_backup_raises(cfg):
    with pytest.raises(BackupUnreadable, match="not found"):
        convert(cfg)
    assert not (cfg.data_root / MANIFEST_NAME).exists()


# -- ownership of files at a destination -----------------------------------------------------


def test_user_file_at_destination_not_overwritten(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    mine = user_pdf(pdf(cfg, f"{LGT_DIR}/W1.pdf"))

    for force in (False, True):
        report, _ = run(cfg, force=force)

        assert [t for t, _ in report.failed] == ["W1"]
        assert "not made by notes" in report.failed[0][1]
        assert pdf(cfg, f"{LGT_DIR}/W1.pdf").read_bytes() == mine
        assert "n-1" not in manifest(cfg)
        assert report.orphans == []
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W2.pdf")) == "n-2"


def test_user_file_replacing_our_pdf_not_overwritten(cfg):
    """The manifest says the tool wrote the path, but the file there now is the user's."""
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    run(cfg)
    mine = user_pdf(pdf(cfg, f"{LGT_DIR}/W1.pdf"))
    note(cfg, "W1", "n-1", *DBA_LGT, modified=2)

    report, _ = run(cfg)

    assert [t for t, _ in report.failed] == ["W1"]
    assert pdf(cfg, f"{LGT_DIR}/W1.pdf").read_bytes() == mine
    assert manifest(cfg)["n-1"]["modified"] == 1


def test_non_pdf_and_folder_at_destination_not_overwritten(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT)
    note(cfg, "W2", "n-2", *DBA_LGT)
    pdf(cfg, LGT_DIR).mkdir(parents=True)
    pdf(cfg, f"{LGT_DIR}/W1.pdf").write_text("plain text")
    pdf(cfg, f"{LGT_DIR}/W2.pdf").mkdir()

    report, _ = run(cfg, force=True)

    assert sorted(t for t, _ in report.failed) == ["W1", "W2"]
    assert pdf(cfg, f"{LGT_DIR}/W1.pdf").read_text() == "plain text"
    assert pdf(cfg, f"{LGT_DIR}/W2.pdf").is_dir()


def test_lost_manifest_does_not_block_own_pdfs(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    note(cfg, "W2", "n-2", *DBA_LGT, modified=1)
    run(cfg)
    (cfg.data_root / MANIFEST_NAME).unlink()
    note(cfg, "W1", "n-1", *DBA_LGT, modified=2)

    report, _ = run(cfg)

    assert report.failed == []
    assert len(report.converted) == 2  # nothing is known to be unchanged any more
    report, _ = run(cfg)
    assert report.unchanged == 2 and report.converted == []


@pytest.mark.parametrize("content", [
    b"{not json",
    b"",
    b"[]",
    b"\xff\xfe\x00",
    b'{"n-1": {"path": "../outside.pdf", "modified": 1, "title": "W1"}}',
    b'{"n-1": {"path": "/abs.pdf", "modified": 1, "title": "W1"}}',
    b'{"n-1": {"path": "CS Degree/x.pdf", "modified": true, "title": "W1"}}',
    b'{"n-1": {"path": "CS Degree/x.pdf", "modified": 1}}',
    b'{"n-1": {"path": "CS Degree/x.pdf", "modified": 1, "title": "W1", "orphans": "x"}}',
])
def test_corrupt_manifest_kept_aside_and_warned(cfg, content):
    note(cfg, "W1", "n-1", *DBA_LGT)
    (cfg.data_root / MANIFEST_NAME).write_bytes(content)

    report, lines = run(cfg)

    assert len(report.converted) == 1 and report.failed == []
    aside = [p for p in cfg.data_root.iterdir() if p.name.startswith(MANIFEST_NAME + ".corrupt-")]
    assert len(aside) == 1 and aside[0].read_bytes() == content
    assert len(report.warnings) == 1
    title, warning = report.warnings[0]
    assert title == MANIFEST_NAME and aside[0].name in warning
    assert any(aside[0].name in line for line in lines)
    assert set(manifest(cfg)) == {"n-1"}


def test_unreadable_manifest_that_cannot_be_moved_stops_run(cfg, monkeypatch):
    note(cfg, "W1", "n-1", *DBA_LGT)
    (cfg.data_root / MANIFEST_NAME).write_bytes(b"{bad")
    real_replace = os.replace

    def replace(src, dst):
        if Path(src).name == MANIFEST_NAME:
            raise PermissionError(13, "Permission denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "rename", replace)

    with pytest.raises(convert_module.ManifestError, match="manifest"):
        run(cfg)
    assert (cfg.data_root / MANIFEST_NAME).read_bytes() == b"{bad"
    assert not list(cfg.data_root.rglob("*.pdf"))


# -- orphans ------------------------------------------------------------------------------------


def test_orphan_reported_every_run_until_file_gone(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W1 intro", "n-1", *DBA_LGT, modified=2)
    run(cfg)

    report, _ = run(cfg)  # nothing changed: still reported
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/W1.pdf"}
    assert report.unchanged == 1

    pdf(cfg, f"{LGT_DIR}/W1.pdf").unlink()  # the user deletes it
    report, _ = run(cfg)
    assert report.orphans == []
    assert "orphans" not in manifest(cfg)["n-1"]


def test_deleted_trashed_and_moved_out_notes_leave_orphans(cfg):
    note(cfg, "Gone", "n-1", *DBA_LGT, modified=1)
    note(cfg, "Trashed", "n-2", *DBA_LGT, modified=1)
    note(cfg, "Moved", "n-3", *DBA_LGT, modified=1)
    note(cfg, "Kept", "n-4", *DBA_LGT, modified=1)
    run(cfg)
    remove_note(cfg, "n-1")
    note(cfg, "Trashed", "n-2", *DBA_LGT, modified=2, in_trash=True)
    note(cfg, "Moved", "n-3", *DBA, "DBA Textbook Notes", modified=2)

    report, _ = run(cfg)

    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/Gone.pdf", f"{LGT_DIR}/Trashed.pdf",
                                         f"{LGT_DIR}/Moved.pdf"}
    assert all(p.is_file() for p in report.orphans)
    assert sorted(report.skipped) == [("Moved", REASON_NOT_SUBFOLDER), ("Trashed", REASON_TRASH)]
    assert set(manifest(cfg)) == {"n-1", "n-2", "n-3", "n-4"}  # kept while the files exist

    pdf(cfg, f"{LGT_DIR}/Gone.pdf").unlink()
    pdf(cfg, f"{LGT_DIR}/Trashed.pdf").unlink()
    report, _ = run(cfg)
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/Moved.pdf"}
    assert set(manifest(cfg)) == {"n-3", "n-4"}

    # Restored from the trash: converted again, nothing orphaned.
    note(cfg, "Trashed", "n-2", *DBA_LGT, modified=3)
    report, _ = run(cfg)
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/Trashed.pdf"}


def test_new_note_takes_renamed_notes_old_name(cfg):
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W2", "n-a", *DBA_LGT, modified=2)  # A renamed away from W1
    note(cfg, "W1", "n-b", *DBA_LGT, modified=2)  # a new note B takes W1

    report, _ = run(cfg)

    assert report.failed == []
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf", f"{LGT_DIR}/W2.pdf"}
    assert report.orphans == []  # W1.pdf is B's now
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1.pdf")) == "n-b"
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W2.pdf")) == "n-a"
    m = manifest(cfg)
    assert m["n-a"] == {"path": f"{LGT_DIR}/W2.pdf", "modified": 2, "title": "W2"}
    assert m["n-b"] == {"path": f"{LGT_DIR}/W1.pdf", "modified": 2, "title": "W1"}
    assert run(cfg)[0].unchanged == 2


def test_swapped_titles(cfg):
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    note(cfg, "W2", "n-b", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W2", "n-a", *DBA_LGT, modified=2)
    note(cfg, "W1", "n-b", *DBA_LGT, modified=2)

    report, _ = run(cfg)

    assert report.failed == [] and report.orphans == []
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1.pdf")) == "n-b"
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W2.pdf")) == "n-a"
    m = manifest(cfg)
    assert (m["n-a"]["path"], m["n-b"]["path"]) == (f"{LGT_DIR}/W2.pdf", f"{LGT_DIR}/W1.pdf")
    assert "orphans" not in m["n-a"] and "orphans" not in m["n-b"]


def test_clash_appears_and_disappears(cfg):
    note(cfg, "W1", "aaaaaaaa-1", *DBA_LGT, modified=1)
    run(cfg)

    # A second "W1" appears: both get id suffixes; A's plain W1.pdf is now an orphan.
    note(cfg, "W1", "bbbbbbbb-2", *DBA_LGT, modified=1)
    report, _ = run(cfg)
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1 (aaaaaaaa).pdf", f"{LGT_DIR}/W1 (bbbbbbbb).pdf"}
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/W1.pdf"}

    # B is deleted: A goes back to W1.pdf (its own old file), the suffixed files are orphans.
    remove_note(cfg, "bbbbbbbb-2")
    report, _ = run(cfg)
    assert report.failed == []
    assert rels(cfg, report.converted) == {f"{LGT_DIR}/W1.pdf"}
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/W1 (aaaaaaaa).pdf", f"{LGT_DIR}/W1 (bbbbbbbb).pdf"}
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1.pdf")) == "aaaaaaaa-1"
    m = manifest(cfg)
    assert m["aaaaaaaa-1"]["orphans"] == [f"{LGT_DIR}/W1 (aaaaaaaa).pdf"]
    assert m["bbbbbbbb-2"]["path"] == f"{LGT_DIR}/W1 (bbbbbbbb).pdf"


def test_gone_notes_pdf_is_not_overwritten_by_new_note(cfg):
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    a_pdf = pdf(cfg, f"{LGT_DIR}/W1.pdf").read_bytes()
    remove_note(cfg, "n-a")  # A deleted in Notein: W1.pdf is its only PDF
    note(cfg, "W1", "n-b", *DBA_LGT, modified=2)

    for force in (False, True):
        report, _ = run(cfg, force=force)
        assert [t for t, _ in report.failed] == ["W1"]
        assert "another note" in report.failed[0][1] and "W1" in report.failed[0][1]
        assert pdf(cfg, f"{LGT_DIR}/W1.pdf").read_bytes() == a_pdf
        assert report.orphans == []  # it is B's destination; the failure says what is in the way

    # The user moves A's PDF away; B converts and A's record is dropped.
    pdf(cfg, f"{LGT_DIR}/W1.pdf").rename(cfg.data_root / "kept.pdf")
    report, _ = run(cfg)
    assert report.failed == [] and len(report.converted) == 1
    assert set(manifest(cfg)) == {"n-b"}


def test_moved_note_orphan_only_in_its_module(cfg):
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W1", "n-1", *DSA_LGT, modified=2)  # moved from DBA to DSA in Notein

    assert run(cfg, module="DSA")[0].orphans == []
    report, _ = run(cfg, module="DBA")
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/W1.pdf"}
    assert report.converted == []
    report, _ = run(cfg)
    assert rels(cfg, report.orphans) == {f"{LGT_DIR}/W1.pdf"}


# -- the user's other files ----------------------------------------------------------------------


def test_data_root_untouched_outside_outputs(cfg):
    root = cfg.data_root
    note(cfg, "DBA W1", "n-1", *DBA_LGT)
    note(cfg, "DBA W2", "n-2", *DBA_LGT)
    note(cfg, "FM2 W1", "n-3", *FM2, "FM2 Lecture")
    note(cfg, "Empty", "n-4", *DBA_LGT, pages=[])
    (cfg.sync_dir / "Broken_xyz").write_bytes(b"not a zip")
    (root / "Notein" / "Old").mkdir(parents=True)
    (root / "Notein" / "Old" / "old.in").write_bytes(b"old export")
    slides = root / "CS Degree" / "Year 2" / "FM2" / "Lecture-Slides" / "Week 1"
    user_pdf(slides / "week1.pdf", "slides")
    user_pdf(pdf(cfg, f"{LGT_DIR}/My own notes.pdf"))  # unrelated PDF in a destination folder
    user_pdf(pdf(cfg, f"{LGT_DIR}/DBA W2.pdf"))  # the user's file at a destination
    (root / "readme.txt").write_text("hello")
    before = snapshot(root)
    dirs_before = {p for p in root.rglob("*") if p.is_dir()}

    run(cfg)
    run(cfg)
    report, _ = run(cfg, force=True)

    after = snapshot(root)
    changed = {k for k in before if after.get(k) != before[k]}
    assert changed == set()  # nothing that existed was modified or removed
    allowed = {MANIFEST_NAME, f"{LGT_DIR}/DBA W1.pdf", "CS Degree/Year 2/FM2/Lecture Notes/FM2 W1.pdf"}
    assert set(after) - set(before) == allowed
    new_dirs = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()} - {
        p.relative_to(root).as_posix() for p in dirs_before}
    assert new_dirs == {"CS Degree/Year 2/FM2/Lecture Notes"}
    assert sorted(t for t, _ in report.failed) == ["DBA W2", "Empty"]


# -- CLI ---------------------------------------------------------------------------------------


def test_cli_convert_summary_and_exit_code(cfg_dir, capsys):
    from notes_cli.config import load_config

    cfg = load_config(cfg_dir)
    note(cfg, "W1", "n-1", *DBA_LGT, modified=1)
    note(cfg, "W2", "n-2", *DBA_LGT, modified=1)
    note(cfg, "Old name", "n-3", *DBA_LGT, modified=1)
    assert main(["--data", str(cfg_dir), "convert"]) == 0
    capsys.readouterr()

    note(cfg, "W1", "n-1", *DBA_LGT, modified=2, pages=[{"images": [{"id": "img-1"}]}])
    note(cfg, "New name", "n-3", *DBA_LGT, modified=2)
    note(cfg, "Empty", "n-4", *DBA_LGT, pages=[])
    note(cfg, "Personal", "n-5", "Other Notes")
    note(cfg, "Bin", "n-6", *DBA_LGT, in_trash=True)
    (cfg.sync_dir / "Broken_xyz").write_bytes(b"not a zip")

    assert main(["--data", str(cfg_dir), "convert", "dba"]) == 1  # a note failed
    out, err = capsys.readouterr()
    lines = out.splitlines()

    assert err == ""
    # With a module filter only skipped notes filed under that module are counted (the
    # binned DBA note, not "Personal").
    assert "2 converted, 1 unchanged, 1 skipped, 1 failed; 1 warning, 1 orphan, 1 bad entry" in lines
    assert "  skipped, in trash: 1" in lines
    assert any(l.startswith("failed: Empty -- ") and "no pages" in l for l in lines)
    assert "warning: W1 -- 1 image(s) not in backup; skipped" in lines
    assert any(l.startswith(f"orphan: {LGT_DIR}/Old name.pdf") for l in lines)
    assert any(l.startswith("bad entry: Broken_xyz -- ") for l in lines)
    assert "Traceback" not in out

    assert main(["--data", str(cfg_dir), "convert", "DBA"]) == 1  # still failing
    pdf(cfg, f"{LGT_DIR}/Old name.pdf").unlink()
    remove_note(cfg, "n-4")
    assert main(["--data", str(cfg_dir), "convert"]) == 0


def test_cli_convert_missing_backup_same_as_organise(cfg_dir, capsys):
    assert main(["--data", str(cfg_dir), "organise"]) == 1
    organise_err = capsys.readouterr().err

    assert main(["--data", str(cfg_dir), "convert"]) == 1
    out, err = capsys.readouterr()

    assert err == organise_err
    assert len(err.strip().splitlines()) == 1 and "notes sync" in err
    assert "Traceback" not in err
    assert not (cfg_dir / MANIFEST_NAME).exists()


def test_cli_convert_unknown_module_exits_2(cfg_dir, capsys):
    assert main(["--data", str(cfg_dir), "convert", "XYZ"]) == 2
    assert "valid modules" in capsys.readouterr().err


# -- cases found in review: taking over another note's file must never lose a PDF ---------


def _fail_note(monkeypatch, note_id: str) -> None:
    real = convert_module.load_note

    def load(zip_path):
        if zip_path.name.endswith("_" + note_id):
            raise RuntimeError("boom")
        return real(zip_path)

    monkeypatch.setattr(convert_module, "load_note", load)


def test_takeover_waits_until_the_other_note_is_safely_converted(cfg, monkeypatch):
    # A was "W1"; it is renamed and a new note B is called "W1". A's conversion fails.
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    a_pdf = pdf(cfg, f"{LGT_DIR}/W1.pdf")
    before = a_pdf.read_bytes()
    note(cfg, "W1 renamed", "n-a", *DBA_LGT, modified=2)
    note(cfg, "W1", "n-b", *DBA_LGT, modified=1)
    _fail_note(monkeypatch, "n-a")

    report, _ = run(cfg)

    # B must not take A's only PDF; A's file is not called an orphan either.
    assert a_pdf.read_bytes() == before and stamped_note_id(a_pdf) == "n-a"
    assert {title for title, _ in report.failed} == {"W1 renamed", "W1"}
    assert report.orphans == []
    assert manifest(cfg)["n-a"]["path"] == f"{LGT_DIR}/W1.pdf"

    monkeypatch.undo()
    report, _ = run(cfg)  # once A converts, B takes the freed name in the same run
    assert report.failed == []
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1 renamed.pdf")) == "n-a"
    assert stamped_note_id(a_pdf) == "n-b"


def test_swap_where_one_note_fails_changes_nothing(cfg, monkeypatch):
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    note(cfg, "W2", "n-b", *DBA_LGT, modified=1)
    run(cfg)
    before = snapshot(cfg.degree_dir)
    note(cfg, "W2", "n-a", *DBA_LGT, modified=2)
    note(cfg, "W1", "n-b", *DBA_LGT, modified=2)
    _fail_note(monkeypatch, "n-b")

    report, _ = run(cfg)

    assert len(report.failed) == 2
    assert snapshot(cfg.degree_dir) == before  # both old PDFs intact, no holding files left


def test_takeover_blocked_when_other_note_is_outside_the_module_filter(cfg):
    # A moves from DBA to DSA; B takes A's old name in DBA; only DBA is converted.
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    note(cfg, "W1", "n-a", *DSA_LGT, modified=2)
    note(cfg, "W1", "n-b", *DBA_LGT, modified=1)

    report, _ = run(cfg, module="DBA")

    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1.pdf")) == "n-a"  # still A's only PDF
    assert [title for title, _ in report.failed] == ["W1"]

    report, _ = run(cfg)
    assert report.failed == []
    assert stamped_note_id(pdf(cfg, f"{DSA_DIR}/W1.pdf")) == "n-a"
    assert stamped_note_id(pdf(cfg, f"{LGT_DIR}/W1.pdf")) == "n-b"


def test_users_copy_of_a_generated_pdf_is_not_overwritten(cfg):
    # The user copies a converted PDF into another folder to annotate it. The copy still
    # carries note A's stamp, but the tool did not put it there.
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    copy = pdf(cfg, f"{LEC_DIR}/W1.pdf")
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(pdf(cfg, f"{LGT_DIR}/W1.pdf").read_bytes() + b"\n% my annotations\n")
    kept = copy.read_bytes()
    note(cfg, "W1", "n-b", *DBA_LEC, modified=1)

    for force in (False, True):
        report, _ = run(cfg, force=force)
        assert copy.read_bytes() == kept
        assert [title for title, _ in report.failed] == ["W1"]
        assert "did not put there" in report.failed[0][1]


@pytest.mark.parametrize("target", ["Lecture-Slides", "../../../../outside", "SGT"])
def test_symlinked_destination_folder_is_refused(cfg, target):
    module = cfg.data_root / "CS Degree/Year 1/DBA"
    (module / "Lecture-Slides").mkdir(parents=True)
    (module / "SGT").mkdir()
    (cfg.data_root.parent / "outside").mkdir()
    (module / "LGT").symlink_to(target, target_is_directory=True)
    note(cfg, "W1", "n-a", *DBA_LGT)

    report, _ = run(cfg)

    assert report.converted == []
    assert "symbolic link" in report.failed[0][1]
    assert list((module / "Lecture-Slides").iterdir()) == []
    assert list((module / "SGT").iterdir()) == []
    assert list((cfg.data_root.parent / "outside").iterdir()) == []


def test_symlinked_parent_folder_is_refused(cfg):
    real = cfg.data_root.parent / "elsewhere"
    real.mkdir()
    (cfg.data_root / "CS Degree").mkdir(exist_ok=True)
    (cfg.data_root / "CS Degree/Year 1").symlink_to(real, target_is_directory=True)
    note(cfg, "W1", "n-a", *DBA_LGT)

    report, _ = run(cfg)

    assert report.converted == [] and "symbolic link" in report.failed[0][1]
    assert list(real.rglob("*.pdf")) == []


def test_unreadable_destination_folder_fails_notes_without_raising(cfg):
    note(cfg, "W1", "n-a", *DBA_LGT, modified=1)
    run(cfg)
    folder = cfg.data_root / LGT_DIR
    folder.chmod(0o600)
    try:
        if os.access(folder / "W1.pdf", os.F_OK):
            pytest.skip("running as a user that ignores folder permissions")
        report, _ = run(cfg)
    finally:
        folder.chmod(0o755)

    assert report.converted == [] and len(report.failed) == 1


def test_manifest_paths_outside_the_module_folders_are_rejected(cfg):
    slides = cfg.data_root / "CS Degree/Year 1/DBA/Lecture-Slides"
    slides.mkdir(parents=True)
    (slides / "s1.pdf").write_bytes(b"%PDF-1.4 slides")
    (cfg.data_root / MANIFEST_NAME).write_text(json.dumps({
        "n-x": {"path": "CS Degree/Year 1/DBA/Lecture-Slides/s1.pdf", "modified": 1, "title": "x"},
    }), encoding="utf-8")
    note(cfg, "W1", "n-a", *DBA_LGT)

    report, _ = run(cfg)

    assert report.orphans == []  # the user's slide is never called an orphan
    assert any(title == MANIFEST_NAME for title, _ in report.warnings)
    assert (slides / "s1.pdf").read_bytes() == b"%PDF-1.4 slides"


def test_user_file_with_a_temp_like_name_is_not_overwritten(cfg):
    folder = cfg.data_root / LGT_DIR
    folder.mkdir(parents=True)
    mine = folder / "W1.pdf.tmp"
    mine.write_bytes(b"mine")
    note(cfg, "W1", "n-a", *DBA_LGT)

    report, _ = run(cfg)

    assert report.failed == [] and mine.read_bytes() == b"mine"
    assert stamped_note_id(folder / "W1.pdf") == "n-a"
