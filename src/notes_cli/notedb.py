"""Load one Notein note zip into plain dataclasses for the renderer.

A note zip holds `note_meta.json` and a SQLite database `note_database_note_<id>_db`
with its `-wal` and `-shm` files, plus `note_pdf_*.pdf` (an imported PDF) and
`note_image_<id>.png` members. Coordinates are `paper_spec` pixels (150 dpi).

Facts settled from the real backup (2026-11-14, all 423 notes in NoteInDataSync):

(a) HORIZONTAL pages: `paper_spec` stores the PORTRAIT size (width < height) and the
    page is shown landscape, so width and height are swapped. Evidence:
    - Every bundled PDF page behind a HORIZONTAL page is landscape, and only the
      swapped reading gives one scale for both axes:
        146 pages (fm1_week4/5/6/8/9/10)  spec 1315x1754, PDF 510.2x382.7 pt:
            1754/510.2 = 3.438, 1315/382.7 = 3.436 (unswapped: 2.58 vs 4.58)
        17 pages (ELC tutorial_09/10/11)  spec 986x1754, PDF 453.5x255.1 pt:
            1754/453.5 = 3.868, 986/255.1 = 3.865
        3 pages (Ex2_data)                spec 1240x1754, PDF 841.9x595.3 pt:
            1754/841.9 = 2.083, 1240/595.3 = 2.083 (= 150/72)
      VERTICAL PDF pages need no swap (e.g. 222 pages 1240x1753 on 595.3x841.9 pt).
    - Ink: only 4 HORIZONTAL pages have any. Ex2_data reaches x=1467 with y <= 917 (past
      the stored width 1240, inside 1754 x 1240); fm1_week4 has a dot at x=1335..1343
      (past the stored width 1315). The other two Ex2_data pages fit either reading.
      None contradicts the swap.
    - The A4 HORIZONTAL pages are thin evidence: there are 11 such PageEntity rows, 10
      of them the never-shown default page (not in page_list), and the one listed page
      (Ex2_data) is blank. Their theme `paperStyle` also stores 1240 x 1754 (portrait).
      The same rule is applied to them.
    Rule: HORIZONTAL -> width = max(w, h), height = min(w, h) (identical to a swap for
    all real data, and safe if a spec were ever stored landscape). `rotation_direction_index`
    is 1 on a single page (DBA SGT W2, VERTICAL) whose ink fits the portrait page; ignored.

(b) Unbounded (`无界`, `PageEntity.unbounded = 1`) pages: `paper_spec` says 1920x1920 but
    content goes far beyond it. Page size = bounding box of the loaded content (stroke
    points widened by half the stroke width, lines likewise, image and text rectangles)
    plus a 40 px margin on every side; content is translated so the box starts at the
    margin. An unbounded page with no content keeps its `paper_spec` size. Real data: 2
    pages in 2 notes. One such note holds 951 binary strokes (row bounds 421,250 ..
    3455,3823); decoded with a throwaway decoder following the plan's blob format it
    became a 3115 x 3654 px page, and rendered, the whole ER diagram sits on it with
    an even margin and nothing clipped. With `inkblob.decode_stroke` (BLOB_DECODER) it
    is 3114.6 x 3654.1 px. A note with no content and stays 1920 x 1920.

Other facts this loader relies on (same survey):
- WAL: every database is in WAL mode and every zip has non-empty `-wal` and `-shm`
  members. Today only one WAL (FM2 W6 LGT) has valid committed frames, and those
  duplicate pages already in the main file, but rows may exist only in the WAL. The
  three members are extracted to a temp dir and opened with `mode=ro`, which does read
  WAL frames (checked: a row committed only to the WAL is returned, with or without
  the `-shm`, and with a stale `-shm` from an earlier snapshot). `mode=ro` never writes
  the database or the WAL, but it opens the `-shm` (wal-index) read-write when it can
  and creates it when it is missing: a missing `-shm` in a read-only directory fails
  with "unable to open database file" (an existing read-only `-shm` works, SQLite 3.46).
  So it only ever runs on the temp copy, never in the backup folder. `immutable=1`
  would avoid the `-shm` but silently ignores the WAL, so it is not used.
- `note_meta.json`'s `pdfInfo.pdfPath` is empty in every real note; the path is read
  from the page's `paper_theme.pdfInfo.pdfPath`, else from `PdfInfoEntity.pdf_path`.
  88 notes bundle exactly one PDF; every PDF page of a note points at that same PDF and
  its basename always equals the member name after `note_pdf_`.
- `NoteContentEntity` has several rows in 33 notes (copies keep the source note's
  row); exactly one row per note has the note's id, and that one is used.
- Page layers are always `paper_theme` and `default`, both visible. 465 strokes, 97
  shapes, 51 images and 1 text box use the layer id `unspecified`, which has no
  PageLayerEntity row; such elements are drawn (only layers stored as invisible hide
  content).
- Paper themes: `LambdaPaperTheme` with `baseTheme` `ColorPaperTheme` (`color`) and a
  `paperStyle` of `NewLinePaperStyle` or `BlankPaperStyle`, or `PdfPaperTheme`. The
  ruled lines of `NewLinePaperStyle` are not part of `Page` and are not drawn.
- Strokes: `record_json` (type 1, and 39 strokes of type 5 that are two-point straight
  lines) or `ink_stroke_blob`, never both. Shapes: only type 7 (a two-point line).
- 51 images have `page_id = "link_ref"`: snippets linked in from another note. Each
  has one `DualLinkRefEntity` row (`item_id` = image id) whose `page_id` is the page
  showing it, with the same bounds (within 3e-5 px). They are placed on that page.
- Tables not read: `OutlineEntity` (table of contents, 167 rows) and
  `DualLinkSourceEntity` (link anchors, 51 rows) hold no page content. `GlyphEntity`,
  `TableBlockEntity`, `BeautifiedChunkEntity`, `QuoteEntity`, `HyperLinkEntity`,
  `CommentEntity`, audio tables etc. are empty in every note; a note where any table
  other than the known non-content ones has rows gets a warning.
"""

