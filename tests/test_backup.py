import json
import sqlite3
import zipfile

import pytest

from notes_cli.backup import BadEntry, Backup, FolderEntry, NoteEntry, scan

from factory import make_folder_zip, make_note_zip


def test_scan_reads_notes_and_folders(tmp_path):
    make_folder_zip(tmp_path, "Uni CS", "f-root", None)
    make_folder_zip(tmp_path, "Year 1", "f-y1", "f-root")
    z = make_note_zip(tmp_path, "Week 1", "n-1", "f-y1", modified=1769252926000, in_trash=True)

    backup = scan(tmp_path)

    assert isinstance(backup, Backup)
    assert len(backup.notes) == 1
    assert len(backup.folders) == 2
    assert backup.bad == []
    assert backup.folders["f-root"] == FolderEntry(id="f-root", title="Uni CS", parent_id=None)
    assert backup.folders["f-y1"] == FolderEntry(id="f-y1", title="Year 1", parent_id="f-root")
    assert backup.notes[0] == NoteEntry(
        id="n-1",
        title="Week 1",
        parent_id="f-y1",
        modified=1769252926000,
        in_trash=True,
        zip_path=z,
    )
    assert z.name == "Week 1_n-1"


def test_folder_path_follows_parents_and_strips(tmp_path):
    make_folder_zip(tmp_path, "Uni CS", "f-root", None)
    make_folder_zip(tmp_path, "Year 1", "f-y1", "f-root")
    make_folder_zip(tmp_path, "SWE Lecture ", "f-swe", "f-y1")
    make_note_zip(tmp_path, "Week 1", "n-1", "f-swe")

    backup = scan(tmp_path)

    assert backup.folder_path(backup.notes[0]) == ("Uni CS", "Year 1", "SWE Lecture")


def test_missing_parent_gives_partial_path(tmp_path):
    # "Year 1" points at a parent folder that is not in the backup.
    make_folder_zip(tmp_path, "Year 1", "f-y1", "f-gone")
    make_folder_zip(tmp_path, "DBA", "f-dba", "f-y1")
    make_note_zip(tmp_path, "A", "n-a", "f-dba")
    make_note_zip(tmp_path, "B", "n-b", "f-also-gone")
    make_note_zip(tmp_path, "C", "n-c", None)

    backup = scan(tmp_path)
    by_id = {n.id: n for n in backup.notes}

    assert backup.folder_path(by_id["n-a"]) == ("Year 1", "DBA")
    assert backup.folder_path(by_id["n-b"]) == ()
    assert backup.folder_path(by_id["n-c"]) == ()
    assert backup.bad == []


def test_parent_cycle_terminates(tmp_path):
    make_folder_zip(tmp_path, "A", "f-a", "f-b")
    make_folder_zip(tmp_path, "B", "f-b", "f-a")
    make_folder_zip(tmp_path, "Self", "f-self", "f-self")
    make_note_zip(tmp_path, "N", "n-1", "f-a")
    make_note_zip(tmp_path, "M", "n-2", "f-self")

    backup = scan(tmp_path)
    by_id = {n.id: n for n in backup.notes}

    assert backup.folder_path(by_id["n-1"]) == ("B", "A")
    assert backup.folder_path(by_id["n-2"]) == ("Self",)


def test_template_and_directories_ignored(tmp_path):
    with zipfile.ZipFile(tmp_path / "Custom1_c921dc5a", "w") as z:
        z.writestr("template_Custom1", b"\x00template bytes")
    sub = tmp_path / ".claude"
    sub.mkdir()
    (sub / "junk").write_bytes(b"not a zip")
    make_note_zip(tmp_path, "Week 1", "n-1", None)

    backup = scan(tmp_path)

    assert [n.id for n in backup.notes] == ["n-1"]
    assert backup.folders == {}
    assert backup.bad == []


def test_corrupt_zip_reported_not_raised(tmp_path):
    (tmp_path / "Broken_abc").write_bytes(b"not a zip")
    make_note_zip(tmp_path, "Week 1", "n-1", None)
    make_folder_zip(tmp_path, "Uni CS", "f-root", None)

    backup = scan(tmp_path)

    assert [n.id for n in backup.notes] == ["n-1"]
    assert list(backup.folders) == ["f-root"]
    assert len(backup.bad) == 1
    assert isinstance(backup.bad[0], BadEntry)
    assert backup.bad[0].path == tmp_path / "Broken_abc"
    assert backup.bad[0].reason


