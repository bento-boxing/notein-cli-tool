import pymupdf
import pytest

from notes_cli import render
from notes_cli.backup import scan
from notes_cli.notedb import Image, Line, Note, Page, Stroke, TextBox, load_note
from notes_cli.render import PX_TO_PT, render_pdf

WHITE = -1
BLACK = -16777216


def argb(value: int) -> int:
    """An unsigned 0xAARRGGBB literal as the signed int the app stores."""
    return value - (1 << 32) if value >= 1 << 31 else value


def png(width=40, height=20, alpha=False) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), alpha)
    pix.set_rect(pix.irect, (200, 30, 30, 128) if alpha else (200, 30, 30))
    return pix.tobytes("png")


def page(**kwargs) -> Page:
    values = dict(width=1240, height=1754, background=WHITE, pdf_page=None)
    values.update(kwargs)
    return Page(**values)


def note(*pages: Page, pdf: bytes | None = None) -> Note:
    return Note("n-1", "Week 1", list(pages), pdf, [])


def render_open(tmp_path, n: Note) -> pymupdf.Document:
    out = tmp_path / "out.pdf"
    render_pdf(n, out)
    return pymupdf.open(out)


def test_page_count_and_size(tmp_path):
    n = note(page(), page(width=1754, height=1240), page(width=3114.6, height=3654.1))

    with render_open(tmp_path, n) as doc:
        assert doc.page_count == 3
        sizes = [(p.rect.width, p.rect.height) for p in doc]

    assert sizes[0] == pytest.approx((595.2, 841.92), abs=0.1)
    assert sizes[1] == pytest.approx((841.92, 595.2), abs=0.1)
    assert sizes[2] == pytest.approx((3114.6 * PX_TO_PT, 3654.1 * PX_TO_PT), abs=0.1)
    assert PX_TO_PT == 72 / 150


def test_stroke_is_vector_and_coloured(tmp_path):
    stroke = Stroke([(100, 200, 0.5), (300, 250, 0.6), (400, 500, 0.7)], argb(0xFFFF0000), 2.6)
    n = note(page(strokes=[stroke]))

    with render_open(tmp_path, n) as doc:
        drawings = doc[0].get_drawings()
        images = doc[0].get_images()

    assert len(drawings) == 1
    (d,) = drawings
    assert d["type"] == "s"
    assert d["color"] == pytest.approx((1, 0, 0))
    assert d["width"] == pytest.approx(2.6 * 0.48, abs=0.01)
    assert d["stroke_opacity"] == pytest.approx(1.0)
    assert d["lineCap"] == (1, 1, 1)  # round
    assert d["lineJoin"] == 1  # round
    # One polyline through the three points, scaled to points, not rescaled to fit.
    assert [item[0] for item in d["items"]] == ["l", "l"]
    corners = [d["items"][0][1], d["items"][0][2], d["items"][1][2]]
    assert [(p.x, p.y) for p in corners] == [
        pytest.approx((x * 0.48, y * 0.48), abs=0.01) for x, y in [(100, 200), (300, 250), (400, 500)]
    ]
    assert images == []


def test_stroke_alpha_becomes_opacity(tmp_path):
    half = Stroke([(100, 100, 1), (200, 200, 1)], argb(0x8000FF00), 3)
    opaque = Stroke([(300, 300, 1), (400, 400, 1)], BLACK, 3)  # opacity must go back to 1
    n = note(page(strokes=[half, opaque]))

    with render_open(tmp_path, n) as doc:
        first, second = doc[0].get_drawings()

    assert first["stroke_opacity"] == pytest.approx(0.5, abs=0.01)
    assert first["color"] == pytest.approx((0, 1, 0))
    assert second["stroke_opacity"] == pytest.approx(1.0)
    assert second["color"] == pytest.approx((0, 0, 0))


def test_single_point_stroke_draws_dot(tmp_path):
    n = note(page(strokes=[Stroke([(100, 200, 0.5)], BLACK, 10)]))

    with render_open(tmp_path, n) as doc:
        drawings = doc[0].get_drawings()

    assert len(drawings) == 1
    (d,) = drawings
    assert d["type"] == "f"
    assert d["fill"] == pytest.approx((0, 0, 0))
    rect = d["rect"]
    assert ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2) == pytest.approx((48, 96), abs=0.02)
    assert (rect.width, rect.height) == pytest.approx((10 * 0.48, 10 * 0.48), abs=0.05)


def test_stroke_of_repeated_point_draws_dot(tmp_path):
    # Several real "dot" strokes have many points, all identical.
    dot = Stroke([(500, 600, 0.1), (500, 600, 0.2), (500, 600, 0.3)], argb(0x80FF0000), 4)
    n = note(page(strokes=[dot]))

    with render_open(tmp_path, n) as doc:
        (d,) = doc[0].get_drawings()

    assert d["type"] == "f"
    assert d["fill"] == pytest.approx((1, 0, 0))
    assert d["fill_opacity"] == pytest.approx(0.5, abs=0.01)
    assert (d["rect"].x0 + d["rect"].x1) / 2 == pytest.approx(240, abs=0.02)
    assert d["rect"].width == pytest.approx(4 * 0.48, abs=0.05)


def test_stroke_without_points_not_drawn(tmp_path):
    n = note(page(strokes=[Stroke([], BLACK, 3), Stroke([(1, 1, 1), (9, 9, 1)], BLACK, 3)]))

    with render_open(tmp_path, n) as doc:
        assert len(doc[0].get_drawings()) == 1


def test_blank_note_makes_blank_pages(tmp_path):
    n = note(page(), page(), page())

    with render_open(tmp_path, n) as doc:
        assert doc.page_count == 3
        for p in doc:
            assert p.get_drawings() == []
            assert p.get_images() == []
            assert p.get_text() == ""
        assert doc.is_pdf


def test_background_colour(tmp_path):
    n = note(page(background=argb(0xFFFFF0C0)), page(background=WHITE), page(background=None))

    with render_open(tmp_path, n) as doc:
        coloured = doc[0].get_drawings()
        # White paper and (in this version) a page whose PDF background is not drawn
        # need no rectangle: a PDF page is white already.
        assert doc[1].get_drawings() == []
        assert doc[2].get_drawings() == []
        page_rect = doc[0].rect

    assert len(coloured) == 1
    (d,) = coloured
    assert d["type"] == "f"
    assert d["fill"] == pytest.approx((1, 0xF0 / 255, 0xC0 / 255), abs=0.002)
    assert tuple(d["rect"]) == pytest.approx(tuple(page_rect), abs=0.01)


def test_background_drawn_under_ink(tmp_path):
    stroke = Stroke([(100, 100, 1), (200, 200, 1)], BLACK, 3)
    n = note(page(background=argb(0xFF202020), strokes=[stroke]))

    with render_open(tmp_path, n) as doc:
        background, ink = doc[0].get_drawings()

    assert background["type"] == "f" and ink["type"] == "s"
    assert background["seqno"] < ink["seqno"]