from __future__ import annotations

import html as html_lib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Stroke:
    points: list[tuple[float, float, float]]  # (x, y, pressure)
    color: int  # Android ARGB, signed (JSON and blob strokes alike); mask with 0xFFFFFFFF
    width: float


@dataclass
class Line:
    p1: tuple[float, float]
    p2: tuple[float, float]
    color: int
    width: float


@dataclass
class Image:
    data: bytes
    rect: tuple[float, float, float, float]  # left, top, right, bottom
    rotation: float


@dataclass
class TextBox:
    text: str  # HTML stripped to plain text; "\n" separates lines
    rect: tuple[float, float, float, float]
    size: float  # font size in page pixels
    color: int


@dataclass
class Page:
    width: float  # in page pixels, already oriented
    height: float
    background: int | None  # ARGB, None when a PDF page is the background
    pdf_page: int | None  # 0-based index into Note.pdf
    strokes: list[Stroke] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    texts: list[TextBox] = field(default_factory=list)


@dataclass
class Note:
    id: str
    title: str
    pages: list[Page]
    pdf: bytes | None
    warnings: list[str]


# Decoder for `ink_stroke_blob` strokes: `inkblob.decode_stroke`, assigned at the end of
# this module. While None, blob strokes are skipped with one warning per note.
BLOB_DECODER: Callable[[bytes], Stroke] | None = None

UNBOUNDED_MARGIN = 40.0
WHITE = -1  # ARGB 0xFFFFFFFF as a signed Android int
BLACK = -16777216  # ARGB 0xFF000000
A4 = (1240.0, 1754.0)
UNBOUNDED_NAME = "无界"
LINE_SHAPE = 7
LINK_REF_PAGE = "link_ref"

NOTE_META = "note_meta.json"
DB_PREFIX = "note_database_note_"
PDF_PREFIX = "note_pdf_"
IMAGE_PREFIX = "note_image_"
PDF_MISSING = "imported PDF not in backup"

# Tables this loader reads.
READ_TABLES = {
    "NoteContentEntity", "PageEntity", "PageLayerEntity", "PdfInfoEntity", "StrokeEntity",
    "ShapeEntity", "ImageEntity", "TextBoxEntity", "DualLinkRefEntity",
}
# Tables that hold nothing drawn on a page: bookkeeping, the table of contents, link
# anchors, the archived source strokes of beautified handwriting, audio sync records.
NON_CONTENT_TABLES = {
    "android_metadata", "room_master_table", "OutlineEntity", "DualLinkSourceEntity",
    "BeautifySourceStrokeEntity", "NoteAudioFileIdsFieldEntity", "AudioSyncRecordEntity",
}