def test_half_downloaded_zip_reported_not_raised(tmp_path):
    whole = make_note_zip(tmp_path, "Half", "n-half", None)
    data = whole.read_bytes()
    whole.write_bytes(data[: len(data) // 2])
    make_note_zip(tmp_path, "Week 1", "n-1", None)

    backup = scan(tmp_path)

    assert [n.id for n in backup.notes] == ["n-1"]
    assert [b.path.name for b in backup.bad] == ["Half_n-half"]
    assert backup.bad[0].reason


def test_corrupt_member_name_reported_not_raised(tmp_path):
    # A damaged byte in a UTF-8 member name makes zipfile raise UnicodeDecodeError.
    path = tmp_path / "Damaged_f-x"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("folder_éold", '{"id":"f-x","title":"éold"}')
    raw = path.read_bytes()
    assert b"folder_\xc3\xa9old" in raw
    path.write_bytes(raw.replace(b"folder_\xc3\xa9old", b"folder_\xc3(old"))
    make_note_zip(tmp_path, "Week 1", "n-1", None)

    backup = scan(tmp_path)

    assert [n.id for n in backup.notes] == ["n-1"]
    assert [b.path.name for b in backup.bad] == ["Damaged_f-x"]
    assert backup.bad[0].reason


def test_renamed_note_newest_copy_wins(tmp_path):
    # The sync never deletes, so a note renamed in Notein leaves its old zip behind.
    # The old copy sorts first by file name here; it must still lose.
    make_folder_zip(tmp_path, "Archive", "f-old", None)
    make_folder_zip(tmp_path, "DBA LGT Notes", "f-lgt", None)
    stale = make_note_zip(tmp_path, "A old title", "n-1", "f-old", modified=100)
    current = make_note_zip(tmp_path, "W1 new title", "n-1", "f-lgt", modified=200)

    backup = scan(tmp_path)

    assert len(backup.notes) == 1
    note = backup.notes[0]
    assert (note.title, note.modified, note.zip_path) == ("W1 new title", 200, current)
    assert backup.folder_path(note) == ("DBA LGT Notes",)
    assert [b.path for b in backup.bad] == [stale]
    assert "W1 new title_n-1" in backup.bad[0].reason


def test_renamed_folder_newest_copy_wins(tmp_path):
    stale = make_folder_zip(tmp_path, "AAA stale", "f-lgt", None)
    current = make_folder_zip(tmp_path, "DBA LGT Notes", "f-lgt", None)
    for path, modified in ((stale, 100), (current, 200)):
        with zipfile.ZipFile(path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                if name not in ("folder_labels.json", "folder_tn_f-lgt"):
                    meta = json.loads(data)
                    meta["lastModificationTime"] = modified
                    data = json.dumps(meta)
                zf.writestr(name, data)
    make_note_zip(tmp_path, "Week 1", "n-1", "f-lgt")

    backup = scan(tmp_path)

    assert backup.folders["f-lgt"].title == "DBA LGT Notes"
    assert backup.folder_path(backup.notes[0]) == ("DBA LGT Notes",)
    assert [b.path for b in backup.bad] == [stale]


def test_zip_without_meta_or_folder_reported(tmp_path):
    with zipfile.ZipFile(tmp_path / "Mystery_123", "w") as z:
        z.writestr("something_else.json", "{}")
    with zipfile.ZipFile(tmp_path / "BadMeta_456", "w") as z:
        z.writestr("note_meta.json", "{not json")
    make_note_zip(tmp_path, "Week 1", "n-1", None)

    backup = scan(tmp_path)

    assert [n.id for n in backup.notes] == ["n-1"]
    bad = {b.path.name: b.reason for b in backup.bad}
    assert set(bad) == {"Mystery_123", "BadMeta_456"}
    assert all(bad.values())


def test_scan_reads_folder_member_not_thumbnail_or_labels(tmp_path):
    # Real folder zips also hold folder_labels.json and a PNG folder_tn_<id>.
    z = make_folder_zip(tmp_path, "CA1 Question Sheets ", "f-cs1q", "f-ca1")
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
    assert names == {"folder_CA1 Question Sheets ", "folder_labels.json", "folder_tn_f-cs1q"}

    backup = scan(tmp_path)

    assert backup.folders == {
        "f-cs1q": FolderEntry(id="f-cs1q", title="CA1 Question Sheets ", parent_id="f-ca1")
    }


def test_factory_note_zip_has_real_schema_and_pages(tmp_path):
    pages = [{"id": "p-1"}, {"id": "p-2", "width": 1315, "orientation": "HORIZONTAL"}]
    z = make_note_zip(tmp_path, "Week 1", "n-1", "f-1", pages=pages, pdf=b"%PDF-1.4 fake")
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("note_meta.json"))
        db_bytes = zf.read("note_database_note_n-1_db")
    assert meta["id"] == "n-1"
    assert meta["totalPagesCount"] == 2
    assert any(n.startswith("note_pdf_") and n.endswith(".pdf") for n in names)

    db = tmp_path / "copy_db"
    db.write_bytes(db_bytes)
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {
        "NoteContentEntity", "PageEntity", "StrokeEntity", "ShapeEntity", "ImageEntity",
        "TextBoxEntity", "PdfInfoEntity", "PageLayerEntity",
    } <= tables
    (page_list,) = con.execute("select page_list from NoteContentEntity").fetchone()
    assert json.loads(page_list) == ["p-1", "p-2"]
    rows = con.execute("select id, paper_spec, page_orientation from PageEntity").fetchall()
    spec = {r[0]: (json.loads(r[1]), r[2]) for r in rows}
    assert spec["p-1"][0]["width"] == 1240 and spec["p-1"][0]["height"] == 1754
    assert spec["p-2"][0]["width"] == 1315 and spec["p-2"][1] == "HORIZONTAL"
    con.close()


@pytest.mark.realdata
def test_real_backup_counts(real_sync_dir, real_facts):
    backup = scan(real_sync_dir)

    assert len(backup.notes) == real_facts["note_count"]
    assert backup.bad == []
    folder = tuple(real_facts["sample_folder_path"])
    assert [n for n in backup.notes if backup.folder_path(n) == folder], (
        "expected at least one note in " + "/".join(folder))