def test_line_drawn(tmp_path):
    line = Line((100, 100), (1000, 100), argb(0xFF0000FF), 5)
    n = note(page(lines=[line]))

    with render_open(tmp_path, n) as doc:
        (d,) = doc[0].get_drawings()

    assert d["type"] == "s"
    assert d["color"] == pytest.approx((0, 0, 1))
    assert d["width"] == pytest.approx(5 * 0.48, abs=0.01)
    ((kind, p1, p2),) = d["items"]
    assert kind == "l"
    assert (p1.x, p1.y, p2.x, p2.y) == pytest.approx((48, 48, 480, 48), abs=0.01)


def test_image_placed_in_rect(tmp_path):
    rect = (100, 200, 600, 450)
    n = note(page(images=[Image(png(alpha=True), rect, 0.0)]))

    with render_open(tmp_path, n) as doc:
        images = doc[0].get_images()
        (info,) = doc[0].get_image_info()

    assert len(images) == 1
    assert tuple(info["bbox"]) == pytest.approx(tuple(v * PX_TO_PT for v in rect), abs=0.1)
    assert n.warnings == []


def test_draw_order_background_images_ink_text(tmp_path):
    n = note(page(
        background=argb(0xFFEEEEEE),
        texts=[TextBox("Mark Previous", (100, 100, 900, 200), 30, BLACK)],
        strokes=[Stroke([(100, 100, 1), (800, 800, 1)], BLACK, 3)],
        images=[Image(png(), (50, 50, 1000, 1000), 0.0)],
    ))

    with render_open(tmp_path, n) as doc:
        kinds = [kind for kind, _ in doc[0].get_bboxlog()]

    def first(kind):
        return next(i for i, k in enumerate(kinds) if k == kind)

    assert first("fill-path") < first("fill-image") < first("stroke-path") < first("fill-text")


def test_corrupt_image_skipped_with_warning(tmp_path):
    good = Image(png(), (100, 100, 300, 200), 0.0)
    bad = Image(b"\x89PNG\r\n\x1a\nnot really", (400, 400, 600, 600), 0.0)
    stroke = Stroke([(100, 100, 1), (200, 200, 1)], BLACK, 3)
    n = note(page(images=[bad, good], strokes=[stroke]))

    with render_open(tmp_path, n) as doc:
        assert len(doc[0].get_images()) == 1
        assert len(doc[0].get_drawings()) == 1

    assert len(n.warnings) == 1
    assert "image" in n.warnings[0] and "page 1" in n.warnings[0]


def test_rotated_image_drawn_unrotated_with_warning(tmp_path):
    rect = (100, 200, 600, 450)
    n = note(page(images=[Image(png(), rect, 30.0)]))

    with render_open(tmp_path, n) as doc:
        (info,) = doc[0].get_image_info()

    assert tuple(info["bbox"]) == pytest.approx(tuple(v * PX_TO_PT for v in rect), abs=0.1)
    assert len(n.warnings) == 1 and "rotat" in n.warnings[0]


def test_text_box_text_extractable(tmp_path):
    rect = (100, 200, 900, 400)
    box = TextBox("Mark Previous\nsecond line", rect, 30, argb(0xFF3B3B3B))
    n = note(page(texts=[box]))

    with render_open(tmp_path, n) as doc:
        text = doc[0].get_text()
        blocks = doc[0].get_text("dict")["blocks"]

    assert "Mark Previous" in text
    assert "second line" in text
    span = blocks[0]["lines"][0]["spans"][0]
    assert span["size"] == pytest.approx(30 * PX_TO_PT, abs=0.5)
    assert pymupdf.sRGB_to_pdf(span["color"]) == pytest.approx((0x3B / 255,) * 3, abs=0.01)
    box_pt = pymupdf.Rect(*(v * PX_TO_PT for v in rect)) + (-2, -2, 2, 2)  # line boxes reach a little past the text
    assert all(pymupdf.Rect(b["bbox"]) in box_pt for b in blocks)
    assert n.warnings == []


def test_text_too_big_for_its_box_is_shrunk(tmp_path):
    rect = (100, 200, 400, 260)
    n = note(page(texts=[TextBox("A rather long line of text that cannot fit", rect, 40, BLACK)]))

    with render_open(tmp_path, n) as doc:
        text = doc[0].get_text()
        blocks = doc[0].get_text("dict")["blocks"]

    assert "rather long" in text
    box_pt = pymupdf.Rect(*(v * PX_TO_PT for v in rect)) + (-2, -2, 2, 2)  # line boxes reach a little past the text
    assert all(pymupdf.Rect(b["bbox"]) in box_pt for b in blocks)


def test_non_latin_text_does_not_crash(tmp_path):
    n = note(page(texts=[TextBox("中文 Ωμέγα ∑ → é", (100, 200, 900, 400), 30, BLACK)]))

    with render_open(tmp_path, n) as doc:
        text = doc[0].get_text()

    assert "中文" in text and "Ωμέγα" in text and "∑" in text


def test_ink_outside_page_is_clipped_not_scaled(tmp_path):
    # Ink overshooting the page edge (seen up to x=1408 on a 1240 page) keeps its scale.
    stroke = Stroke([(1000, 100, 1), (1408, 100, 1)], BLACK, 3)
    n = note(page(strokes=[stroke]))

    with render_open(tmp_path, n) as doc:
        (d,) = doc[0].get_drawings()
        size = (doc[0].rect.width, doc[0].rect.height)

    assert size == pytest.approx((595.2, 841.92), abs=0.1)
    assert d["items"][0][2].x == pytest.approx(1408 * 0.48, abs=0.01)


def test_no_tmp_file_left(tmp_path):
    out = tmp_path / "Week 1.pdf"
    render_pdf(note(page()), out)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["Week 1.pdf"]


def test_failed_write_leaves_old_pdf_and_no_tmp(tmp_path, monkeypatch):
    out = tmp_path / "Week 1.pdf"
    out.write_bytes(b"previous version")

    def broken_save(self, filename, *args, **kwargs):
        with open(filename, "wb") as f:
            f.write(b"half a PDF")
        raise OSError("disk full")

    monkeypatch.setattr(pymupdf.Document, "save", broken_save)
    with pytest.raises(OSError, match="disk full"):
        render_pdf(note(page()), out)

    assert out.read_bytes() == b"previous version"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Week 1.pdf"]


def test_existing_pdf_replaced(tmp_path):
    out = tmp_path / "Week 1.pdf"
    out.write_bytes(b"previous version")

    render_pdf(note(page(), page()), out)

    with pymupdf.open(out) as doc:
        assert doc.page_count == 2


