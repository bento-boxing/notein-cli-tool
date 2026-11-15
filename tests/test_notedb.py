import os
import sqlite3
import tempfile
import time
import zipfile

import pymupdf
import pytest

from notes_cli import notedb
from notes_cli.backup import scan
from notes_cli.notedb import Image, Line, Note, Page, Stroke, TextBox, load_note

from factory import make_note_zip

BLACK = -16777216
WHITE = -1
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"SOURCE PAGE {n + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _load(tmp_path, **kwargs) -> Note:
    pages = kwargs.pop("pages", None)
    return load_note(make_note_zip(tmp_path, "Week 1", "n-1", None, pages=pages, **kwargs))


def test_pages_in_page_list_order(tmp_path):
    # Rows are inserted p1, p2, p3; page_list says p3, p1, p2.
    pages = [{"id": "p1", "width": 1001}, {"id": "p2", "width": 1002}, {"id": "p3", "width": 1003}]
    note = _load(
        tmp_path,
        pages=pages,
        extra_sql=["UPDATE NoteContentEntity SET page_list = '[\"p3\",\"p1\",\"p2\"]'"],
    )

    assert [p.width for p in note.pages] == [1003, 1001, 1002]
    assert note.id == "n-1"
    assert note.title == "Week 1"
    assert note.warnings == []


def test_blank_a4_page(tmp_path):
    note = _load(tmp_path)

    assert note.pages == [
        Page(width=1240, height=1754, background=WHITE, pdf_page=None,
             strokes=[], lines=[], images=[], texts=[])
    ]
    assert note.pdf is None


def test_json_stroke_loaded(tmp_path):
    stroke = {"points": [(10, 20, 0.5), (30, 40, 0.7)], "color": BLACK, "width": 2.6}
    note = _load(tmp_path, pages=[{"strokes": [stroke]}])

    assert note.pages[0].strokes == [
        Stroke(points=[(10.0, 20.0, 0.5), (30.0, 40.0, 0.7)], color=BLACK, width=2.6)
    ]
    assert note.warnings == []


def test_strokes_keep_drawing_order(tmp_path):
    strokes = [{"points": [(n, n, 1.0)], "color": -n} for n in range(1, 6)]
    note = _load(tmp_path, pages=[{"strokes": strokes}])

    assert [s.color for s in note.pages[0].strokes] == [-1, -2, -3, -4, -5]


def test_straight_line_stroke_type_loaded(tmp_path):
    # Real notes hold a few `type: 5` strokes: two-point straight lines.
    stroke = {"points": [(10, 20, 1.0), (300, 20, 1.0)], "type": 5}
    note = _load(tmp_path, pages=[{"strokes": [stroke]}])

    assert note.pages[0].strokes[0].points == [(10.0, 20.0, 1.0), (300.0, 20.0, 1.0)]
    assert note.warnings == []


def test_shape_line_loaded(tmp_path):
    line = {"p1": (174.2, 371.8), "p2": (871.5, 371.8), "color": -14803426, "width": 2.1}
    note = _load(tmp_path, pages=[{"lines": [line]}])

    assert note.pages[0].lines == [
        Line(p1=(174.2, 371.8), p2=(871.5, 371.8), color=-14803426, width=2.1)
    ]
    assert note.warnings == []


def test_unsupported_shape_warns(tmp_path):
    note = _load(tmp_path, pages=[{"lines": [{"p1": (0, 0), "p2": (5, 5), "type": 3}] * 2}])

    assert note.pages[0].lines == []
    assert len(note.warnings) == 1
    assert "shape" in note.warnings[0] and "type 3" in note.warnings[0]


def test_image_bytes_and_rect(tmp_path):
    image = {"id": "img-1", "rect": (41.5, 22.5, 305.0, 125.25)}
    note = _load(tmp_path, pages=[{"images": [image]}], images={"img-1": PNG})

    assert note.pages[0].images == [
        Image(data=PNG, rect=(41.5, 22.5, 305.0, 125.25), rotation=0.0)
    ]
    assert note.warnings == []