def load_note(zip_path: Path) -> Note:
    """Read the note zip at `zip_path` (never modified) into a `Note`.

    Raises ValueError if the zip has no note database, and lets zipfile/sqlite3/json
    errors from a damaged zip propagate; the temp copy is removed in every case.
    """
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        meta = _meta(zf, names, zip_path)
        note_id = meta["id"]
        title = meta.get("title") if isinstance(meta.get("title"), str) else ""
        db_member = _database_member(names, note_id, zip_path)
        with tempfile.TemporaryDirectory(prefix="notes-cli-") as scratch:
            db_path = Path(scratch).resolve() / "note.db"
            for suffix in ("", "-wal", "-shm"):
                if db_member + suffix in names:
                    with zf.open(db_member + suffix) as src, open(f"{db_path}{suffix}", "wb") as dst:
                        shutil.copyfileobj(src, dst)
            con = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
            try:
                return _Loader(zf, names, con, note_id, title).load()
            finally:
                con.close()


def _meta(zf: zipfile.ZipFile, names: set[str], zip_path: Path) -> dict:
    if NOTE_META not in names:
        raise ValueError(f"{zip_path.name}: no {NOTE_META} in zip")
    meta = json.loads(zf.read(NOTE_META))
    if not isinstance(meta, dict) or not isinstance(meta.get("id"), str) or not meta["id"]:
        raise ValueError(f"{zip_path.name}: {NOTE_META} has no note id")
    return meta


def _database_member(names: set[str], note_id: str, zip_path: Path) -> str:
    expected = f"{DB_PREFIX}{note_id}_db"
    if expected in names:
        return expected
    found = sorted(n for n in names if n.startswith(DB_PREFIX) and n.endswith("_db"))
    if len(found) == 1:
        return found[0]
    raise ValueError(f"{zip_path.name}: expected one note database {expected}, found {found}")


class _PageBuild:
    """A page being assembled; `unbounded` pages are resized once all content is in."""

    def __init__(self, page: Page, unbounded: bool):
        self.page = page
        self.unbounded = unbounded


