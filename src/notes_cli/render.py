"""Render a loaded `Note` to a vector PDF with PyMuPDF.

One PDF page per `Page`, `width x height x PX_TO_PT` points. Per page, in this order:
background colour, images, lines and strokes, text boxes. Content outside the page is
left in place and clipped by the page, never rescaled. Ink that crosses the boundary
between two consecutive pages is drawn on both, so the pages match up when viewed one
under the other (`_spilled_ink`).

Ink is written as a hand-built content stream rather than through PyMuPDF's `Shape`
API: each stroke is one path (`m`, then one `l` per point, `S`) with round caps and
joins, and colour, width and opacity are only emitted when they change. Coordinates are
integers in hundredths of a point (the stream starts with a `0.01 0 0 -0.01 0 H cm`,
which also flips y so page coordinates can be written top-down). On the largest real
note (IAS W1, 352,913 points) it renders in 1.1 s to 1.75 MB, against 6.8 s and 2.65 MB
for the same paths drawn with `Shape.draw_polyline` (measured 2026-11-15). PyMuPDF's
`get_drawings()` reports each stroke as one path with its colour, width and `stroke_opacity`, exactly as for paths drawn through its own API.

A stroke whose points all round to the same position (a single-point stroke, or a "dot"
made of repeated identical points) is drawn as a filled circle of the stroke's width.

Problems with one element (an image that cannot be decoded, a text box that cannot be
set, a stroke with non-finite coordinates) skip that element and are appended to
`note.warnings`, prefixed with the 1-based page number; the rest of the note renders.

Images: `rotation` is 0 in every real image; a non-zero rotation is not applied (the
image fills its rectangle unrotated) and is reported as a warning.

Output is deterministic: no timestamps or random ids are written, so rendering the same
note twice gives byte-identical files.

Every PDF carries an ownership stamp in its document information: creator `notes-cli`
(`PDF_CREATOR`) and subject `Notein note <note id>`. `stamped_note_id` reads it back.
`convert` overwrites an existing file only when it carries this stamp, so a file the
user put at a destination is never replaced, with or without the manifest.

Pages written on top of an imported PDF (`page.pdf_page` set, `note.pdf` bundled)
---------------------------------------------------------------------------------
Coordinate mapping, from all 461 such pages in the real backup (88 notes, one PDF each,
surveyed 2026-11-15). Page sizes are `Page.width x Page.height` (already oriented by the
loader); the source size is the page as a viewer shows it:

    page px       orientation  source pt      px per pt  pages  notes
    1240 x 1753   portrait     595.3 x 841.9  2.083        219     49  (A4)
    1754 x 1315   landscape    510.2 x 382.7  3.438        146      6  (slides)
    1240 x 1604   portrait     612.0 x 792.0  2.026         47     25  (Letter)
    1240 x 1984   portrait     640.0 x 1024   1.938         19      1
    1754 x 986    landscape    453.5 x 255.1  3.867         17      3
    1240 x 1754   portrait     595.0 x 842.0  2.084          6      1
    1754 x 1240   landscape    841.9 x 595.3  2.083          3      1
    1240 x 1753   portrait     595.4 x 841.8  2.083          3      1
    1240 x 1752   portrait     596.0 x 842.0  2.081          1      1

Every source page has /Rotate 0, a MediaBox at (0, 0) and CropBox = MediaBox, and on
every page the horizontal and vertical scales agree to within 0.07%. Rule: the output
page has the source page's own size in points, upright, and page pixels map to points by
`scale = source_width_pt / page.width`. `PX_TO_PT` fits only the A4 shapes; on the
other 229 pages it would put ink 3% to 86% too near to or too far from the top left. If
the shapes ever differ by more than 1% (not seen), the page is "fitted by width": the
source page sits at the top at its own size, ink is scaled by width, the output page is
made tall enough for the whole note page, and a warning is added.

How the source page is put under the ink: a new, plain output page (origin (0, 0), no
/Rotate, no CropBox) of that size, with the source page drawn onto it as a Form XObject
(`show_pdf_page`), rather than copying the page itself (`insert_pdf`). On the plain page
the hand-written ink stream, PyMuPDF's images and text boxes all share one coordinate
system, so nothing needs to know about MediaBox origins, CropBoxes or rotation; a
copied page keeps all three, so the ink would need the page's full transformation
(PyMuPDF's own `Page.transformation_matrix` ignores /Rotate and the MediaBox origin for
rotated pages). Measured on the 88 real notes, source pages only: both give output
within about 1% of the size of the bundled PDFs (shared fonts and images are grafted
once per note through PyMuPDF's graft map, and a source page used twice is one XObject),
the slide text stays real, extractable text (MuPDF and poppler), and both are
deterministic. With the ink, all 88 render in 2.3 s to 25.2 MB, against 22.6 MB of
bundled PDFs plus 2.7 MB for the same ink on blank pages.

`show_pdf_page` itself mishandles a source page with /Rotate (in PyMuPDF 2.1 / MuPDF 1.28.6, 90 and
270 put the content off the page and 180 shows it upside down), so the in-memory copy
of the source page gets /Rotate 0 and the rotation is applied through
`show_pdf_page(rotate=-rotation)`; tests cover offset MediaBoxes, inset CropBoxes and
/Rotate 90/180/270 against the source page as MuPDF displays it.

Not carried over from the imported PDF: its bookmark outline (18 real PDFs have one) and
page labels (3 have them). They could be rebuilt with `set_toc` from the same
first-output-page mapping the links use; not done yet.

The Form XObject carries the page's content only, not its annotations. The real PDFs
have no annotations other than links (none of other kinds), so links are re-created:
web links (30, e.g. Java API docs in DSA notes) where they were, and internal links
(hyperref cross-references, Beamer navigation, a table of contents; 521) pointing at the
first output page that shows their target page; internal links to pages not in the note,
and links of other kinds (none in the real data), are dropped. All 551 real links are
kept.

The source pages are added under each page's content after everything else is drawn and
after text-box fonts are subset, so the imported PDF's fonts are never subset: they are
normally subsets already, and MuPDF's subsetter reports "Reserved charstring byte"
format errors on 7 of the real PDFs. MuPDF error output is switched off while rendering
(and restored), so nothing reaches the terminal; messages MuPDF records while reading
the imported PDF become one note warning (in the real data only "Quick Start Guide"
has any: "invalid link destination").

When the PDF cannot be used (not bundled, unreadable, needs a password, or `pdf_page`
past its end) or a source page cannot be drawn, the page is drawn on blank paper at the
note page's size (`PX_TO_PT`) with a warning; the note still renders.
"""

