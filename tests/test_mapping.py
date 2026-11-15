from pathlib import Path

import pytest

from notes_cli.backup import scan
from notes_cli.cli import main
from notes_cli.mapping import (
    REASON_NOT_SUBFOLDER,
    REASON_OUTSIDE,
    REASON_TRASH,
    REASON_UNCONFIGURED,
    Placement,
    plan,
    safe_name,
)

from factory import make_folder_zip, make_note_zip


def folders(sync: Path, *path: str) -> str:
    """Create folder zips for each level of `path` (reusing existing ones) and return the
    leaf folder's id. Ids are derived from the path, so shared prefixes share folders."""
    sync.mkdir(parents=True, exist_ok=True)
    parent = None
    for depth in range(1, len(path) + 1):
        folder_id = "f:" + "/".join(path[:depth])
        if not any(p.name.endswith("_" + folder_id.replace("/", "_")) for p in sync.iterdir()):
            make_folder_zip(sync, path[depth - 1], folder_id, parent)
        parent = folder_id
    return parent


def note(sync: Path, title: str, id: str, *path: str, **kw) -> None:
    sync.mkdir(parents=True, exist_ok=True)
    parent = folders(sync, *path) if path else None
    make_note_zip(sync, title, id, parent, **kw)


def placements(cfg) -> dict[str, Placement]:
    result = plan(scan(cfg.sync_dir), cfg)
    return {p.note.id: p for p in result}


def rel(cfg, p: Placement) -> str:
    return p.dest.relative_to(cfg.data_root).as_posix()


DBA = ("Uni CS", "Year 1", "Semester 2", "DBA")


def test_lecture_lgt_sgt_mapped(cfg):
    s = cfg.sync_dir
    note(s, "DBA Lecture W1", "n-lec", *DBA, "DBA Lecture Notes")
    note(s, "DBA LGT W1", "n-lgt", *DBA, "DBA LGT Notes")
    note(s, "DBA SGT 1", "n-sgt", *DBA, "DBA SGT")

    got = placements(cfg)

    assert rel(cfg, got["n-lec"]) == "CS Degree/Year 1/DBA/Lecture Notes/DBA Lecture W1.pdf"
    assert rel(cfg, got["n-lgt"]) == "CS Degree/Year 1/DBA/LGT/DBA LGT W1.pdf"
    assert rel(cfg, got["n-sgt"]) == "CS Degree/Year 1/DBA/SGT/DBA SGT 1.pdf"
    assert all(p.reason is None for p in got.values())


def test_no_semester_level(cfg):
    note(cfg.sync_dir, "SWE S1W2 DevOps\n", "n-1", "Uni CS", "Year 2", "SWE", "SWE Lecture ")

    p = placements(cfg)["n-1"]

    assert p.reason is None
    assert rel(cfg, p) == "CS Degree/Year 2/SWE/Lecture Notes/SWE S1W2 DevOps.pdf"


def test_subfolder_match_ignores_case_and_spacing(cfg):
    s = cfg.sync_dir
    note(s, "A", "n-1", *DBA, "dba  lgt   notes")
    note(s, "B", "n-2", *DBA, "DBASGT")
    note(s, "C", "n-3", "Uni CS", "Year 1", "Semester 2", "dba", "DBA Lecture")

    got = placements(cfg)

    assert rel(cfg, got["n-1"]) == "CS Degree/Year 1/DBA/LGT/A.pdf"
    assert rel(cfg, got["n-2"]) == "CS Degree/Year 1/DBA/SGT/B.pdf"
    assert rel(cfg, got["n-3"]) == "CS Degree/Year 1/DBA/Lecture Notes/C.pdf"


def test_fm1_slides_are_lecture_notes(cfg):
    note(cfg.sync_dir, "fm1_week4", "n-1", "Uni CS", "Year 1", "Semester 1", "FM1", "FM1 Slides")

    p = placements(cfg)["n-1"]

    assert rel(cfg, p) == "CS Degree/Year 1/FM1/Lecture Notes/fm1_week4.pdf"


def test_revision_subtree_skipped(cfg):
    s = cfg.sync_dir
    rev = ("Uni CS", "Year 1", "Semester 2", "Revision")
    note(s, "DBA Rev LGT 1", "n-1", *rev, "DBA", "DBA LGT")
    note(s, "DBA Rev Lecture", "n-2", *rev, "DBA")

    got = placements(cfg)

    assert got["n-1"] == Placement(got["n-1"].note, None, "module not configured")
    assert got["n-2"].dest is None and got["n-2"].reason == "module not configured"