class _Loader:
    def __init__(self, zf: zipfile.ZipFile, names: set[str], con: sqlite3.Connection,
                 note_id: str, title: str):
        self.zf = zf
        self.names = names
        self.con = con
        self.note_id = note_id
        self.title = title
        self.warnings: list[str] = []
        self.trashed: set[str] = set()  # ids of pages skipped on purpose
        self.off_page = 0  # elements whose page is not one of the note's pages
        self.tables = {
            name: {row[1] for row in con.execute(f"PRAGMA table_info(`{name}`)")}
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    def load(self) -> Note:
        page_ids, note_pdf_info_id = self._note_content()
        hidden = self._hidden_layers()
        builds, order, pdf = self._pages(page_ids, note_pdf_info_id)
        self._strokes(builds, hidden)
        self._shapes(builds, hidden)
        self._images(builds, hidden)
        self._texts(builds, hidden)
        for build in builds.values():
            if build.unbounded:
                _fit_to_content(build.page)
        if self.off_page:
            self.warnings.append(
                f"{self.off_page} element(s) on pages that are not in the note's page list; skipped")
        self._unread_tables()
        return Note(self.note_id, self.title, [b.page for b in order], pdf, self.warnings)

    # -- note and pages ---------------------------------------------------------------

    def _note_content(self) -> tuple[list[str], str | None]:
        rows = self.con.execute("SELECT id, page_list, pdf_info_id FROM NoteContentEntity").fetchall()
        mine = [r for r in rows if r[0] == self.note_id]
        if mine:
            row = mine[0]
        elif len(rows) == 1:
            row = rows[0]
        else:
            raise ValueError(f"no NoteContentEntity row for note {self.note_id}")
        page_ids = json.loads(row[1]) if row[1] else []
        if not isinstance(page_ids, list):
            raise ValueError(f"page_list of note {self.note_id} is not a list")
        return [p for p in page_ids if isinstance(p, str)], row[2]

    def _hidden_layers(self) -> set[str]:
        if "PageLayerEntity" not in self.tables:
            return set()
        return {lid for lid, visible in self.con.execute("SELECT id, visible FROM PageLayerEntity")
                if not visible}

    def _pages(self, page_ids: list[str], note_pdf_info_id: str | None):
        self.con.row_factory = sqlite3.Row
        try:
            rows = {r["id"]: r for r in self.con.execute("SELECT * FROM PageEntity")}
        finally:
            self.con.row_factory = None
        pdf_paths = {}
        if "PdfInfoEntity" in self.tables:
            pdf_paths = dict(self.con.execute("SELECT id, pdf_path FROM PdfInfoEntity"))

        builds: dict[str, _PageBuild] = {}
        order: list[_PageBuild] = []
        missing_pages = 0
        unknown_themes: Counter[str] = Counter()
        pdf_member: str | None = None  # the note's PDF: the first bundled one a page uses
        unbundled: Counter[str] = Counter()  # PDF basename -> pages drawn blank
        second_pdf: Counter[str] = Counter()
        for page_id in page_ids:
            if page_id in builds:
                order.append(builds[page_id])
                continue
            row = rows.get(page_id)
            if row is None:
                missing_pages += 1
                continue
            if _col(row, "is_in_trash_bin"):
                self.trashed.add(page_id)
                continue
            spec = _json_dict(_col(row, "paper_spec"))
            theme = _json_dict(_col(row, "paper_theme"))
            width, height = _paper_size(spec, theme)
            if _col(row, "page_orientation") == "HORIZONTAL":
                width, height = max(width, height), min(width, height)
            unbounded = bool(_col(row, "unbounded")) or spec.get("name") == UNBOUNDED_NAME

            background: int | None = WHITE
            pdf_page = None
            theme_type = str(theme.get("type", ""))
            if theme_type.endswith("PdfPaperTheme"):
                info = theme.get("pdfInfo") if isinstance(theme.get("pdfInfo"), dict) else {}
                path = info.get("pdfPath") or pdf_paths.get(info.get("id")) \
                    or pdf_paths.get(note_pdf_info_id) \
                    or (next(iter(pdf_paths.values())) if len(pdf_paths) == 1 else "")
                name = os.path.basename(str(path or ""))
                member = PDF_PREFIX + name if name else None
                page_num = theme.get("pageNum")
                if member not in self.names or not isinstance(page_num, int) or page_num < 0:
                    unbundled[name or "unknown PDF"] += 1
                elif pdf_member is not None and member != pdf_member:
                    second_pdf[name] += 1
                else:
                    pdf_member = member
                    background, pdf_page = None, page_num
            else:
                color = _theme_colour(theme)
                if color is not None:
                    background = color
                elif theme:
                    unknown_themes[theme_type.rsplit(".", 1)[-1] or "untyped"] += 1

            build = _PageBuild(Page(width, height, background, pdf_page), unbounded)
            builds[page_id] = build
            order.append(build)

        if missing_pages:
            self.warnings.append(f"{missing_pages} page(s) in page_list not in the database; skipped")
        for name, n in sorted(unbundled.items()):
            self.warnings.append(f"{PDF_MISSING} ({name}); {n} page(s) drawn on blank paper")
        for name, n in sorted(second_pdf.items()):
            self.warnings.append(
                f"note uses more than one imported PDF; {n} page(s) of {name} drawn on blank paper")
        for name, n in sorted(unknown_themes.items()):
            self.warnings.append(f"unrecognised paper theme {name} on {n} page(s); drawn on white")
        pdf = self.zf.read(pdf_member) if pdf_member is not None else None
        if pdf is not None:
            pdf = self._check_pdf_pages(pdf, builds)
        return builds, order, pdf

    def _check_pdf_pages(self, pdf: bytes, builds: dict[str, _PageBuild]) -> bytes | None:
        """Pages pointing past the end of the imported PDF (or at an unreadable PDF) become
        blank pages with a warning, so the renderer never has to drop one silently."""
        import pymupdf

        try:
            with pymupdf.open(stream=pdf, filetype="pdf") as doc:
                count = doc.page_count
        except Exception as e:
            count = -1
            self.warnings.append(f"imported PDF cannot be read ({type(e).__name__}); its pages are drawn on blank paper")
        if count == 0:  # e.g. a truncated file that MuPDF "repairs" to nothing
            self.warnings.append("imported PDF has no pages (damaged?); its pages are drawn on blank paper")
        beyond = 0
        for build in builds.values():
            page = build.page
            if page.pdf_page is not None and page.pdf_page >= count:
                page.pdf_page, page.background = None, WHITE
                beyond += 1
        if beyond and count > 0:
            self.warnings.append(
                f"{beyond} page(s) point past the end of the imported PDF ({count} pages); drawn on blank paper")
        return pdf if count > 0 else None

    # -- elements ---------------------------------------------------------------------

    def _page(self, builds: dict[str, _PageBuild], page_id) -> _PageBuild | None:
        """The page an element belongs to; counts elements that belong to no page of the
        note (pages in the trash are skipped on purpose and not counted)."""
        build = builds.get(page_id)
        if build is None and page_id not in self.trashed and page_id != LINK_REF_PAGE:
            self.off_page += 1
        return build

    def _select(self, table: str, columns: list[str]):
        """Rows of `table` in drawing order; a column the table lacks reads as NULL."""
        have = self.tables[table]
        cols = ", ".join(f"`{c}`" if c in have else "NULL" for c in columns)
        order = "creation_time, rowid" if "creation_time" in have else "rowid"
        return self.con.execute(f"SELECT {cols} FROM `{table}` ORDER BY {order}")

    def _strokes(self, builds: dict[str, _PageBuild], hidden: set[str]) -> None:
        if "StrokeEntity" not in self.tables:
            return
        decoder = BLOB_DECODER
        no_decoder = failed = bad_json = empty = linked = 0
        for page_id, layer, record, blob in self._select(
                "StrokeEntity", ["page_id", "layer_id", "record_json", "ink_stroke_blob"]):
            build = self._page(builds, page_id)
            if build is None:
                linked += page_id == LINK_REF_PAGE
                continue
            if layer in hidden:
                continue
            if record is not None:
                try:
                    data = json.loads(record)
                    points = [(float(p["x"]), float(p["y"]), float(p.get("p", 1.0)))
                              for p in data["points"]]
                    stroke = Stroke(points, int(data["color"]), float(data["width"]))
                    if not all(math.isfinite(v) for pt in points for v in pt[:2]):
                        raise ValueError("non-finite coordinate")
                except (ValueError, KeyError, TypeError, OverflowError):
                    bad_json += 1
                    continue
            elif blob is not None:
                if decoder is None:
                    no_decoder += 1
                    continue
                try:
                    stroke = decoder(bytes(blob))
                except Exception:  # one bad blob must not lose the rest of the note
                    failed += 1
                    continue
            else:
                empty += 1
                continue
            if stroke.points:
                build.page.strokes.append(stroke)
            else:
                empty += 1
        if no_decoder:
            self.warnings.append(
                f"{no_decoder} binary ink stroke(s) (ink_stroke_blob) skipped: no decoder")
        if failed:
            self.warnings.append(f"{failed} binary ink stroke(s) could not be decoded; skipped")
        if bad_json:
            self.warnings.append(f"{bad_json} stroke(s) with unreadable record_json; skipped")
        if empty:
            self.warnings.append(f"{empty} stroke row(s) with no ink data; skipped")
        if linked:
            self.warnings.append(f"{linked} linked stroke(s) from another note not rendered")

    def _shapes(self, builds: dict[str, _PageBuild], hidden: set[str]) -> None:
        if "ShapeEntity" not in self.tables:
            return
        unsupported: Counter[str] = Counter()
        for page_id, layer, kind, points, color, width in self._select(
                "ShapeEntity", ["page_id", "layer_id", "type", "points", "color", "width"]):
            build = self._page(builds, page_id)
            if build is None or layer in hidden:
                continue
            try:
                pts = json.loads(points)
                xy = [(float(p["x"]), float(p["y"])) for p in pts]
                color, width = int(color), float(width)
                if not all(math.isfinite(v) for pt in xy for v in pt):
                    raise ValueError("non-finite coordinate")
            except (ValueError, KeyError, TypeError, OverflowError):
                unsupported[f"type {kind} with unreadable data"] += 1
                continue
            if kind == LINE_SHAPE and len(xy) == 2:
                build.page.lines.append(Line(xy[0], xy[1], color, width))
            elif kind == LINE_SHAPE:
                unsupported[f"type {kind} with {len(xy)} points"] += 1
            else:
                unsupported[f"type {kind}"] += 1
        for what, n in sorted(unsupported.items()):
            self.warnings.append(f"{n} shape(s) of {what} not supported; skipped")

    def _images(self, builds: dict[str, _PageBuild], hidden: set[str]) -> None:
        if "ImageEntity" not in self.tables:
            return
        link_pages: dict[str, str] = {}
        if "DualLinkRefEntity" in self.tables:
            for item_id, page_id in self.con.execute(
                    "SELECT item_id, page_id FROM DualLinkRefEntity ORDER BY rowid"):
                link_pages.setdefault(item_id, page_id)
        missing = unplaced = 0
        for image_id, uri, layer, bounds, rotation, page_id, *ltrb in self._select(
                "ImageEntity", ["id", "uri", "layer_id", "bounds", "rotation", "page_id",
                                "left", "top", "right", "bottom"]):
            if page_id == LINK_REF_PAGE:
                page_id = link_pages.get(image_id)
                if page_id is None:
                    unplaced += 1
                    continue
            build = self._page(builds, page_id)
            if build is None or layer in hidden:
                continue
            rect = _rect(bounds, ltrb)
            if rect is None:
                unplaced += 1
                continue
            member = IMAGE_PREFIX + os.path.basename(str(uri or ""))
            if member not in self.names:
                member = f"{IMAGE_PREFIX}{image_id}.png"
            if member not in self.names:
                missing += 1
                continue
            build.page.images.append(Image(self.zf.read(member), rect, _number(rotation, 0.0)))
        if missing:
            self.warnings.append(f"{missing} image(s) not in backup; skipped")
        if unplaced:
            self.warnings.append(f"{unplaced} image(s) with no page or position; skipped")

    def _texts(self, builds: dict[str, _PageBuild], hidden: set[str]) -> None:
        if "TextBoxEntity" not in self.tables:
            return
        unplaced = 0
        for page_id, layer, text, size, color, bounds, *ltrb in self._select(
                "TextBoxEntity", ["page_id", "layer_id", "text", "text_size",
                                  "default_text_color", "bounds", "left", "top", "right", "bottom"]):
            build = self._page(builds, page_id)
            if build is None or layer in hidden:
                continue
            rect = _rect(bounds, ltrb)
            if rect is None:
                unplaced += 1
                continue
            text = str(text or "")
            styled = _STYLE_RE.sub("", text)  # inline styles only, not a <style> block
            html_size = _FONT_SIZE_RE.search(styled)
            html_color = _COLOR_RE.search(styled)
            build.page.texts.append(TextBox(
                text=html_to_text(text),
                rect=rect,
                size=float(html_size.group(1)) if html_size else (_number(size, 30.0) or 30.0),
                color=(_rgb_to_argb(html_color.group(1)) if html_color
                       else color if isinstance(color, int) else BLACK),
            ))
        if unplaced:
            self.warnings.append(f"{unplaced} text box(es) with no position; skipped")

    def _unread_tables(self) -> None:
        for table in sorted(self.tables):
            if table in READ_TABLES or table in NON_CONTENT_TABLES or table.startswith("sqlite_"):
                continue
            (n,) = self.con.execute(f"SELECT count(*) FROM `{table}`").fetchone()
            if n:
                self.warnings.append(f"{table}: {n} row(s) of content this tool does not render")


# -- helpers ----------------------------------------------------------------------------


def _col(row: sqlite3.Row, name: str):
    return row[name] if name in row.keys() else None


def _number(value, default: float) -> float:
    """`value` as a finite float, else `default` (for columns that should be numeric)."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _json_dict(text) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _paper_size(spec: dict, theme: dict) -> tuple[float, float]:
    """`paper_spec` width/height, else the theme's paperStyle size, else A4 (as stored,
    i.e. before orientation)."""
    width, height = _positive(spec.get("width")), _positive(spec.get("height"))
    if width and height:
        return width, height
    style = theme.get("paperStyle") if isinstance(theme.get("paperStyle"), dict) else {}
    width, height = _positive(style.get("paperWidth")), _positive(style.get("paperHeight"))
    if width and height:
        return width, height
    return A4


def _theme_colour(theme: dict) -> int | None:
    """The background ARGB of a plain-paper theme: `color` on the theme itself
    (`ColorPaperTheme`) or on its `baseTheme` chain (`LambdaPaperTheme`)."""
    seen = 0
    while isinstance(theme, dict) and seen < 8:
        color = theme.get("color")
        if isinstance(color, int) and not isinstance(color, bool):
            return color
        theme = theme.get("baseTheme")
        seen += 1
    return None


def _rect(bounds, ltrb) -> tuple[float, float, float, float] | None:
    """A rectangle from a `bounds` JSON object, else from the left/top/right/bottom columns."""
    data = _json_dict(bounds)
    try:
        rect = tuple(float(data[k]) for k in ("left", "top", "right", "bottom"))
        if all(math.isfinite(v) for v in rect):
            return rect  # type: ignore[return-value]
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    if all(isinstance(v, (int, float)) and math.isfinite(v) for v in ltrb):
        return tuple(float(v) for v in ltrb)  # type: ignore[return-value]
    return None


_STYLE_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_BREAK_RE = re.compile(r"<br\s*/?>|</(?:div|p|li|h[1-6])\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*px", re.IGNORECASE)
_COLOR_RE = re.compile(r"(?<![-\w])color\s*:\s*#([0-9a-fA-F]{6})\b")


def html_to_text(text: str) -> str:
    """Plain text of a Notein text box's HTML: `<br>` and block ends become line breaks,
    tags and `<style>` blocks go, entities are decoded, spaces inside a line are kept."""
    text = _STYLE_RE.sub("", text)
    # Raw newlines are the serializer's formatting; real breaks are <br> or block tags.
    text = text.replace("\r", "").replace("\n", " ")
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text).replace("﻿", "").replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.split("\n")).strip("\n")


def _rgb_to_argb(hex_rgb: str) -> int:
    """`3b3b3b` -> opaque ARGB as a signed 32-bit int, like the app's colour ints."""
    value = 0xFF000000 | int(hex_rgb, 16)
    return value - (1 << 32) if value >= 1 << 31 else value