def test_note_without_pages_raises(tmp_path):
    out = tmp_path / "Week 1.pdf"

    with pytest.raises(ValueError, match="no pages"):
        render_pdf(note(), out)

    assert list(tmp_path.iterdir()) == []


def test_same_note_gives_identical_bytes(tmp_path):
    def build():
        return note(page(
            background=argb(0xFFFFF8E0),
            strokes=[Stroke([(100, 100, 1), (200, 250, 1)], argb(0x80FF0000), 3),
                     Stroke([(500, 500, 1)], BLACK, 6)],
            lines=[Line((10, 10), (500, 10), BLACK, 2)],
            images=[Image(png(alpha=True), (100, 600, 400, 800), 0.0)],
            texts=[TextBox("Mark Previous", (100, 900, 900, 1000), 30, BLACK)],
        ))

    render_pdf(build(), tmp_path / "a.pdf")
    render_pdf(build(), tmp_path / "b.pdf")

    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_unrenderable_stroke_skipped_with_warning(tmp_path):
    bad = Stroke([(float("nan"), 1, 1), (2, 2, 1)], BLACK, 3)
    good = Stroke([(1, 1, 1), (9, 9, 1)], BLACK, 3)
    n = note(page(strokes=[bad, good]))

    with render_open(tmp_path, n) as doc:
        assert len(doc[0].get_drawings()) == 1

    assert len(n.warnings) == 1 and "stroke" in n.warnings[0]


@pytest.mark.realdata
def test_real_notes_render_every_element(real_sync_dir, real_facts, tmp_path):
    """Three real notes (one with binary strokes, one with JSON strokes, one with images):
    one PDF page per page, one path per stroke and line, every image placed."""
    backup = scan(real_sync_dir)
    titles = set(real_facts["render_titles"])
    entries = [e for e in backup.notes if e.title.strip() in titles and not e.in_trash]
    assert {e.title.strip() for e in entries} == titles

    for entry in entries:
        n = load_note(entry.zip_path)
        warnings_before = list(n.warnings)
        out = tmp_path / f"{entry.id}.pdf"
        render_pdf(n, out)
        assert n.warnings == warnings_before, entry.title
        with pymupdf.open(out) as doc:
            assert doc.page_count == len(n.pages)
            for pdf_page, p in zip(doc, n.pages):
                background = int(p.background not in (None, WHITE))
                assert len(pdf_page.get_drawings()) == len(p.strokes) + len(p.lines) + background
                assert len(pdf_page.get_image_info()) == len(p.images)


@pytest.mark.parametrize("layout", ["indirect resources", "direct resources", "indirect extgstate"])
def test_opacity_resource_added_whatever_the_resources_layout(layout):
    # New pages have an indirect /Resources; pages copied from other PDFs (Task 7) may
    # have a direct one, or an indirect /ExtGState inside it.
    doc = pymupdf.open()
    p = doc.new_page()
    if layout != "indirect resources":
        doc.xref_set_key(p.xref, "Resources", "<</ExtGState<</Old<</CA 1>>>>>>")
    if layout == "indirect extgstate":
        xref = doc.get_new_xref()
        doc.update_object(xref, "<</Old<</CA 1>>>>")
        doc.xref_set_key(p.xref, "Resources/ExtGState", f"{xref} 0 R")

    render._set_resource(doc, p, "ExtGState", "notesA128", "<</CA 0.5/ca 0.5>>")
    render._append_content(doc, p, b"q /notesA128 gs 1 0 0 RG 10 w 0 0 m 100 100 l S Q")

    (d,) = p.get_drawings()
    assert d["stroke_opacity"] == pytest.approx(0.5)
    objects = "".join(doc.xref_object(x) for x in range(1, doc.xref_length()))
    assert "/notesA128" in objects
    if layout != "indirect resources":
        assert "/Old" in objects  # existing entries are kept


@pytest.mark.parametrize("size", [(float("nan"), 1754), (float("inf"), 1754), (0, 1754), (-5, 10)])
def test_invalid_page_size_falls_back_to_a4_with_warning(tmp_path, size):
    n = note(Page(size[0], size[1], WHITE, None))

    with render_open(tmp_path, n) as doc:
        assert tuple(doc[0].rect) == pytest.approx((0, 0, 595.2, 841.92), abs=0.1)
    assert any("invalid page size" in w for w in n.warnings)


def test_oversized_page_is_limited_with_warning(tmp_path):
    n = note(Page(100_000, 1754, WHITE, None))

    with render_open(tmp_path, n) as doc:
        assert doc[0].rect.width == pytest.approx(14400)
    assert any("larger than a PDF page can be" in w for w in n.warnings)


def test_absurdly_distant_stroke_skipped_with_warning(tmp_path):
    far = Stroke([(3e38, 10.0, 1.0), (20.0, 20.0, 1.0)], BLACK, 2.6)
    near = Stroke([(10.0, 10.0, 1.0), (20.0, 20.0, 1.0)], BLACK, 2.6)
    n = note(page(strokes=[far, near]))

    with render_open(tmp_path, n) as doc:
        assert len(doc[0].get_drawings()) == 1
    assert any("1 stroke(s) or line(s) with unusable" in w for w in n.warnings)


def test_text_control_characters_do_not_truncate(tmp_path):
    n = note(page(texts=[TextBox("before\x00after\x07", (100, 200, 900, 400), 30, BLACK)]))

    with render_open(tmp_path, n) as doc:
        assert "beforeafter" in doc[0].get_text()


def test_text_shrunk_to_unreadable_warns(tmp_path):
    n = note(page(texts=[TextBox("far too much text for this box " * 40, (100, 200, 160, 215), 30, BLACK)]))

    with render_open(tmp_path, n):
        pass
    assert any("shrunk" in w for w in n.warnings)


def test_temp_name_keeps_dots_in_names_without_pdf_suffix(tmp_path, monkeypatch):
    # "W1.5 notes" and "W1.6 notes" must not both use "W1.pdf.tmp". The temp name also
    # carries the process id, so two runs at once never share one.
    import re

    seen = []
    real_replace = render.os.replace
    monkeypatch.setattr(render.os, "replace", lambda a, b: (seen.append(a.name), real_replace(a, b)))
    for name in ("W1.5 notes", "W1.6 notes", "plain.pdf"):
        render_pdf(note(page()), tmp_path / name)

    assert [re.sub(r"\.\d+\.tmp$", ".N.tmp", name) for name in seen] == [
        "W1.5 notes.pdf.N.tmp", "W1.6 notes.pdf.N.tmp", "plain.pdf.N.tmp"]
    assert len(set(seen)) == 3