def test_module_in_wrong_year_skipped(cfg):
    note(cfg.sync_dir, "X", "n-1", "Uni CS", "Year 2", "DBA", "DBA LGT")

    p = placements(cfg)["n-1"]

    assert (p.dest, p.reason) == (None, REASON_UNCONFIGURED)


def test_note_directly_in_module_skipped(cfg):
    note(cfg.sync_dir, "CA1 Week 2 Exercises ", "n-1", "Uni CS", "Year 1", "Semester 1", "CA1")

    p = placements(cfg)["n-1"]

    assert (p.dest, p.reason) == (None, "not in a lecture/LGT/SGT folder")


def test_textbook_and_question_sheets_skipped(cfg):
    s = cfg.sync_dir
    note(s, "DBA Textbook 1", "n-1", *DBA, "DBA Textbook Notes")
    note(s, "CA1 Q1", "n-2", "Uni CS", "Year 1", "Semester 1", "CA1", "CA1 Question Sheets ")
    note(s, "PRG CW", "n-3", "Uni CS", "Year 1", "Semester 2", "PRG", "PRG CW Notes")
    note(s, "Deep", "n-4", *DBA, "DBA LGT Notes", "Week 1")

    got = placements(cfg)

    for note_id in ("n-1", "n-2", "n-3", "n-4"):
        assert (got[note_id].dest, got[note_id].reason) == (None, REASON_NOT_SUBFOLDER)


def test_notes_in_year_or_semester_folder_skipped(cfg):
    s = cfg.sync_dir
    note(s, "Timetable", "n-1", "Uni CS", "Year 1")
    note(s, "Plan", "n-2", "Uni CS", "Year 1", "Semester 2")
    note(s, "Loose", "n-3", "Uni CS")

    got = placements(cfg)

    assert got["n-1"].reason == REASON_NOT_SUBFOLDER
    assert got["n-2"].reason == REASON_NOT_SUBFOLDER
    assert got["n-3"].reason == REASON_OUTSIDE


def test_trash_skipped(cfg):
    note(cfg.sync_dir, "DBA LGT W1", "n-1", *DBA, "DBA LGT Notes", in_trash=True)

    p = placements(cfg)["n-1"]

    assert (p.dest, p.reason) == (None, "in trash")
    assert REASON_TRASH == "in trash"


def test_personal_notes_outside_tree(cfg):
    s = cfg.sync_dir
    note(s, "Project plan", "n-1", "Other Notes", "Projects")
    note(s, "DP tricks", "n-2", "Hobbies")
    note(s, "Quick Start Guide", "n-3")
    note(s, "Orphan", "n-4", "Year 1", "DBA", "DBA LGT")  # tree root missing

    got = placements(cfg)

    for note_id in ("n-1", "n-2", "n-3", "n-4"):
        assert (got[note_id].dest, got[note_id].reason) == (None, "outside degree tree")


def test_safe_name_cases():
    assert safe_name("a/b") == "a-b"
    assert safe_name(" DSA W9 ") == "DSA W9"
    assert safe_name("..x") == "x"
    assert safe_name("") == "untitled"
    assert safe_name("SWE S1W2 DevOps\n") == "SWE S1W2 DevOps"
    assert safe_name("a\\b:c") == "a-b-c"
    assert safe_name("tab\there") == "tab-here"
    assert safe_name("nul\x00") == "nul-"
    assert safe_name("What? <x> | \"y\" *") == "What- -x- - -y- -"
    assert safe_name(". . hidden") == "hidden"
    assert safe_name("...") == "untitled"
    assert safe_name("  \n ") == "untitled"
    assert safe_name("../../etc/passwd") == "-..-etc-passwd"


def test_safe_name_never_escapes_or_hides():
    for title in ("/", "..", ".", "a/../../b", "\\..\\x", " .bashrc ", "x" * 1000, "é" * 300):
        name = safe_name(title)
        assert name and not name.startswith(".")
        assert "/" not in name and "\\" not in name
        assert name == name.strip()
        assert len((name + " (12345678-1234-1234-1234-123456789012).pdf").encode()) <= 255