def test_linked_image_placed_on_its_page(tmp_path):
    # An image linked in from another note: ImageEntity.page_id is "link_ref" and a
    # DualLinkRefEntity row names the page it sits on.
    image = {"id": "img-link", "rect": (41, 22, 305, 125), "link": True}
    note = _load(tmp_path, pages=[{}, {"images": [image]}], images={"img-link": PNG})

    assert note.pages[0].images == []
    assert note.pages[1].images == [Image(data=PNG, rect=(41.0, 22.0, 305.0, 125.0), rotation=0.0)]
    assert note.warnings == []


def test_missing_image_warns(tmp_path):
    images = [{"id": "img-here"}, {"id": "img-gone"}]
    note = _load(tmp_path, pages=[{"images": images}], images={"img-here": PNG})

    assert len(note.pages[0].images) == 1
    assert len(note.warnings) == 1
    assert "image" in note.warnings[0] and "not in backup" in note.warnings[0]


def test_textbox_html_stripped(tmp_path):
    html = ('<div align="left"><font style="font-size:30px;legacy-size:false;color:#000000">'
            "Mark  Week</font></div>")
    text = {"html": html, "rect": (687.25, 1582.5, 1264.25, 1623.5), "size": 30.0}
    note = _load(tmp_path, pages=[{"texts": [text]}])

    assert note.pages[0].texts == [
        TextBox(text="Mark  Week", rect=(687.25, 1582.5, 1264.25, 1623.5), size=30.0, color=BLACK)
    ]


def test_textbox_style_block_breaks_and_html_size(tmp_path):
    # Shaped like the real PRG W10 text box: a <style> block, <br/> line breaks, a BOM, and
    # an HTML font size (51px) that differs from the text_size column (30) and fits the box.
    html = (
        "<style>\n  ul {\n      padding-left: 0;\n  }\n</style>\n"
        '<font style="font-size:51px;legacy-size:false"><font style="color:#3b3b3b">'
        "First  line &amp; more.<br/>\n</font></font>"
        '<font style="font-size:51px;legacy-size:false"><font style="color:#3b3b3b">﻿<br/>\n'
        "</font></font>"
        '<font style="font-size:51px;legacy-size:false"><font style="color:#3b3b3b">﻿ '
        "Second paragraph.</font></font>"
    )
    note = _load(tmp_path, pages=[{"texts": [{"html": html, "size": 30.0}]}])

    (box,) = note.pages[0].texts
    assert box.text == "First  line & more.\n\nSecond paragraph."
    assert box.size == 51.0
    assert box.color == 0xFF3B3B3B - (1 << 32)


def test_pdf_page_mapping(tmp_path):
    pdf = _pdf(3)
    pages = [{"pdf_page": 2}, {}, {"pdf_page": 0}]
    note = _load(tmp_path, pages=pages, pdf=pdf)

    assert note.pdf == pdf
    assert [(p.pdf_page, p.background) for p in note.pages] == [(2, None), (None, WHITE), (0, None)]
    assert note.warnings == []


def test_pdf_path_read_from_database_not_meta(tmp_path):
    # note_meta.json's pdfPath is empty in every real note; without the page theme's copy
    # the path still comes from PdfInfoEntity.
    pdf = _pdf(1)
    z = make_note_zip(tmp_path, "W1", "n-1", None, pages=[{"pdf_page": 0}], pdf=pdf,
                      pdf_path_in_theme=False)
    with zipfile.ZipFile(z) as zf:
        assert '"pdfPath":""' in zf.read("note_meta.json").decode()

    note = load_note(z)

    assert note.pdf == pdf
    assert note.pages[0].pdf_page == 0
    assert note.warnings == []


def test_unbundled_pdf_warns_blank(tmp_path):
    note = _load(tmp_path, pages=[{"pdf_page": 0}, {"pdf_page": 1}])

    assert note.pdf is None
    assert [(p.pdf_page, p.background) for p in note.pages] == [(None, WHITE), (None, WHITE)]
    assert len(note.warnings) == 1
    assert "imported PDF not in backup" in note.warnings[0]