def test_output_accepted_by_poppler_without_complaint(tmp_path):
    # The ink is a hand-written content stream, so check it with a second PDF
    # implementation, not only with MuPDF.
    import shutil
    import subprocess

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        pytest.skip("poppler (pdftoppm) not installed")
    strokes = [
        Stroke([(10.0, 10.0, 1.0), (200.5, 300.25, 1.0), (400.0, 100.0, 1.0)], BLACK, 2.6),
        Stroke([(50.0, 50.0, 1.0)], argb(0x80FF0000), 6.0),
        Stroke([(60.0, 500.0, 1.0), (900.0, 520.0, 1.0)], argb(0x4000FF00), 20.0),
    ]
    n = note(page(strokes=strokes, lines=[Line((0.0, 0.0), (1240.0, 1754.0), BLACK, 2.1)],
                  texts=[TextBox("Typed text", (100, 800, 900, 900), 30, BLACK)],
                  background=argb(0xFFFCF9F4)))
    out = tmp_path / "check.pdf"
    render_pdf(n, out)

    result = subprocess.run([pdftoppm, "-r", "30", "-png", str(out), str(tmp_path / "img")],
                            capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stderr.strip() == ""
    assert (tmp_path / "img-1.png").stat().st_size > 0


# -- Task 7: ink over imported PDF pages ------------------------------------------------

GREEN = argb(0xFF00FF00)
_HELVETICA = "<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>"


def raw_page(doc: pymupdf.Document, mediabox, content: str, cropbox=None, rotate=0,
             resources: str = _HELVETICA) -> None:
    """Append a page whose content stream is written in PDF user space, so the test
    controls MediaBox, CropBox and /Rotate independently of PyMuPDF's helpers."""
    p = doc.new_page()
    doc.xref_set_key(p.xref, "MediaBox", "[{} {} {} {}]".format(*mediabox))
    if cropbox:
        doc.xref_set_key(p.xref, "CropBox", "[{} {} {} {}]".format(*cropbox))
    if rotate:
        doc.xref_set_key(p.xref, "Rotate", str(rotate))
    doc.xref_set_key(p.xref, "Resources", resources)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode())
    doc.xref_set_key(p.xref, "Contents", f"{xref} 0 R")


def slides(*texts: str, size=(720, 540)) -> bytes:
    """A plain source PDF with one page per text, the text drawn near the top left."""
    doc = pymupdf.open()
    for text in texts:
        p = doc.new_page(width=size[0], height=size[1])
        p.insert_text((72, 100), text, fontsize=24)
    return doc.tobytes()


def colour_boxes(pdf_page: pymupdf.Page) -> dict[str, tuple[int, int, int, int] | None]:
    """Bounding boxes (in points, at 72 dpi) of pure red, green and blue pixels as the
    page is displayed, i.e. after /Rotate, CropBox and MediaBox are applied."""
    pix = pdf_page.get_pixmap(dpi=72, alpha=False)
    s, n, w = pix.samples, pix.n, pix.width
    found: dict[str, list[int]] = {}
    for i in range(0, len(s), n):
        r, g, b = s[i], s[i + 1], s[i + 2]
        name = ("red" if r > 200 and g < 60 and b < 60 else "green" if g > 200 and r < 60 and b < 60
                else "blue" if b > 200 and r < 60 and g < 60 else None)
        if name:
            x, y = (i // n) % w, (i // n) // w
            box = found.setdefault(name, [x, y, x + 1, y + 1])
            box[0], box[1] = min(box[0], x), min(box[1], y)
            box[2], box[3] = max(box[2], x + 1), max(box[3], y + 1)
    return {k: tuple(found[k]) if k in found else None for k in ("red", "green", "blue")}


def centre(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def blue_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), False)
    pix.set_rect(pix.irect, (0, 0, 255))
    return pix.tobytes("png")


# Each geometry shows a 720 x 540 pt landscape page; the red square and the text are
# written in user space, so where they appear depends on MediaBox, CropBox and /Rotate.
_MARK = "1 0 0 rg {x} {y} 20 20 re f 0 g BT /F1 20 Tf {tx} {ty} Td (SLIDE ONE) Tj ET"
GEOMETRIES = {
    "plain": dict(mediabox=(0, 0, 720, 540), content=_MARK.format(x=180, y=135, tx=300, ty=300)),
    "origin offset": dict(mediabox=(100, 200, 820, 740),
                          content=_MARK.format(x=280, y=335, tx=400, ty=500)),
    "inset cropbox": dict(mediabox=(0, 0, 820, 640), cropbox=(50, 60, 770, 600),
                          content=_MARK.format(x=180, y=135, tx=300, ty=300)),
    "rotate 90": dict(mediabox=(0, 0, 540, 720), rotate=90,
                      content=_MARK.format(x=180, y=135, tx=100, ty=300)),
    "rotate 180": dict(mediabox=(0, 0, 720, 540), rotate=180,
                       content=_MARK.format(x=180, y=135, tx=300, ty=300)),
    "rotate 270": dict(mediabox=(0, 0, 540, 720), rotate=270,
                       content=_MARK.format(x=180, y=135, tx=100, ty=300)),
    "rotate 90, offset and cropbox": dict(mediabox=(-40, 30, 600, 830), cropbox=(10, 60, 550, 780),
                                          rotate=90, content=_MARK.format(x=200, y=300, tx=60, ty=500)),
}


def geometry_pdf(name: str) -> bytes:
    doc = pymupdf.open()
    raw_page(doc, **GEOMETRIES[name])
    return doc.tobytes()


def test_source_text_preserved(tmp_path):
    n = note(page(width=1315, height=986, background=None, pdf_page=0), pdf=slides("SLIDE ONE"))

    with render_open(tmp_path, n) as doc:
        assert "SLIDE ONE" in doc[0].get_text()
        assert doc[0].get_text("words")[0][4] == "SLIDE"  # real text, not a picture of it
        assert doc[0].get_images() == []
    assert n.warnings == []


def test_ink_scaled_to_source_page(tmp_path):
    # 720 / 1315 = 0.5475 pt per pixel, not PX_TO_PT (0.48).
    stroke = Stroke([(1315 / 2 - 50, 986 / 2, 1), (1315 / 2 + 50, 986 / 2, 1)], BLACK, 4)
    n = note(page(width=1315, height=986, background=None, pdf_page=0, strokes=[stroke]),
             pdf=slides("SLIDE ONE"))

    with render_open(tmp_path, n) as doc:
        size = (doc[0].rect.width, doc[0].rect.height)
        (d,) = doc[0].get_drawings()

    assert size == pytest.approx((720, 540), abs=0.01)
    assert centre(d["rect"]) == pytest.approx((360, 270), abs=1)
    assert d["rect"].width == pytest.approx(100 * 720 / 1315, abs=0.5)  # path extent, not line width
    assert d["width"] == pytest.approx(4 * 720 / 1315, abs=0.01)