from __future__ import annotations

import html
import math
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from notes_cli.notedb import Image, Line, Note, Page, Stroke, TextBox

PX_TO_PT = 72 / 150  # page pixels are 150 dpi
_REPEATED = re.compile(r"\.\.\. repeated \d+ times\.\.\.")

_UNITS = 100  # content-stream units per point (coordinates are 1/100 pt integers)
_KAPPA = 0.5523  # Bezier control-point distance for a quarter circle of radius 1
_GS_PREFIX = "notesA"  # ExtGState resource names: notesA<alpha 0..255>
_WHITE = (255, 255, 255)
_MAX_UNITS = 2**31 - 1  # PDF integers are 32-bit; real ink is far inside this
MAX_PAGE_PT = 14400.0  # the largest page side PDF viewers accept (200 inches)
A4_PT = (595.2, 841.92)
# Control characters other than newline and tab: they truncate or corrupt PDF text.
_CONTROL = {c: None for c in (*range(0, 9), 11, 12, *range(14, 32), 127)}
MIN_TEXT_SCALE = 0.5  # below this a shrunk-to-fit text box is reported as hard to read
SHAPE_TOLERANCE = 0.01  # relative difference of x and y scale above which a PDF page is fitted by width
PDF_CREATOR = "notes-cli"  # ownership stamp: document information "creator"
PDF_SUBJECT_PREFIX = "Notein note "  # ... and "subject" is this followed by the note id