def test_background_colour(tmp_path):
    note = _load(tmp_path, pages=[{"background": -198156}, {"background": -5443}])

    assert [p.background for p in note.pages] == [-198156, -5443]


@pytest.mark.parametrize(
    "theme",
    [
        "NULL",
        "'{\"type\":\"com.orion.notein.domain.models.papertheme.FancyPaperTheme\"}'",
        "'{\"type\":\"x.ColorPaperTheme\",\"color\":-5443}'",
        "'not json'",
    ],
)
def test_other_paper_themes_do_not_crash(tmp_path, theme):
    note = _load(tmp_path, extra_sql=[f"UPDATE PageEntity SET paper_theme = {theme}"])

    (page,) = note.pages
    expected = -5443 if "ColorPaperTheme" in theme else WHITE
    assert page.background == expected
    assert (page.width, page.height, page.pdf_page) == (1240, 1754, None)


def test_invisible_layer_skipped(tmp_path):
    hidden = {"layer": "hidden"}
    page = {
        "strokes": [
            {"points": [(1, 1, 1)], "color": -1, **hidden},
            {"points": [(2, 2, 1)], "color": -2},
            # Real notes put some elements on a layer id with no PageLayerEntity row.
            {"points": [(3, 3, 1)], "color": -3, "layer": "unspecified"},
        ],
        "lines": [{"p1": (0, 0), "p2": (1, 1), **hidden}],
        "images": [{"id": "img-1", **hidden}],
        "texts": [{"html": "secret", **hidden}],
    }
    note = _load(tmp_path, pages=[page], images={"img-1": PNG}, layers={"hidden": False})

    (p,) = note.pages
    assert [s.color for s in p.strokes] == [-2, -3]
    assert (p.lines, p.images, p.texts) == ([], [], [])
    assert note.warnings == []


def test_trashed_page_skipped(tmp_path):
    pages = [{"width": 1001}, {"width": 1002, "trashed": True,
                               "strokes": [{"points": [(1, 1, 1)]}]}, {"width": 1003}]
    note = _load(tmp_path, pages=pages)

    assert [p.width for p in note.pages] == [1001, 1003]


def test_horizontal_page_size(tmp_path):
    # Step 1(a): paper_spec stores the portrait size; HORIZONTAL pages are landscape.
    pages = [
        {"width": 1315, "height": 1754, "orientation": "HORIZONTAL", "name": "PDF"},
        {"orientation": "HORIZONTAL"},
        {"width": 986, "height": 1754, "orientation": "HORIZONTAL", "pdf_page": 0},
        {},
    ]
    note = _load(tmp_path, pages=pages, pdf=_pdf(1))

    assert [(p.width, p.height) for p in note.pages] == [
        (1754, 1315), (1754, 1240), (1754, 986), (1240, 1754)
    ]


def test_unbounded_page_fits_content(tmp_path):
    # Content spans x 498..1502, y 598..2602 once the stroke's half-width (2) is included.
    page = {
        "unbounded": True,
        "strokes": [{"points": [(500, 600, 1), (1500, 2600, 1)], "width": 4.0}],
        "lines": [{"p1": (600, 700), "p2": (900, 700), "width": 2.0}],
        "images": [{"id": "img-1", "rect": (1000, 1000, 1200, 1100)}],
        "texts": [{"html": "hi", "rect": (700, 2000, 900, 2050)}],
    }
    note = _load(tmp_path, pages=[page, {"unbounded": True}], images={"img-1": PNG})

    p, empty = note.pages
    # All values here are exact in binary floating point.
    assert (p.width, p.height) == (1004 + 80, 2004 + 80)
    dx, dy = 40 - 498, 40 - 598
    assert p.strokes[0].points == [(500 + dx, 600 + dy, 1), (1500 + dx, 2600 + dy, 1)]
    assert (p.lines[0].p1, p.lines[0].p2) == ((600 + dx, 700 + dy), (900 + dx, 700 + dy))
    assert p.images[0].rect == (1000 + dx, 1000 + dy, 1200 + dx, 1100 + dy)
    assert p.texts[0].rect == (700 + dx, 2000 + dy, 900 + dx, 2050 + dy)
    # An unbounded page with no content keeps its paper_spec size.
    assert (empty.width, empty.height) == (1920, 1920)
    assert note.warnings == []