def test_page_order_follows_note_not_pdf(tmp_path):
    n = note(page(width=1315, height=986, background=None, pdf_page=2),
             page(width=1315, height=986, background=None, pdf_page=0),
             pdf=slides("SLIDE ONE", "SLIDE TWO", "SLIDE THREE"))

    with render_open(tmp_path, n) as doc:
        texts = [p.get_text() for p in doc]

    assert len(texts) == 2
    assert "SLIDE THREE" in texts[0] and "SLIDE ONE" not in texts[0]
    assert "SLIDE ONE" in texts[1] and "SLIDE TWO" not in texts[1]


def test_mixed_blank_and_pdf_pages(tmp_path):
    pdf = slides("SLIDE ONE", "SLIDE TWO")
    n = note(page(width=1315, height=986, background=None, pdf_page=0),
             page(background=argb(0xFFFFF0C0)),  # inserted coloured page, A4
             page(),  # inserted white page
             page(width=1315, height=986, background=None, pdf_page=1),
             page(width=1315, height=986, background=None, pdf_page=0),  # first slide again
             pdf=pdf)

    with render_open(tmp_path, n) as doc:
        texts = [p.get_text() for p in doc]
        sizes = [(p.rect.width, p.rect.height) for p in doc]
        coloured = doc[1].get_drawings()

    assert ["SLIDE ONE" in t for t in texts] == [True, False, False, False, True]
    assert ["SLIDE TWO" in t for t in texts] == [False, False, False, True, False]
    assert sizes == [pytest.approx(s, abs=0.1) for s in
                     [(720, 540), (595.2, 841.92), (595.2, 841.92), (720, 540), (720, 540)]]
    assert len(coloured) == 1 and coloured[0]["fill"] == pytest.approx((1, 0xF0 / 255, 0xC0 / 255), abs=0.002)
    assert n.warnings == []


def test_pdf_page_out_of_range_falls_back_blank(tmp_path):
    stroke = Stroke([(100, 200, 1), (300, 200, 1)], BLACK, 3)
    n = note(page(width=1315, height=986, background=None, pdf_page=0),
             page(width=1315, height=986, background=None, pdf_page=7, strokes=[stroke]),
             pdf=slides("SLIDE ONE", "SLIDE TWO"))

    with render_open(tmp_path, n) as doc:
        assert doc.page_count == 2
        assert "SLIDE ONE" in doc[0].get_text()
        assert doc[1].get_text() == ""
        assert (doc[1].rect.width, doc[1].rect.height) == pytest.approx((1315 * PX_TO_PT, 986 * PX_TO_PT))
        (d,) = doc[1].get_drawings()  # the ink is still there, at the blank page's scale
        assert d["items"][0][1].x == pytest.approx(100 * PX_TO_PT, abs=0.01)

    assert len(n.warnings) == 1
    assert "past the end of the imported PDF" in n.warnings[0] and "blank" in n.warnings[0]


def test_rotated_source_page(tmp_path):
    # A portrait MediaBox shown landscape by /Rotate 90; the red square marks the place
    # the ink was written over.
    pdf = geometry_pdf("rotate 90")
    with pymupdf.open(stream=pdf) as src:
        marker = colour_boxes(src[0])["red"]
    mx, my = centre(marker)
    scale = 720 / 1315
    stroke = Stroke([(mx / scale - 5, my / scale, 1), (mx / scale + 5, my / scale, 1)], GREEN, 8)
    n = note(page(width=1315, height=986, background=None, pdf_page=0, strokes=[stroke]), pdf=pdf)

    with render_open(tmp_path, n) as doc:
        out = doc[0]
        assert out.rotation == 0
        assert (out.rect.width, out.rect.height) == pytest.approx((720, 540), abs=0.01)
        boxes = colour_boxes(out)
        line = out.get_text("dict")["blocks"][0]["lines"][0]

    assert "SLIDE ONE" in line["spans"][0]["text"]
    # Written left to right in user space; /Rotate 90 (clockwise) shows it running down.
    assert line["dir"] == pytest.approx((0, 1), abs=1e-6)
    assert boxes["red"] == pytest.approx(marker, abs=1)  # slide content where the source shows it
    assert centre(boxes["green"]) == pytest.approx((mx, my), abs=1.5)  # ink on the marker


@pytest.mark.parametrize("geometry", list(GEOMETRIES))
def test_ink_images_and_text_line_up_with_source_content(tmp_path, geometry):
    """Whatever the source page's MediaBox origin, CropBox and /Rotate, the output page is
    the page as a viewer shows it, and ink, images and text boxes land where they were
    written relative to the slide."""
    pdf = geometry_pdf(geometry)
    with pymupdf.open(stream=pdf) as src:
        marker = colour_boxes(src[0])["red"]
        source_words = [w[4] for w in src[0].get_text("words")]
    mx, my = centre(marker)
    scale = 720 / 1315
    stroke = Stroke([(mx / scale - 5, my / scale, 1), (mx / scale + 5, my / scale, 1)], GREEN, 8)
    image_pt = (600, 400, 660, 440)
    text_pt = (40, 470, 400, 520)
    n = note(page(width=1315, height=986, background=None, pdf_page=0, strokes=[stroke],
                  images=[Image(blue_png(), tuple(v / scale for v in image_pt), 0.0)],
                  texts=[TextBox("TYPED NOTE", tuple(v / scale for v in text_pt), 30, BLACK)]),
             pdf=pdf)

    with render_open(tmp_path, n) as doc:
        out = doc[0]
        assert out.rotation == 0
        assert tuple(out.rect) == pytest.approx((0, 0, 720, 540), abs=0.01)
        boxes = colour_boxes(out)
        words = {w[4]: pymupdf.Rect(w[:4]) for w in out.get_text("words")}

    assert boxes["red"] == pytest.approx(marker, abs=1)
    assert centre(boxes["green"]) == pytest.approx((mx, my), abs=1.5)
    assert boxes["blue"] == pytest.approx(image_pt, abs=1)
    assert source_words == ["SLIDE", "ONE"]
    assert {"SLIDE", "ONE", "TYPED", "NOTE"} <= set(words)
    assert words["TYPED"] in pymupdf.Rect(text_pt) + (-2, -2, 2, 2)
    assert n.warnings == []


def test_pdf_backed_page_size_and_scale_from_source(tmp_path):
    # Real shapes: Letter at 2.026 px/pt and A4 at 150 dpi; the page is the source's size.
    stroke = Stroke([(1240, 1604, 1)], BLACK, 4)  # bottom-right corner of the Letter note page
    n = note(page(width=1240, height=1604, background=None, pdf_page=0, strokes=[stroke]),
             page(width=1240, height=1753, background=None, pdf_page=1),
             pdf=_two_sizes())

    with render_open(tmp_path, n) as doc:
        sizes = [(p.rect.width, p.rect.height) for p in doc]
        (dot,) = doc[0].get_drawings()

    assert sizes == [pytest.approx((612, 792), abs=0.01), pytest.approx((595.3, 841.9), abs=0.01)]
    # One scale, from the width: 612 / 1240 = 0.4935 pt/px; 1604 px is 791.65 pt (the
    # real 0.04% difference in shape, 0.35 pt at the bottom edge).
    assert centre(dot["rect"]) == pytest.approx((612, 1604 * 612 / 1240), abs=0.05)