def stamped_note_id(path: Path) -> str | None:
    """The note id in the ownership stamp of the PDF at `path`, or None when `path` is
    not a readable PDF written by `render_pdf` (missing, a folder, not a PDF, damaged,
    encrypted, or a PDF made by anything else). Only reads; never raises for the file's
    content."""
    path = Path(path)
    if not path.is_file():
        return None
    show_errors = pymupdf.TOOLS.mupdf_display_errors()
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(path, filetype="pdf") as doc:
            meta = doc.metadata or {}
    except Exception:
        return None
    finally:
        pymupdf.TOOLS.mupdf_display_errors(show_errors)
    subject = meta.get("subject") or ""
    if meta.get("creator") != PDF_CREATOR or not subject.startswith(PDF_SUBJECT_PREFIX):
        return None
    return subject[len(PDF_SUBJECT_PREFIX):] or None


def render_pdf(note: Note, out: Path) -> None:
    """Write `note` as a PDF at `out`, atomically.

    The PDF is saved next to `out` under a new temp name and renamed over `out`, so an
    interrupted or failed run leaves any earlier `out` untouched and no temp file
    behind. The parent folder must exist. Raises ValueError for a note with no pages
    (a PDF cannot have none). Element-level problems go to `note.warnings`.
    """
    if not note.pages:
        raise ValueError(f"note {note.id} has no pages")
    out = Path(out)
    tmp = _temp_path(out)
    show_errors = pymupdf.TOOLS.mupdf_display_errors()
    doc = imported = None
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)
        doc = pymupdf.open()
        imported = _ImportedPdf(note)
        has_text = False
        spills = _spilled_ink(note.pages)
        for number, page in enumerate(note.pages, 1):
            source = imported.source(page, number)
            has_text |= _render_page(doc, note, page, number, source, spills[number - 1])
            if source is not None:
                imported.place(doc.page_count - 1, number, source)
        if has_text:
            # Text boxes embed whole fallback fonts (several MB for CJK); keep only the
            # glyphs used. Done before the imported pages are added, so their fonts are
            # left as they are.
            doc.subset_fonts()
        imported.draw(doc)
        imported.report()
        doc.set_metadata({"title": note.title, "creator": PDF_CREATOR,
                          "subject": PDF_SUBJECT_PREFIX + note.id})
        try:
            doc.save(tmp, garbage=4, deflate=True, no_new_id=True)
            os.replace(tmp, out)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        try:
            if doc is not None:
                doc.close()
            if imported is not None:
                imported.close()
        finally:
            pymupdf.TOOLS.mupdf_display_errors(show_errors)


def _temp_path(out: Path) -> Path:
    """A temp name beside `out` that no file has: `<name>.pdf.<pid>.tmp`, with a counter
    added if that exists. Never an existing file (so nothing of the user's can be
    overwritten, and a leftover of a killed run is not reused), and different for two
    processes converting at once. A name without the `.pdf` suffix keeps its dots, so
    two outputs in one folder cannot share a temp name."""
    stem = out.name.removesuffix(".pdf")
    candidate = out.with_name(f"{stem}.pdf.{os.getpid()}.tmp")
    counter = 1
    while os.path.lexists(candidate):
        counter += 1
        candidate = out.with_name(f"{stem}.pdf.{os.getpid()}-{counter}.tmp")
    return candidate