def _fit_to_content(page: Page) -> None:
    """Size an unbounded page to its content plus a margin, moving the content so the
    content box starts at the margin. A page with no content keeps its size."""
    boxes = []
    for s in page.strokes:
        half = s.width / 2
        xs = [p[0] for p in s.points]
        ys = [p[1] for p in s.points]
        boxes.append((min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half))
    for ln in page.lines:
        half = ln.width / 2
        boxes.append((min(ln.p1[0], ln.p2[0]) - half, min(ln.p1[1], ln.p2[1]) - half,
                      max(ln.p1[0], ln.p2[0]) + half, max(ln.p1[1], ln.p2[1]) + half))
    for item in (*page.images, *page.texts):
        left, top, right, bottom = item.rect
        boxes.append((min(left, right), min(top, bottom), max(left, right), max(top, bottom)))
    if not boxes:
        return
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    dx, dy = UNBOUNDED_MARGIN - left, UNBOUNDED_MARGIN - top
    page.width = right - left + 2 * UNBOUNDED_MARGIN
    page.height = bottom - top + 2 * UNBOUNDED_MARGIN
    for s in page.strokes:
        s.points = [(x + dx, y + dy, p) for x, y, p in s.points]
    for ln in page.lines:
        ln.p1 = (ln.p1[0] + dx, ln.p1[1] + dy)
        ln.p2 = (ln.p2[0] + dx, ln.p2[1] + dy)
    for item in (*page.images, *page.texts):
        left_, top_, right_, bottom_ = item.rect
        item.rect = (left_ + dx, top_ + dy, right_ + dx, bottom_ + dy)


# Imported last on purpose: inkblob imports Stroke from this module, which is defined by
# now, so the import cycle resolves whichever of the two modules is imported first.
from notes_cli.inkblob import decode_stroke as _decode_stroke  # noqa: E402

BLOB_DECODER = _decode_stroke