def test_blob_strokes_warn_when_no_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(notedb, "BLOB_DECODER", None)
    strokes = [{"blob": b"\x0a\x01a"}, {"points": [(1, 2, 0.5)]}, {"blob": b"\x0a\x01b"}]
    note = _load(tmp_path, pages=[{"strokes": strokes}, {"strokes": [{"blob": b"x"}]}])

    assert [s.points for s in note.pages[0].strokes] == [[(1.0, 2.0, 0.5)]]
    assert note.pages[1].strokes == []
    assert len(note.warnings) == 1
    assert "3" in note.warnings[0] and "decoder" in note.warnings[0]


def _stub_decoder(blob: bytes) -> Stroke:
    if blob == b"bad":
        raise ValueError("malformed")
    return Stroke(points=[(float(blob[0]), float(blob[1]), 1.0)], color=-7, width=3.0)


def test_blob_strokes_use_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(notedb, "BLOB_DECODER", _stub_decoder)
    strokes = [{"blob": bytes([1, 2])}, {"points": [(5, 6, 0.5)]}, {"blob": bytes([3, 4])}]
    note = _load(tmp_path, pages=[{"strokes": strokes}])

    assert [s.points for s in note.pages[0].strokes] == [
        [(1.0, 2.0, 1.0)], [(5.0, 6.0, 0.5)], [(3.0, 4.0, 1.0)]
    ]
    assert note.pages[0].strokes[0] == Stroke(points=[(1.0, 2.0, 1.0)], color=-7, width=3.0)
    assert note.warnings == []


def test_blob_decoder_failure_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(notedb, "BLOB_DECODER", _stub_decoder)
    strokes = [{"blob": bytes([1, 2])}, {"blob": b"bad"}, {"blob": b"bad"}]
    note = _load(tmp_path, pages=[{"strokes": strokes}])

    assert len(note.pages[0].strokes) == 1
    assert len(note.warnings) == 1
    assert "2" in note.warnings[0] and "could not be decoded" in note.warnings[0]


def test_row_only_in_wal_is_read(tmp_path):
    pages = [
        {"id": "p1", "strokes": [{"points": [(1, 1, 1)]}]},
        {"id": "p2", "wal_only": True, "strokes": [{"points": [(7, 8, 0.5)]}]},
    ]
    z = make_note_zip(tmp_path, "W1", "n-1", None, pages=pages)

    # The factory mirrors the real layout: db, -wal and -shm members, and p2's rows are
    # only in the -wal member.
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert {"note_database_note_n-1_db-wal", "note_database_note_n-1_db-shm"} <= names
        db_only = tmp_path / "db-only" / "note_db"
        db_only.parent.mkdir()
        db_only.write_bytes(zf.read("note_database_note_n-1_db"))
    con = sqlite3.connect(f"file:{db_only}?mode=ro", uri=True)
    assert [r[0] for r in con.execute("SELECT id FROM PageEntity")] == ["p1"]
    assert con.execute("SELECT count(*) FROM StrokeEntity").fetchone() == (1,)
    con.close()

    note = load_note(z)

    assert len(note.pages) == 2
    assert note.pages[1].strokes == [Stroke(points=[(7.0, 8.0, 0.5)], color=BLACK, width=2.6)]