def _two_sizes() -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "LETTER")
    doc.new_page(width=595.3, height=841.9).insert_text((72, 100), "A FOUR")
    return doc.tobytes()


def test_aspect_mismatch_fits_width_with_warning(tmp_path):
    # Cannot happen in today's data. The slide keeps its own size at the top, the ink is
    # scaled by width, and the page grows so that nothing on the note page is cut off.
    stroke = Stroke([(100, 1700, 1), (200, 1700, 1)], BLACK, 3)  # near the bottom of the note page
    n = note(page(width=1240, height=1754, background=None, pdf_page=0, strokes=[stroke]),
             pdf=slides("SLIDE ONE", size=(720, 540)))

    with render_open(tmp_path, n) as doc:
        rect = doc[0].rect
        (d,) = doc[0].get_drawings()
        assert "SLIDE ONE" in doc[0].get_text()
        word = doc[0].get_text("words")[0]

    scale = 720 / 1240
    assert (rect.width, rect.height) == pytest.approx((720, 1754 * scale), abs=0.01)
    assert d["items"][0][1].y == pytest.approx(1700 * scale, abs=0.01)
    assert word[1] < 100  # the slide sits at the top, unscaled
    assert len(n.warnings) == 1
    assert "page 1" in n.warnings[0] and "shape" in n.warnings[0]


@pytest.mark.parametrize("pdf, reason", [
    (None, "does not have"),
    (b"%PDF-1.7\nthis is not really a PDF", "cannot be read"),
    (b"", "cannot be read"),
])
def test_missing_or_unreadable_pdf_falls_back_blank(tmp_path, pdf, reason):
    stroke = Stroke([(100, 200, 1), (300, 200, 1)], BLACK, 3)
    n = note(page(width=1315, height=986, background=None, pdf_page=0, strokes=[stroke]),
             page(width=1315, height=986, background=None, pdf_page=1), pdf=pdf)

    with render_open(tmp_path, n) as doc:
        assert doc.page_count == 2
        assert [p.get_text() for p in doc] == ["", ""]
        assert len(doc[0].get_drawings()) == 1
        assert doc[0].rect.width == pytest.approx(1315 * PX_TO_PT)

    assert len(n.warnings) == 1
    assert reason in n.warnings[0] and "2 page(s)" in n.warnings[0] and "blank" in n.warnings[0]


def test_encrypted_pdf_falls_back_blank(tmp_path):
    with pymupdf.open(stream=slides("SECRET")) as src:
        locked = src.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        open_with_owner_pw_only = src.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o")

    n = note(page(width=1315, height=986, background=None, pdf_page=0), pdf=locked)
    with render_open(tmp_path, n) as doc:
        assert doc[0].get_text() == ""
    assert len(n.warnings) == 1 and "password" in n.warnings[0] and "blank" in n.warnings[0]

    # Encrypted only against editing: opens without a password, so it is used.
    n = note(page(width=1315, height=986, background=None, pdf_page=0), pdf=open_with_owner_pw_only)
    with render_open(tmp_path, n) as doc:
        assert "SECRET" in doc[0].get_text()
    assert n.warnings == []


def test_source_links_kept_and_retargeted(tmp_path):
    src = pymupdf.open()
    for text in ("SLIDE ONE", "SLIDE TWO", "SLIDE THREE"):
        src.new_page(width=720, height=540).insert_text((72, 100), text)
    src[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 20, 110, 40),
                        "uri": "https://example.org/docs"})
    src[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 50, 110, 70), "page": 2})
    src[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 80, 110, 100), "page": 1})
    pdf = src.tobytes()
    # Output: slide 1, slide 3. The link to slide 3 now points at output page 2; slide 2
    # is not in the note, so the link to it is dropped.
    n = note(page(width=1315, height=986, background=None, pdf_page=0),
             page(width=1315, height=986, background=None, pdf_page=2), pdf=pdf)

    with render_open(tmp_path, n) as doc:
        links = doc[0].get_links()

    uri = [ln for ln in links if ln["kind"] == pymupdf.LINK_URI]
    goto = [ln for ln in links if ln["kind"] == pymupdf.LINK_GOTO]
    assert len(links) == 2
    assert uri[0]["uri"] == "https://example.org/docs"
    assert tuple(uri[0]["from"]) == pytest.approx((10, 20, 110, 40), abs=0.01)
    assert goto[0]["page"] == 1
    assert tuple(goto[0]["from"]) == pytest.approx((10, 50, 110, 70), abs=0.01)


def test_link_on_rotated_source_page_keeps_its_place(tmp_path):
    src = pymupdf.open()
    raw_page(src, mediabox=(0, 0, 540, 720), rotate=90, content=_MARK.format(x=180, y=135, tx=100, ty=300))
    src[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(400, 300, 500, 320),
                        "uri": "https://example.org"})
    pdf = src.tobytes()
    with pymupdf.open(stream=pdf) as check:
        (shown,) = check[0].get_links()

    n = note(page(width=1315, height=986, background=None, pdf_page=0), pdf=pdf)
    with render_open(tmp_path, n) as doc:
        (link,) = doc[0].get_links()

    assert tuple(link["from"]) == pytest.approx(tuple(shown["from"]), abs=0.01)


def test_shared_source_resources_not_duplicated(tmp_path):
    # One incompressible image used by every source page; source pages used repeatedly.
    import random

    rng = random.Random(7)
    noise = pymupdf.Pixmap(pymupdf.csRGB, 300, 300, bytes(rng.randrange(256) for _ in range(300 * 300 * 3)), False)
    src = pymupdf.open()
    image_xref = 0
    for i in range(4):
        p = src.new_page(width=720, height=540)
        image_xref = p.insert_image(pymupdf.Rect(300, 100, 600, 400), pixmap=noise if not image_xref else None,
                                    xref=image_xref)
        p.insert_text((72, 100), f"SLIDE {i + 1}")
    pdf = src.tobytes(garbage=4, deflate=True)
    pages = [page(width=1315, height=986, background=None, pdf_page=i % 4,
                  strokes=[Stroke([(10, 10, 1), (500, 500, 1)], BLACK, 3)]) for i in range(12)]
    out = tmp_path / "out.pdf"
    render_pdf(note(*pages, pdf=pdf), out)

    with pymupdf.open(out) as doc:
        assert doc.page_count == 12
        assert len({doc.get_page_images(i)[0][0] for i in range(12)}) == 1
    assert out.stat().st_size < len(pdf) * 1.1 + 20_000