def _spilled_ink(pages: list[Page]) -> list[tuple[list[Line], list[Stroke]]]:
    """For each page, the ink of its neighbours that reaches onto it, moved into this
    page's coordinates.

    The app shows a note as one continuous column of pages with no gap, and ink written
    across a page boundary is stored whole on one of the two pages, with coordinates
    beyond that page's edge (in the real backup: always on the lower page, above its top,
    on 40 pages). Each page clips its content, so such ink is also drawn on the neighbour
    it reaches onto: with the pages one under the other, as a PDF viewer shows them, the
    writing then matches up across the boundary. Only neighbours of the same width are
    joined (a page of another width does not line up with this one).
    """
    spills: list[tuple[list[Line], list[Stroke]]] = [([], []) for _ in pages]

    def joined(a: Page, b: Page) -> bool:
        return (all(math.isfinite(v) and v > 0 for v in (a.width, a.height, b.width, b.height))
                and abs(a.width - b.width) <= 1)

    def moved(page: Page, dy: float, beyond) -> tuple[list[Line], list[Stroke]]:
        lines = [Line((ln.p1[0], ln.p1[1] + dy), (ln.p2[0], ln.p2[1] + dy), ln.color, ln.width)
                 for ln in page.lines if beyond([ln.p1[1], ln.p2[1]])]
        strokes = [Stroke([(x, y + dy, pr) for x, y, pr in st.points], st.color, st.width)
                   for st in page.strokes if st.points and beyond([pt[1] for pt in st.points])]
        return lines, strokes

    for index, page in enumerate(pages):
        if index + 1 < len(pages) and joined(page, pages[index + 1]):
            below = pages[index + 1]
            # the page below has ink above its top: it lands at the bottom of this page
            lines, strokes = moved(below, page.height, lambda ys: min(ys) < 0)
            spills[index][0].extend(lines)
            spills[index][1].extend(strokes)
            # this page has ink below its bottom: it lands at the top of the page below
            lines, strokes = moved(page, -page.height, lambda ys, h=page.height: max(ys) > h)
            spills[index + 1][0].extend(lines)
            spills[index + 1][1].extend(strokes)
    return spills


def _render_page(doc: pymupdf.Document, note: Note, page: Page, number: int,
                 source: _SourcePage | None = None,
                 spill: tuple[list[Line], list[Stroke]] = ((), ())) -> bool:
    """Append one output page for `page`; returns whether any text box was drawn.
    `spill` is the neighbours' ink that reaches onto this page (`_spilled_ink`).

    With a `source`, the page gets the source page's size and scale and no background
    colour (the imported page, added later by `_ImportedPdf.draw`, is the paper)."""

    def warn(message: str) -> None:
        note.warnings.append(f"page {number}: {message}")

    if source is not None:
        out, scale = _imported_page(doc, page, source, warn)
    else:
        out, scale = _plain_page(doc, page, warn)

    if page.background is not None and source is None:
        rgb = _rgb_over_white(page.background)
        if rgb != _WHITE:
            r = out.rect
            _append_content(doc, out, (
                f"q {_rgb_operands(rgb)} rg {_num(r.x0)} {_num(r.y0)} "
                f"{_num(r.width)} {_num(r.height)} re f Q").encode())

    for index, image in enumerate(page.images, 1):
        _draw_image(out, image, scale, lambda m, i=index: warn(f"image {i} {m}"))

    lines, strokes = [*page.lines, *spill[0]], [*page.strokes, *spill[1]]
    if lines or strokes:
        content, alphas, unusable = _ink_content(lines, strokes, scale, out.rect.height)
        for alpha in sorted(alphas):
            value = _num(alpha / 255, 4)
            _set_resource(doc, out, "ExtGState", f"{_GS_PREFIX}{alpha}",
                          f"<</Type/ExtGState/CA {value}/ca {value}>>")
        _append_content(doc, out, content)
        if unusable:
            warn(f"{unusable} stroke(s) or line(s) with unusable coordinates or width; skipped")

    drew_text = False
    for index, box in enumerate(page.texts, 1):
        drew_text |= _draw_text(out, box, scale, lambda m, i=index: warn(f"text box {i} {m}"))
    return drew_text