def test_rollback_journal_note_loads(tmp_path):
    z = make_note_zip(tmp_path, "W1", "n-1", None, journal="rollback",
                      pages=[{"strokes": [{"points": [(1, 2, 0.5)]}]}])
    with zipfile.ZipFile(z) as zf:
        assert not any(n.endswith(("-wal", "-shm")) for n in zf.namelist())

    note = load_note(z)

    assert note.pages[0].strokes[0].points == [(1.0, 2.0, 0.5)]


def test_note_content_row_chosen_by_note_id(tmp_path):
    # Real notes copied from another note keep the old note's NoteContentEntity row too.
    other = (
        "INSERT INTO NoteContentEntity (id, page_list, page_layer_list, outline_list,"
        " default_page_id, pdf_info_id, saved_page_index, activated_page_layer_id, zoom,"
        " unbounded_page_offset_x, unbounded_page_offset_y, unbounded_note, creation_time,"
        " last_modification_time, extras) VALUES ('aaa-older', '[\"p2\",\"p1\"]', '[]', '[]',"
        " '', NULL, 0, 'default', 1.0, 0, 0, 0, 0, 0, '{}')"
    )
    note = _load(tmp_path, pages=[{"id": "p1", "width": 1001}, {"id": "p2", "width": 1002}],
                 extra_sql=[other])

    assert [p.width for p in note.pages] == [1001, 1002]


def test_unrendered_content_table_warns(tmp_path):
    sql = [
        "CREATE TABLE `GlyphEntity` (`id` TEXT NOT NULL, `page_id` TEXT NOT NULL, PRIMARY KEY(`id`))",
        "INSERT INTO GlyphEntity VALUES ('g1', 'n-1-p1'), ('g2', 'n-1-p1')",
        "CREATE TABLE `TableBlockEntity` (`id` TEXT NOT NULL, PRIMARY KEY(`id`))",
        "CREATE TABLE `OutlineEntity` (`id` TEXT NOT NULL, PRIMARY KEY(`id`))",
        "INSERT INTO OutlineEntity VALUES ('o1')",
    ]
    note = _load(tmp_path, extra_sql=sql)

    # Non-empty GlyphEntity warns; an empty table and a table of contents do not.
    assert len(note.warnings) == 1
    assert "GlyphEntity" in note.warnings[0] and "2" in note.warnings[0]


def test_backup_zip_untouched_and_temp_dir_removed(tmp_path, monkeypatch):
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    backup = tmp_path / "backup"
    backup.mkdir()
    z = make_note_zip(backup, "W1", "n-1", None, pages=[{"strokes": [{"points": [(1, 1, 1)]}]}])
    old = time.time() - 1000
    os.utime(z, (old, old))
    before = (z.read_bytes(), z.stat().st_mtime, sorted(os.listdir(backup)))

    load_note(z)

    assert (z.read_bytes(), z.stat().st_mtime, sorted(os.listdir(backup))) == before
    assert os.listdir(temp_root) == []


def test_temp_dir_removed_on_error(tmp_path, monkeypatch):
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    z = make_note_zip(tmp_path, "W1", "n-1", None)
    broken = tmp_path / "Broken_n-2"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("_db"):
                data = b"this is not a database" * 100
            dst.writestr(name, data)

    with pytest.raises(Exception):
        load_note(broken)

    assert os.listdir(temp_root) == []


def test_zip_without_database_raises(tmp_path):
    z = tmp_path / "Meta only_n-1"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("note_meta.json", '{"id":"n-1","title":"Meta only"}')

    with pytest.raises(ValueError, match="database"):
        load_note(z)