def test_pdf_backed_note_gives_identical_bytes(tmp_path):
    src = pymupdf.open()
    raw_page(src, **GEOMETRIES["rotate 90"])
    src.new_page(width=720, height=540).insert_text((72, 100), "SLIDE TWO")
    src[1].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 20, 110, 40), "uri": "https://x.org"})
    src[1].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 50, 110, 70), "page": 0})
    pdf = src.tobytes()

    def build():
        return note(
            page(width=1315, height=986, background=None, pdf_page=1,
                 strokes=[Stroke([(100, 100, 1), (200, 250, 1)], argb(0x80FF0000), 3)],
                 texts=[TextBox("Mark Previous", (100, 600, 900, 700), 30, BLACK)]),
            page(background=argb(0xFFFFF8E0)),
            page(width=1315, height=986, background=None, pdf_page=0,
                 images=[Image(png(), (100, 600, 400, 800), 0.0)]),
            pdf=pdf)

    render_pdf(build(), tmp_path / "a.pdf")
    render_pdf(build(), tmp_path / "b.pdf")

    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_imported_fonts_not_subset(tmp_path):
    # Fonts of the imported PDF are kept byte for byte (subsetting them is not needed and
    # makes MuPDF complain about some real PDFs); text-box fonts are still subset.
    src = pymupdf.open()
    src.new_page(width=720, height=540).insert_htmlbox(
        pymupdf.Rect(50, 50, 600, 200), "SLIDE ONE", css="* {font-family: sans-serif; font-size: 30pt}")
    pdf = src.tobytes(garbage=4, deflate=True)
    with pymupdf.open(stream=pdf) as s:
        (font,) = s.get_page_fonts(0)
        source_font = s.extract_font(font[0])[3]
    assert len(source_font) > 10_000

    n = note(page(width=1315, height=986, background=None, pdf_page=0,
                  texts=[TextBox("Typed", (100, 600, 900, 700), 30, BLACK)]), pdf=pdf)
    with render_open(tmp_path, n) as doc:
        fonts = [x for x in range(1, doc.xref_length()) if doc.xref_get_key(x, "Type")[1] == "/Font"]
        buffers = [doc.extract_font(x)[3] for x in fonts]
        text = doc[0].get_text()

        names = [doc.xref_get_key(x, "BaseFont")[1] for x in fonts]

    assert source_font in buffers
    assert any("+" in name for name in names)  # the text box's font is a subset
    assert "SLIDE ONE" in text and "Typed" in text


def test_mupdf_prints_nothing_while_rendering(tmp_path, capfd, monkeypatch):
    # A damaged imported PDF (cut short): MuPDF repairs what it can; nothing reaches the
    # terminal, what MuPDF said becomes a note warning, and the earlier error-display
    # setting is restored.
    import io

    # PyMuPDF prints MuPDF errors to the sys.stdout it saw at import, which pytest's
    # capture fixtures do not see; send them somewhere the test can read.
    printed = io.StringIO()
    monkeypatch.setattr(pymupdf, "_g_out_message", printed)
    whole = slides("SLIDE ONE", "SLIDE TWO")
    damaged = whole[: len(whole) // 2]
    before = pymupdf.TOOLS.mupdf_display_errors()
    # Precondition: left to itself, MuPDF prints an error for this PDF.
    pymupdf.TOOLS.mupdf_display_errors(True)
    try:
        with pymupdf.open(stream=damaged, filetype="pdf") as d:
            for p in d:
                p.rect
    finally:
        pymupdf.TOOLS.mupdf_display_errors(before)
    assert "MuPDF error" in printed.getvalue()
    printed.seek(0)
    printed.truncate()
    capfd.readouterr()

    n = note(page(width=1315, height=986, background=None, pdf_page=0),
             page(width=1315, height=986, background=None, pdf_page=1),
             page(images=[Image(b"\x89PNG\r\n\x1a\nnot really", (100, 100, 200, 200), 0.0)]),
             pdf=damaged)

    with render_open(tmp_path, n) as doc:
        assert doc.page_count == 3

    captured = capfd.readouterr()
    assert printed.getvalue() == ""
    assert captured.out == "" and captured.err == ""
    assert pymupdf.TOOLS.mupdf_display_errors() == before
    assert any("image" in w for w in n.warnings)
    assert any("MuPDF reported problems in the imported PDF" in w for w in n.warnings)


def test_pdf_backed_output_accepted_by_poppler(tmp_path):
    import shutil
    import subprocess

    pdftoppm, pdftotext = shutil.which("pdftoppm"), shutil.which("pdftotext")
    if pdftoppm is None or pdftotext is None:
        pytest.skip("poppler (pdftoppm, pdftotext) not installed")
    stroke = Stroke([(100, 100, 1), (600, 400, 1)], argb(0x80FF0000), 4)
    n = note(page(width=1315, height=986, background=None, pdf_page=0, strokes=[stroke],
                  texts=[TextBox("Typed text", (100, 800, 900, 900), 30, BLACK)]),
             page(),
             page(width=1315, height=986, background=None, pdf_page=0),
             pdf=geometry_pdf("rotate 90, offset and cropbox"))
    out = tmp_path / "check.pdf"
    render_pdf(n, out)

    image = subprocess.run([pdftoppm, "-r", "30", "-png", str(out), str(tmp_path / "img")],
                           capture_output=True, text=True)
    text = subprocess.run([pdftotext, str(out), "-"], capture_output=True, text=True)

    assert image.returncode == 0 and image.stderr.strip() == ""
    assert text.returncode == 0 and text.stderr.strip() == ""
    pages = text.stdout.split("\f")
    assert "SLIDE ONE" in pages[0] and "Typed text" in pages[0]
    assert "SLIDE ONE" not in pages[1] and "SLIDE ONE" in pages[2]


@pytest.mark.realdata
def test_real_pdf_backed_notes(real_sync_dir, real_facts, tmp_path):
    """Real notes written on imported PDFs (A4 portrait, landscape slides at 3.438 px/pt,
    landscape A4, Letter size): every PDF page is the source page at its own size with its
    text, and every stroke is drawn on top of it."""
    backup = scan(real_sync_dir)
    titles = set(real_facts["pdf_backed_titles"])
    entries = [e for e in backup.notes if e.title.strip() in titles and not e.in_trash]
    assert {e.title.strip() for e in entries} == titles

    checked_ink_pages = 0
    for entry in entries:
        n = load_note(entry.zip_path)
        assert n.pdf is not None, entry.title
        warnings_before = list(n.warnings)
        out = tmp_path / f"{entry.id}.pdf"
        render_pdf(n, out)
        assert n.warnings == warnings_before, entry.title
        with pymupdf.open(stream=n.pdf) as src, pymupdf.open(out) as doc:
            assert doc.page_count == len(n.pages)
            for pdf_page, p in zip(doc, n.pages):
                assert pdf_page.rotation == 0
                ink = len(p.strokes) + len(p.lines)
                if p.pdf_page is None:
                    continue
                sp = src[p.pdf_page]
                assert tuple(pdf_page.rect) == pytest.approx(tuple(sp.rect), abs=0.01)
                assert pdf_page.get_text().split() == sp.get_text().split()
                assert len(pdf_page.get_drawings()) == len(sp.get_drawings()) + ink
                # The ink sits where the app put it: its bounding box is the strokes' box
                # scaled by this page's own scale (source width / page width), not 150 dpi.
                if p.strokes and not p.lines:
                    k = sp.rect.width / p.width
                    xs = [x for s in p.strokes for x, _, _ in s.points]
                    ys = [y for s in p.strokes for _, y, _ in s.points]
                    drawn = pdf_page.get_drawings()[len(sp.get_drawings()):]
                    box = pymupdf.Rect()
                    for d in drawn:
                        box |= d["rect"]
                    expected = (min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k)
                    assert tuple(box) == pytest.approx(expected, abs=2.5), (entry.title, p.pdf_page)
                    checked_ink_pages += 1
    assert checked_ink_pages >= 10


def test_invalid_note_page_size_on_pdf_page_keeps_source_page(tmp_path):
    stroke = Stroke([(100, 200, 1), (300, 200, 1)], BLACK, 3)
    n = note(Page(float("nan"), 986, None, 0, strokes=[stroke]), pdf=slides("SLIDE ONE"))

    with render_open(tmp_path, n) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == pytest.approx((720, 540))
        assert "SLIDE ONE" in doc[0].get_text()
        (d,) = doc[0].get_drawings()
        assert d["items"][0][1].x == pytest.approx(100 * PX_TO_PT, abs=0.01)

    assert len(n.warnings) == 1 and "invalid page size" in n.warnings[0]


def test_repeat_counter_left_by_earlier_mupdf_work_is_not_a_warning(tmp_path):
    # MuPDF's "... repeated N times..." counter survives a reset of its message store, so
    # work done on some other PDF just before must not put a warning on a clean note.
    broken = pymupdf.open()
    broken.new_page()
    xref = broken.get_new_xref()
    broken.update_object(xref, "<</Type/Annot/Subtype/Link/Rect[0 0 10 10]/Dest[99 0 R/Fit]>>")
    broken.xref_set_key(broken[0].xref, "Annots", f"[{xref} 0 R]")
    data = broken.tobytes()
    broken.close()
    with pymupdf.open(stream=data) as doc:
        for _ in range(4):
            doc[0].get_links()

    clean = pymupdf.open()
    clean.new_page(width=595, height=842).insert_text((72, 72), "SLIDE")
    pdf = clean.tobytes()
    clean.close()
    n = note(page(background=None, pdf_page=0), pdf=pdf)
    render_pdf(n, tmp_path / "out.pdf")

    assert n.warnings == []


def test_pdf_carries_ownership_stamp(tmp_path):
    out = tmp_path / "Week 1.pdf"
    render_pdf(note(page()), out)

    assert render.stamped_note_id(out) == "n-1"
    with pymupdf.open(out) as doc:
        assert doc.metadata["title"] == "Week 1"
        assert doc.metadata["creator"] == render.PDF_CREATOR


def test_stamp_absent_from_other_files(tmp_path):
    other = tmp_path / "other.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Week 1", "subject": "Notein note n-1"})  # no creator
    doc.save(other)
    (tmp_path / "text.pdf").write_text("not a pdf")
    (tmp_path / "folder.pdf").mkdir()

    for name in ("other.pdf", "text.pdf", "folder.pdf", "missing.pdf"):
        assert render.stamped_note_id(tmp_path / name) is None