def _plain_page(doc: pymupdf.Document, page: Page, warn) -> tuple[pymupdf.Page, float]:
    """A new page at the note page's size, 150 dpi."""
    scale = PX_TO_PT
    width, height = page.width * scale, page.height * scale
    if not (math.isfinite(width) and math.isfinite(height) and width >= 1 and height >= 1):
        warn(f"invalid page size {page.width} x {page.height}; drawn on A4")
        width, height = A4_PT
    elif width > MAX_PAGE_PT or height > MAX_PAGE_PT:
        warn(f"page of {page.width:.0f} x {page.height:.0f} px is larger than a PDF page can be; "
             "content beyond the limit is cut off")
        width, height = min(width, MAX_PAGE_PT), min(height, MAX_PAGE_PT)
    return doc.new_page(width=width, height=height), scale


def _imported_page(doc: pymupdf.Document, page: Page, source: _SourcePage,
                   warn) -> tuple[pymupdf.Page, float]:
    """A new page at the source page's size (as shown), and the pixel-to-point scale
    that maps the note page onto it (see the module docstring)."""
    width, height = source.rect.width, source.rect.height
    if not all(math.isfinite(v) and v > 0 for v in (page.width, page.height)):
        warn(f"invalid page size {page.width} x {page.height}; ink drawn at 150 dpi on the imported page")
        return doc.new_page(width=width, height=height), PX_TO_PT
    scale = width / page.width
    if abs(height / page.height - scale) > SHAPE_TOLERANCE * scale:
        warn(f"imported PDF page is {width:.1f} x {height:.1f} pt, a different shape from the "
             f"note page ({page.width:g} x {page.height:g} px); placed at the top, ink scaled to its width")
        height = min(max(height, page.height * scale), max(height, MAX_PAGE_PT))
    return doc.new_page(width=width, height=height), scale


# -- imported PDF -------------------------------------------------------------------------


@dataclass
class _SourcePage:
    number: int  # 0-based page number in the imported PDF
    rect: pymupdf.Rect  # the page as shown: (0, 0, width, height) after CropBox and /Rotate
    rotation: int
    links: list[dict]  # as `get_links()` gives them: rectangles in shown coordinates