def test_malformed_rows_are_skipped_with_warnings_not_raised(tmp_path):
    good = {"points": [(10, 20, 0.5), (30, 40, 0.7)], "color": BLACK, "width": 2.6}
    sql = [
        # A stroke whose colour is Infinity, one with a NaN coordinate, one with no points.
        "UPDATE StrokeEntity SET record_json = replace(record_json, '-16777216', 'Infinity')"
        " WHERE rowid = 2",
        "UPDATE StrokeEntity SET record_json = replace(record_json, '\"x\":10', '\"x\":NaN')"
        " WHERE rowid = 3",
        "UPDATE StrokeEntity SET record_json = '{\"type\":1,\"color\":-1,\"width\":2,\"points\":[]}'"
        " WHERE rowid = 4",
        "UPDATE ShapeEntity SET color = 'abc'",
        "UPDATE ImageEntity SET rotation = 'abc'",
        "UPDATE TextBoxEntity SET text_size = 'abc'",
    ]
    page = {
        "strokes": [dict(good), dict(good), dict(good), dict(good)],
        "lines": [{"p1": (1, 2), "p2": (3, 4)}],
        "images": [{"id": "img-1"}],
        "texts": [{"html": "plain"}],
    }
    note = _load(tmp_path, pages=[page], images={"img-1": PNG}, extra_sql=sql)

    (loaded,) = note.pages
    assert len(loaded.strokes) == 1
    assert loaded.lines == []
    assert [i.rotation for i in loaded.images] == [0.0]
    assert [t.size for t in loaded.texts] == [30.0]
    text = "\n".join(note.warnings)
    assert "2 stroke(s) with unreadable record_json" in text
    assert "1 stroke row(s) with no ink data" in text
    assert "1 shape(s)" in text


def test_elements_on_unknown_page_warn(tmp_path):
    stroke = {"points": [(10, 20, 0.5), (30, 40, 0.7)], "color": BLACK, "width": 2.6}
    sql = ["UPDATE StrokeEntity SET page_id = 'no-such-page' WHERE rowid = 2"]
    note = _load(tmp_path, pages=[{"strokes": [stroke, stroke]}], extra_sql=sql)

    assert len(note.pages[0].strokes) == 1
    assert any("1 element(s) on pages that are not in the note's page list" in w
               for w in note.warnings)


def test_pdf_page_past_end_is_blank_with_warning(tmp_path):
    pdf = _pdf(2)
    note = _load(tmp_path, pages=[{"pdf_page": 1}, {"pdf_page": 5}], pdf=pdf)

    assert [(p.pdf_page, p.background) for p in note.pages] == [(1, None), (None, WHITE)]
    assert any("past the end of the imported PDF" in w for w in note.warnings)


def test_unreadable_pdf_is_blank_with_warning(tmp_path):
    note = _load(tmp_path, pages=[{"pdf_page": 0}], pdf=b"not a pdf at all")

    assert note.pdf is None
    assert [(p.pdf_page, p.background) for p in note.pages] == [(None, WHITE)]
    assert any("imported PDF cannot be read" in w for w in note.warnings)


def test_pdf_without_pages_is_blank_with_warning(tmp_path):
    # A damaged PDF can open "successfully" with no pages at all.
    empty = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    with pymupdf.open(stream=empty, filetype="pdf") as doc:
        assert doc.page_count == 0
    note = _load(tmp_path, pages=[{"pdf_page": 0}], pdf=empty)

    assert note.pdf is None
    assert [(p.pdf_page, p.background) for p in note.pages] == [(None, WHITE)]
    assert len(note.warnings) == 1 and "imported PDF" in note.warnings[0]


def test_textbox_size_and_colour_ignore_style_block(tmp_path):
    html = (
        "<style>p{color:#ff0000;font-size:99px}</style>"
        '<font style="font-size:20px;color:#000000">x</font>'
    )
    note = _load(tmp_path, pages=[{"texts": [{"html": html}]}])

    (box,) = note.pages[0].texts
    assert (box.size, box.color) == (20.0, BLACK)


@pytest.mark.realdata
def test_every_real_note_loads(real_sync_dir, real_facts):
    backup = scan(real_sync_dir)
    assert len(backup.notes) == real_facts["note_count"]

    total_pages = 0
    for entry in backup.notes:
        note = load_note(entry.zip_path)
        assert note.id == entry.id
        assert all(p.width > 0 and p.height > 0 for p in note.pages)
        assert not any("no decoder" in w for w in note.warnings), entry.title
        total_pages += len(note.pages)

    assert total_pages > real_facts["min_total_pages"]