def _ink_boxes(doc_page):
    return [tuple(round(v, 1) for v in d["rect"]) for d in doc_page.get_drawings()]


def test_ink_above_a_page_is_also_drawn_at_the_bottom_of_the_previous_page(tmp_path):
    # The app shows pages as one column with no gap; ink across a boundary is stored on
    # the lower page with negative y. Viewed one under the other, the pages must match up.
    crossing = Stroke([(100.0, -200.0, 1.0), (300.0, -100.0, 1.0)], BLACK, 2.6)
    inside = Stroke([(100.0, 500.0, 1.0), (300.0, 600.0, 1.0)], BLACK, 2.6)
    n = note(page(), page(strokes=[crossing, inside]), page())

    with render_open(tmp_path, n) as doc:
        first, second, third = (_ink_boxes(doc[i]) for i in range(3))

    k = PX_TO_PT
    assert len(first) == 1 and len(second) == 2 and third == []
    assert first[0] == pytest.approx((100 * k, (1754 - 200) * k, 300 * k, (1754 - 100) * k), abs=0.2)
    assert n.warnings == []


def test_ink_below_a_page_is_also_drawn_at_the_top_of_the_next_page(tmp_path):
    crossing = Line((100.0, 1700.0), (300.0, 1900.0), BLACK, 2.0)
    n = note(page(lines=[crossing]), page())

    with render_open(tmp_path, n) as doc:
        first, second = _ink_boxes(doc[0]), _ink_boxes(doc[1])

    k = PX_TO_PT
    assert len(first) == 1 and len(second) == 1
    assert second[0] == pytest.approx((100 * k, (1700 - 1754) * k, 300 * k, (1900 - 1754) * k), abs=0.2)


def test_ink_is_not_carried_to_a_neighbour_of_another_width_or_past_the_ends(tmp_path):
    crossing = Stroke([(100.0, -200.0, 1.0), (300.0, -100.0, 1.0)], BLACK, 2.6)
    n = note(page(strokes=[crossing]), page(width=1754, height=1240), page(strokes=[crossing]))

    with render_open(tmp_path, n) as doc:
        counts = [len(doc[i].get_drawings()) for i in range(3)]

    assert counts == [1, 0, 1]


def test_spilled_ink_uses_the_scale_of_the_imported_page_it_lands_on(tmp_path):
    src = pymupdf.open()
    src.new_page(width=620, height=877).insert_text((72, 72), "SLIDE ONE")  # 2 px per pt
    src.new_page(width=620, height=877).insert_text((72, 72), "SLIDE TWO")
    pdf = src.tobytes()
    src.close()
    crossing = Stroke([(200.0, -100.0, 1.0), (400.0, -50.0, 1.0)], BLACK, 2.0)
    n = note(page(background=None, pdf_page=0), page(background=None, pdf_page=1, strokes=[crossing]), pdf=pdf)

    with render_open(tmp_path, n) as doc:
        boxes = _ink_boxes(doc[0])

    assert len(boxes) == 1
    assert boxes[0] == pytest.approx((100, (1754 - 100) / 2, 200, (1754 - 50) / 2), abs=0.3)