def test_same_title_same_folder_both_get_id_suffix(cfg):
    s = cfg.sync_dir
    note(s, "DBA LGT W1", "aaaaaaaa-1111", *DBA, "DBA LGT Notes")
    note(s, "DBA LGT W1 ", "bbbbbbbb-2222", *DBA, "DBA LGT Notes")
    note(s, "DBA LGT W1", "cccccccc-3333", *DBA, "DBA Lecture Notes")  # other folder: no clash

    got = placements(cfg)

    assert rel(cfg, got["aaaaaaaa-1111"]) == "CS Degree/Year 1/DBA/LGT/DBA LGT W1 (aaaaaaaa).pdf"
    assert rel(cfg, got["bbbbbbbb-2222"]) == "CS Degree/Year 1/DBA/LGT/DBA LGT W1 (bbbbbbbb).pdf"
    assert rel(cfg, got["cccccccc-3333"]) == "CS Degree/Year 1/DBA/Lecture Notes/DBA LGT W1.pdf"


def test_names_equal_after_safe_name_or_case_get_suffix(cfg):
    s = cfg.sync_dir
    note(s, "a/b", "11111111-a", *DBA, "DBA LGT Notes")
    note(s, "a-b", "22222222-b", *DBA, "DBA LGT Notes")
    note(s, "A-B", "33333333-c", *DBA, "DBA LGT Notes")  # differs only by case
    note(s, ".hidden", "44444444-d", *DBA, "DBA SGT")
    note(s, "hidden", "55555555-e", *DBA, "DBA SGT")

    got = placements(cfg)
    names = {k: p.dest.name for k, p in got.items()}

    assert names == {
        "11111111-a": "a-b (11111111).pdf",
        "22222222-b": "a-b (22222222).pdf",
        "33333333-c": "A-B (33333333).pdf",
        "44444444-d": "hidden (44444444).pdf",
        "55555555-e": "hidden (55555555).pdf",
    }


def test_suffix_collisions_fall_back_to_full_id(cfg):
    s = cfg.sync_dir
    lgt = (*DBA, "DBA LGT Notes")
    # Same title and the same first 8 characters of the id.
    note(s, "W1", "abcdefgh-0001", *lgt)
    note(s, "W1", "abcdefgh-0002", *lgt)
    # A title that equals another note's suffixed name.
    note(s, "W2", "12345678-aaaa", *lgt)
    note(s, "W2", "99999999-bbbb", *lgt)
    note(s, "W2 (12345678)", "77777777-cccc", *lgt)

    got = placements(cfg)
    dests = [p.dest for p in got.values()]

    assert len({str(d).casefold() for d in dests}) == len(dests)
    assert got["abcdefgh-0001"].dest.name == "W1 (abcdefgh-0001).pdf"
    assert got["abcdefgh-0002"].dest.name == "W1 (abcdefgh-0002).pdf"
    assert got["99999999-bbbb"].dest.name == "W2 (99999999).pdf"
    # Every name stays traceable to its note.
    for note_id, p in got.items():
        assert p.dest.name.startswith(("W1", "W2"))


def test_every_note_gets_exactly_one_placement(cfg):
    s = cfg.sync_dir
    note(s, "A", "n-1", *DBA, "DBA LGT Notes")
    note(s, "B", "n-2", "Other Notes")
    note(s, "C", "n-3", *DBA, "DBA LGT Notes", in_trash=True)

    backup = scan(s)
    result = plan(backup, cfg)

    assert sorted(p.note.id for p in result) == ["n-1", "n-2", "n-3"]
    for p in result:
        assert (p.dest is None) != (p.reason is None)
        assert p.reason in (None, REASON_TRASH, REASON_OUTSIDE, REASON_UNCONFIGURED, REASON_NOT_SUBFOLDER)