class _ImportedPdf:
    """The note's imported PDF: which source page each output page shows, drawing them
    under the ink, re-creating their links, and the warnings for pages that fall back to
    blank paper."""

    def __init__(self, note: Note):
        self.note = note
        self.doc: pymupdf.Document | None = None
        self.opened = False
        self.unusable = ""  # why the whole PDF cannot be used
        self.blank = 0  # pages drawn blank because of `unusable`
        self.beyond = 0  # pages pointing past the end of the PDF
        self.pages: dict[int, _SourcePage | str] = {}  # str: why the page cannot be read
        self.placed: list[tuple[int, int, _SourcePage]] = []  # (output index, note page number, source)
        self.messages: list[str] = []

    @contextmanager
    def _mupdf(self):
        """Keep what MuPDF records while it works on the imported PDF. (MuPDF's message
        store is global; it is emptied first so that only these messages are kept.)"""
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            yield
        finally:
            lines = [m for m in pymupdf.TOOLS.mupdf_warnings().splitlines() if m.strip()]
            # MuPDF collapses repeated messages and its repeat counter survives the reset
            # above, so a window can open with "... repeated N times..." about a message
            # from before it (possibly from another note). Those lines are not ours.
            while lines and _REPEATED.fullmatch(lines[0].strip()):
                lines.pop(0)
            self.messages += lines

    def _open(self) -> None:
        self.opened = True
        if self.note.pdf is None:
            self.unusable = "the note does not have the imported PDF its pages are written on"
            return
        try:
            with self._mupdf():
                doc = pymupdf.open(stream=self.note.pdf, filetype="pdf")
        except Exception as e:
            self.unusable = f"imported PDF cannot be read ({_describe(e)})"
            return
        if doc.needs_pass:
            self.unusable = "imported PDF is protected by a password"
        elif doc.page_count == 0:
            self.unusable = "imported PDF cannot be read (no pages)"
        else:
            self.doc = doc
            return
        doc.close()

    def source(self, page: Page, number: int) -> _SourcePage | None:
        """The source page `page` is written on, or None for a plain page (including one
        whose source page cannot be used, which is counted for a warning)."""
        if page.pdf_page is None:
            return None
        if not self.opened:
            self._open()
        if self.doc is None:
            self.blank += 1
            return None
        index = page.pdf_page
        if not 0 <= index < self.doc.page_count:
            self.beyond += 1
            return None
        if index not in self.pages:
            try:
                with self._mupdf():
                    sp = self.doc[index]
                    rect, rotation, links = sp.rect, sp.rotation, sp.get_links()
                if rect.is_empty or not all(math.isfinite(v) for v in rect):
                    raise ValueError(f"page size {rect.width} x {rect.height}")
                self.pages[index] = _SourcePage(index, rect, rotation, links)
            except Exception as e:
                self.pages[index] = _describe(e)
        found = self.pages[index]
        if isinstance(found, str):
            self.note.warnings.append(f"page {number}: imported PDF page {index + 1} cannot be "
                                      f"read ({found}); drawn on blank paper")
            return None
        return found

    def place(self, output_index: int, number: int, source: _SourcePage) -> None:
        self.placed.append((output_index, number, source))

    def draw(self, doc: pymupdf.Document) -> None:
        """Draw each placed source page under its output page's content, then its links."""
        if not self.placed:
            return
        normalised: set[int] = set()
        shown: list[tuple[pymupdf.Page, _SourcePage]] = []
        for output_index, number, source in self.placed:
            out = doc[output_index]
            try:
                with self._mupdf():
                    if source.rotation and source.number not in normalised:
                        # show_pdf_page misplaces rotated pages: show the unrotated page
                        # and rotate it here instead (in-memory copy only).
                        self.doc[source.number].set_rotation(0)
                    normalised.add(source.number)
                    out.show_pdf_page(pymupdf.Rect(0, 0, source.rect.width, source.rect.height),
                                      self.doc, source.number, overlay=False,
                                      rotate=-source.rotation)
                shown.append((out, source))
            except Exception as e:
                self.note.warnings.append(f"page {number}: imported PDF page {source.number + 1} could "
                                          f"not be drawn ({_describe(e)}); drawn on blank paper")
        first_shown: dict[int, int] = {}
        for out, source in shown:
            first_shown.setdefault(source.number, out.number)
        for out, source in shown:
            for link in source.links:
                _copy_link(out, link, first_shown)

    def report(self) -> None:
        if self.blank:
            self.note.warnings.append(f"{self.unusable}; {self.blank} page(s) drawn on blank paper")
        if self.beyond:
            self.note.warnings.append(
                f"{self.beyond} page(s) point past the end of the imported PDF "
                f"({self.doc.page_count} pages); drawn on blank paper")
        if self.messages and self.doc is not None:  # else `unusable` already says why
            text = "; ".join(dict.fromkeys(m.strip() for m in self.messages))
            text = text if len(text) <= 200 else text[:197] + "..."
            self.note.warnings.append(f"MuPDF reported problems in the imported PDF: {text}")

    def close(self) -> None:
        if self.doc is not None:
            self.doc.close()


def _copy_link(out: pymupdf.Page, link: dict, first_shown: dict[int, int]) -> None:
    """Re-create a source page's link on `out` (same coordinates: `out` is the source page
    as shown). Web links are kept; links to a page of the imported PDF point at the first
    output page showing it, or are dropped if the note does not show it."""
    try:
        rect = pymupdf.Rect(link["from"])
        kind = link.get("kind")
        if kind == pymupdf.LINK_URI and link.get("uri"):
            out.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": link["uri"]})
        elif kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and link.get("page", -1) in first_shown:
            out.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": first_shown[link["page"]],
                             "to": pymupdf.Point(link.get("to") or (0, 0)), "zoom": 0})
    except Exception:  # a malformed link is not worth losing the page over
        pass


# -- ink --------------------------------------------------------------------------------