def test_organise_creates_folders_and_lists_everything(cfg_dir, capsys):
    sync = cfg_dir / "NoteInDataSync"
    note(sync, "DBA LGT W1", "n-1", *DBA, "DBA LGT Notes")
    note(sync, "DBA LGT W1", "n-2", *DBA, "DBA LGT Notes")
    note(sync, "SWE S1W2 DevOps\n", "n-3", "Uni CS", "Year 2", "SWE", "SWE Lecture ")
    note(sync, "DBA Rev", "n-4", "Uni CS", "Year 1", "Semester 2", "Revision", "DBA", "DBA LGT")
    note(sync, "Quick Start Guide", "n-5")
    note(sync, "Old", "n-6", *DBA, "DBA SGT", in_trash=True)
    (sync / "Broken_xyz").write_bytes(b"not a zip")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in sync.iterdir())

    assert main(["--data", str(cfg_dir), "organise"]) == 0
    out, err = capsys.readouterr()

    degree = cfg_dir / "CS Degree"
    for module, year in {"PRG": 1, "DBA": 1, "DSA": 1, "IST": 1, "CA1": 1, "ELC": 1,
                         "FM1": 1, "SWE": 2, "IAS": 2, "FM2": 2, "PAR": 2}.items():
        for sub in ("Lecture Notes", "LGT", "SGT"):
            folder = degree / f"Year {year}" / module / sub
            assert folder.is_dir(), folder
            assert list(folder.iterdir()) == []  # no PDFs written
    assert not list(cfg_dir.rglob("*.pdf"))
    assert sorted((p.name, p.stat().st_mtime_ns) for p in sync.iterdir()) == before

    lines = out.splitlines()
    assert "DBA LGT W1 -> CS Degree/Year 1/DBA/LGT/DBA LGT W1 (n-1).pdf" in lines
    assert "DBA LGT W1 -> CS Degree/Year 1/DBA/LGT/DBA LGT W1 (n-2).pdf" in lines
    assert "SWE S1W2 DevOps -> CS Degree/Year 2/SWE/Lecture Notes/SWE S1W2 DevOps.pdf" in lines
    assert any(l.startswith("DBA Rev -- skipped: module not configured") for l in lines)
    assert any(l.startswith("Quick Start Guide -- skipped: outside degree tree") for l in lines)
    assert any(l.startswith("Old -- skipped: in trash") for l in lines)
    assert any("Broken_xyz" in l and "not a readable zip" in l for l in lines)
    # One line per note: six notes, six note lines.
    assert sum((" -> " in l) or (" -- skipped: " in l) for l in lines) == 6
    assert "6 notes: 3 mapped, 3 skipped" in out
    assert "1 bad entr" in out
    assert "Traceback" not in err

    # Running again is safe and gives the same output.
    assert main(["--data", str(cfg_dir), "organise"]) == 0
    assert capsys.readouterr().out == out


def test_organise_shows_control_characters_on_one_line(cfg_dir, capsys):
    sync = cfg_dir / "NoteInDataSync"
    note(sync, "two\nlines", "n-1", "Other Notes")

    assert main(["--data", str(cfg_dir), "organise"]) == 0
    out = capsys.readouterr().out

    assert any(l.startswith("two\\nlines -- skipped: outside degree tree") for l in out.splitlines())


def test_organise_missing_sync_folder_one_line(cfg_dir, capsys):
    assert main(["--data", str(cfg_dir), "organise"]) == 1
    out, err = capsys.readouterr()

    assert "NoteInDataSync" in err
    assert "notes sync" in err
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err
    assert not (cfg_dir / "CS Degree").exists()


def test_organise_sync_path_is_a_file(cfg_dir, capsys):
    (cfg_dir / "NoteInDataSync").write_text("oops")

    assert main(["--data", str(cfg_dir), "organise"]) == 1
    err = capsys.readouterr().err

    assert "NoteInDataSync" in err
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err


@pytest.mark.realdata
def test_real_plan_totals(real_cfg, real_facts):
    backup = scan(real_cfg.sync_dir)
    result = plan(backup, real_cfg)

    assert sorted(p.note.id for p in result) == sorted(n.id for n in backup.notes)
    mapped = [p for p in result if p.dest is not None]
    assert len({str(p.dest).casefold() for p in mapped}) == len(mapped)

    def count(module: str, sub: str | None = None) -> int:
        base = real_cfg.module_dir(module)
        return sum(
            1 for p in mapped
            if p.dest.parent.parent == base and (sub is None or p.dest.parent.name == sub)
        )

    for module, sub, expected in real_facts["plan_counts"]:
        assert count(module, sub) == expected, (module, sub)


def test_safe_name_lone_surrogate():
    # json.loads can return a lone surrogate from an escape like "\ud83d".
    assert safe_name("W1 \ud83d") == "W1 -"
    safe_name("W1 \ud83d").encode("utf-8")