def _ink_content(lines: list[Line], strokes: list[Stroke], scale: float,
                 height_pt: float) -> tuple[bytes, set[int], int]:
    """The content stream for `lines` then `strokes` (in that order), the alpha values
    that need an ExtGState, and the number of strokes skipped as unusable."""
    k = scale * _UNITS
    out = [f"q 0.01 0 0 -0.01 0 {_num(height_pt)} cm 1 J 1 j"]
    state = _InkState(out)
    unusable = 0
    paths = [((ln.p1[0], ln.p1[1], 0.0), (ln.p2[0], ln.p2[1], 0.0)) for ln in lines]
    styles = [(ln.color, ln.width) for ln in lines]
    paths += [s.points for s in strokes]
    styles += [(s.color, s.width) for s in strokes]
    for points, (color, width) in zip(paths, styles):
        if not points:
            continue
        try:
            xy = [(round(x * k), round(y * k)) for x, y, _ in points]
            w = max(1, round(width * k))
            if (max(map(max, xy)) > _MAX_UNITS or min(map(min, xy)) < -_MAX_UNITS
                    or w > _MAX_UNITS):
                raise OverflowError("beyond what a PDF number can hold")
        except (ValueError, OverflowError, TypeError):  # NaN / inf / not a number / absurd
            unusable += 1
            continue
        last = xy[0]
        path = [last]
        for p in xy:
            if p != last:
                path.append(p)
                last = p
        if len(path) == 1:
            state.fill(color)
            out.append(_circle(last[0], last[1], w / 2))
        else:
            state.stroke(color, w)
            x0, y0 = path[0]
            out.append(f"{x0} {y0} m " + " ".join([f"{x} {y} l" for x, y in path[1:]]) + " S")
    out.append("Q")
    return "\n".join(out).encode("ascii"), state.alphas, unusable


class _InkState:
    """Emits colour, width and opacity operators only when they change."""

    def __init__(self, out: list[str]):
        self.out = out
        self.stroke_rgb: str | None = None
        self.fill_rgb: str | None = None
        self.width: int | None = None
        self.alpha = 255
        self.alphas: set[int] = set()

    def _colour(self, color: int) -> str:
        argb = color & 0xFFFFFFFF
        alpha = argb >> 24
        if alpha != self.alpha:
            self.out.append(f"/{_GS_PREFIX}{alpha} gs")
            self.alphas.add(alpha)
            self.alpha = alpha
        return _rgb_operands(((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF))

    def stroke(self, color: int, width: int) -> None:
        rgb = self._colour(color)
        if rgb != self.stroke_rgb:
            self.out.append(f"{rgb} RG")
            self.stroke_rgb = rgb
        if width != self.width:
            self.out.append(f"{width} w")
            self.width = width

    def fill(self, color: int) -> None:
        rgb = self._colour(color)
        if rgb != self.fill_rgb:
            self.out.append(f"{rgb} rg")
            self.fill_rgb = rgb


def _circle(cx: int, cy: int, r: float) -> str:
    """A filled circle as four Bezier curves (content-stream units)."""
    r = max(r, 0.5)
    c = r * _KAPPA

    def n(v: float) -> str:
        return _num(v, 1)

    return (f"{n(cx + r)} {n(cy)} m "
            f"{n(cx + r)} {n(cy + c)} {n(cx + c)} {n(cy + r)} {n(cx)} {n(cy + r)} c "
            f"{n(cx - c)} {n(cy + r)} {n(cx - r)} {n(cy + c)} {n(cx - r)} {n(cy)} c "
            f"{n(cx - r)} {n(cy - c)} {n(cx - c)} {n(cy - r)} {n(cx)} {n(cy - r)} c "
            f"{n(cx + c)} {n(cy - r)} {n(cx + r)} {n(cy - c)} {n(cx + r)} {n(cy)} c f")


# -- images and text ----------------------------------------------------------------------


def _scaled_rect(rect: tuple[float, float, float, float], scale: float) -> pymupdf.Rect:
    return pymupdf.Rect(*(v * scale for v in rect)).normalize()


def _draw_image(out: pymupdf.Page, image: Image, scale: float, warn) -> None:
    rect = _scaled_rect(image.rect, scale)
    if rect.is_empty or not all(math.isfinite(v) for v in rect):
        warn("has an empty or invalid rectangle; skipped")
        return
    turn = image.rotation % 360 if math.isfinite(image.rotation) else image.rotation
    if not (math.isfinite(turn) and min(turn, 360 - turn) < 0.01):
        warn(f"is rotated {image.rotation:g} degrees; drawn unrotated")
    try:
        out.insert_image(rect, stream=image.data, keep_proportion=False)
    except Exception as e:  # undecodable image data must not lose the rest of the note
        warn(f"could not be drawn ({_describe(e)}); skipped")


def _draw_text(out: pymupdf.Page, box: TextBox, scale: float, warn) -> bool:
    text = box.text.replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL)
    if not text.strip():
        return False
    rect = _scaled_rect(box.rect, scale)
    if rect.is_empty or not all(math.isfinite(v) for v in rect):
        warn("has an empty or invalid rectangle; skipped")
        return False
    argb = box.color & 0xFFFFFFFF
    size = box.size * scale if math.isfinite(box.size) and box.size > 0 else 12.0
    css = (f"* {{font-family: sans-serif; font-size: {_num(size)}pt; "
           f"color: #{argb & 0xFFFFFF:06x}; margin: 0; padding: 0; white-space: pre-wrap;}}")
    try:
        # MuPDF picks fallback fonts for non-Latin text and shrinks the text (never
        # below 0 scale) until it fits the rectangle.
        spare, shrink = out.insert_htmlbox(rect, html.escape(text), css=css, scale_low=0,
                                      opacity=(argb >> 24) / 255)
    except Exception as e:
        warn(f"could not be drawn ({_describe(e)}); skipped")
        return False
    if spare < 0:
        warn("does not fit its rectangle; skipped")
        return False
    if shrink < MIN_TEXT_SCALE:
        warn(f"was shrunk to {shrink:.0%} of its size to fit its rectangle; it may be hard to read")
    return True


# -- PDF plumbing -------------------------------------------------------------------------


def _append_content(doc: pymupdf.Document, page: pymupdf.Page, content: bytes) -> None:
    """Add `content` as a new content stream drawn after (on top of) the page's own."""
    existing = page.get_contents()
    if existing:
        page.wrap_contents()  # balanced q/Q, so earlier content cannot leak state
        existing = page.get_contents()
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content)
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in [*existing, xref]) + "]")


def _set_resource(doc: pymupdf.Document, page: pymupdf.Page, category: str, name: str,
                  value: str) -> None:
    """Set /Resources/<category>/<name> of `page`, following indirect dictionaries
    (`xref_set_key` cannot write through an indirect object in a key path)."""
    holder, path = page.xref, "Resources"
    kind, ref = doc.xref_get_key(holder, path)
    if kind == "xref":
        holder, path = int(ref.split()[0]), ""
    elif kind != "dict":
        doc.xref_set_key(holder, path, "<<>>")
    path = f"{path}/{category}" if path else category
    kind, ref = doc.xref_get_key(holder, path)
    if kind == "xref":
        holder, path = int(ref.split()[0]), ""
    doc.xref_set_key(holder, f"{path}/{name}" if path else name, value)


def _rgb_over_white(color: int) -> tuple[int, int, int]:
    """An ARGB paper colour as the opaque RGB it shows on white paper."""
    argb = color & 0xFFFFFFFF
    a = (argb >> 24) / 255
    return tuple(round(a * ((argb >> s) & 0xFF) + (1 - a) * 255) for s in (16, 8, 0))  # type: ignore[return-value]


def _rgb_operands(rgb: tuple[int, int, int]) -> str:
    return " ".join(_num(c / 255, 4) for c in rgb)


def _num(value: float, places: int = 2) -> str:
    """A PDF number: fixed point, trailing zeros dropped, never an exponent."""
    if not math.isfinite(value):
        raise ValueError(f"not a PDF number: {value}")
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _describe(error: Exception) -> str:
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    text = text if len(text) <= 100 else text[:97] + "..."
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
