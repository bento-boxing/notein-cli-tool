"""Builders for synthetic Notein backup entries (folder and note zips).

The zips mirror the real backup in `data/NoteInDataSync`: no file extension, named
`<title>_<id>`, with the same member names and, for notes, a SQLite database created
with the app's real `CREATE TABLE` statements, in WAL mode with `-wal`/`-shm` members.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

# Copied verbatim from a real note database (sqlite_master).
SCHEMA = [
    """CREATE TABLE `NoteContentEntity` (`id` TEXT NOT NULL, `page_list` TEXT, `page_layer_list` TEXT, `outline_list` TEXT NOT NULL, `default_page_id` TEXT NOT NULL, `pdf_info_id` TEXT, `saved_page_index` INTEGER NOT NULL, `activated_page_layer_id` TEXT NOT NULL, `zoom` REAL NOT NULL, `unbounded_page_offset_x` REAL NOT NULL, `unbounded_page_offset_y` REAL NOT NULL, `page_offset_json` TEXT NOT NULL DEFAULT '{"offset_x":0,"offset_y":0}', `unbounded_note` INTEGER NOT NULL, `creation_time` INTEGER NOT NULL, `last_modification_time` INTEGER NOT NULL, `extras` TEXT NOT NULL, PRIMARY KEY(`id`))""",
    """CREATE TABLE `PageEntity` (`id` TEXT NOT NULL, `note_id` TEXT NOT NULL, `paper_spec` TEXT, `page_orientation` TEXT NOT NULL, `paper_theme` TEXT, `padding_color` INTEGER NOT NULL, `paper_padding` TEXT, `padding_style_json` TEXT, `creation_time` INTEGER NOT NULL, `last_modification_time` INTEGER NOT NULL, `tn_path` TEXT NOT NULL, `unbounded` INTEGER NOT NULL, `source` INTEGER NOT NULL, `recover_note_id` TEXT NOT NULL, `recover_note_title` TEXT NOT NULL, `recover_index` INTEGER NOT NULL, `is_in_trash_bin` INTEGER NOT NULL, `history_page_id_list` TEXT, `rotation_direction_index` INTEGER NOT NULL, is_bookmarked INTEGER NOT NULL DEFAULT 0, bookmark_time INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(`id`))""",
    """CREATE TABLE `PdfInfoEntity` (`id` TEXT NOT NULL, `pdf_path` TEXT NOT NULL, `tn_path` TEXT NOT NULL, `password` TEXT, PRIMARY KEY(`id`))""",
    """CREATE TABLE `StrokeEntity` (`id` TEXT NOT NULL, `page_id` TEXT NOT NULL, `layer_id` TEXT NOT NULL, `creation_time` INTEGER NOT NULL, `record_json` TEXT, `ink_stroke_json` TEXT, `left` REAL, `top` REAL, `right` REAL, `bottom` REAL, ink_stroke_blob BLOB DEFAULT NULL, PRIMARY KEY(`id`))""",
    """CREATE TABLE `ImageEntity` (`id` TEXT NOT NULL, `uri` TEXT NOT NULL, `layer` INTEGER NOT NULL, `layer_id` TEXT NOT NULL, `bounds` TEXT, `rotation` REAL NOT NULL, `page_id` TEXT NOT NULL, `creation_time` INTEGER NOT NULL, `last_modification_time` INTEGER NOT NULL, `left` REAL, `top` REAL, `right` REAL, `bottom` REAL, `fixed` INTEGER NOT NULL, PRIMARY KEY(`id`))""",
    """CREATE TABLE `TextBoxEntity` (`id` TEXT NOT NULL, `page_id` TEXT NOT NULL, `text` TEXT NOT NULL, `text_size` REAL NOT NULL, `box_width` REAL NOT NULL, `box_height` REAL NOT NULL, `line_height` REAL NOT NULL, `background_color` INTEGER NOT NULL, `bounds` TEXT, `layer` INTEGER NOT NULL, `layer_id` TEXT NOT NULL, `default_text_color` INTEGER NOT NULL, `creation_time` INTEGER NOT NULL, `local_transform_json` TEXT, `left` REAL, `top` REAL, `right` REAL, `bottom` REAL, fixed INTEGER NOT NULL DEFAULT(0), metrics_version INTEGER NOT NULL DEFAULT(0), PRIMARY KEY(`id`))""",
    """CREATE TABLE `ShapeEntity` (`id` TEXT NOT NULL, `page_id` TEXT NOT NULL, `color` INTEGER NOT NULL, `width` REAL NOT NULL, `points` TEXT NOT NULL, `type` INTEGER NOT NULL, `blend_mode` INTEGER NOT NULL, `layer_id` TEXT NOT NULL, `tape_like` INTEGER NOT NULL, `style` INTEGER NOT NULL, `opacity_of_fill_color` INTEGER NOT NULL, `creation_time` INTEGER NOT NULL, `left` REAL, `top` REAL, `right` REAL, `bottom` REAL, tape_pattern INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(`id`))""",
    """CREATE TABLE `PageLayerEntity` (`id` TEXT NOT NULL, `note_id` TEXT NOT NULL, `visible` INTEGER NOT NULL, `extras` TEXT, `name` TEXT, PRIMARY KEY(`id`))""",
    """CREATE TABLE `DualLinkRefEntity` (`id` TEXT NOT NULL, `note_id` TEXT NOT NULL, `page_id` TEXT NOT NULL, `bounds` TEXT NOT NULL, `item_id` TEXT, `source_id` TEXT, `source_note_id` TEXT, `creation_time` INTEGER NOT NULL, `last_modification_time` INTEGER NOT NULL, `left` REAL, `top` REAL, `right` REAL, `bottom` REAL, PRIMARY KEY(`id`))""",
]

A4_WIDTH = 1240
A4_HEIGHT = 1754
UNBOUNDED_SIZE = 1920  # the real `无界` paper_spec is 1920 x 1920
BLACK = -16777216  # ARGB 0xFF000000 as a signed Android int
WHITE = -1  # ARGB 0xFFFFFFFF as a signed Android int
THEME_PREFIX = "com.orion.notein.domain.models.papertheme."
DOCUMENTS_DIR = "/data/user/0/com.orion.notein.global/files/documents"
IMAGES_DIR = "/data/user/0/com.orion.notein.global/files/images"
LAYERS = ["paper_theme", "default"]
LINK_REF_PAGE = "link_ref"  # ImageEntity.page_id of an image placed through DualLinkRefEntity

PAGE_KEYS = {
    "id", "width", "height", "orientation", "name", "background", "pdf_page",
    "strokes", "lines", "images", "texts", "trashed", "unbounded", "wal_only",
}
STROKE_KEYS = {"points", "color", "width", "layer", "type", "blob", "bounds"}
LINE_KEYS = {"p1", "p2", "color", "width", "layer", "type"}
IMAGE_KEYS = {"id", "rect", "rotation", "layer", "link"}
TEXT_KEYS = {"html", "rect", "size", "color", "layer"}


def _zip_name(title: str, id: str) -> str:
    # The real backup names zips `<title>_<id>`; "/" cannot appear in a file name.
    return f"{title}_{id}".replace("/", "_")


def _compact(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _check_keys(kind: str, item: dict, allowed: set[str]) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"unknown {kind} keys: {sorted(unknown)}")


def _ltrb(rect) -> dict[str, float]:
    left, top, right, bottom = rect
    return {"left": float(left), "top": float(top), "right": float(right), "bottom": float(bottom)}


def make_folder_zip(dir: Path, title: str, id: str, parent_id: str | None) -> Path:
    """Write a folder zip `<title>_<id>` like the real ones and return its path.

    Members: `folder_<title>` (JSON metadata; `parentId` omitted for a root folder, as in
    the real backup), `folder_labels.json` (`[]`) and `folder_tn_<id>` (PNG thumbnail).
    """
    meta: dict[str, object] = {
        "coverColor": -8875339,
        "creationTime": 1767002156820,
        "favorite": False,
        "id": id,
        "inTrashBin": False,
        "lastModificationTime": 1767528561000,
    }
    if parent_id is not None:
        meta["parentId"] = parent_id
    meta["title"] = title
    meta["tnPath"] = f"/storage/emulated/0/Android/data/com.orion.notein.global/files/{id}"

    path = Path(dir) / _zip_name(title, id)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"folder_{title}", _compact(meta))
        zf.writestr("folder_labels.json", "[]")
        zf.writestr(f"folder_tn_{id}", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return path


def make_note_zip(
    dir: Path,
    title: str,
    id: str,
    parent_id: str | None,
    *,
    modified=1,
    in_trash=False,
    pages=None,
    pdf: bytes | None = None,
    images: dict[str, bytes] | None = None,
    layers: dict[str, bool] | None = None,
    journal: str = "wal",
    pdf_path_in_theme: bool = True,
    extra_sql: list[str] | None = None,
) -> Path:
    """Write a note zip `<title>_<id>` like the real ones and return its path.

    Members: `note_meta.json`, the database `note_database_note_<id>_db`,
    `note_pdf_<name>.pdf` when `pdf` is given, and `note_image_<image id>.png` for each
    item of `images` (`{image id: png bytes}`). An image that a page places but that is not
    in `images` is missing from the zip, as can happen in a real backup.

    The database has the real schema. With `journal="wal"` (the default, as in every real
    note) it is in WAL mode and the zip also holds the `_db-wal` and `_db-shm` members:
    everything is checkpointed into the main file except pages marked `wal_only`, whose
    rows exist only in the `-wal` member. `journal="rollback"` writes a single database
    file and no `-wal`/`-shm` members.

    As in the real backup, `note_meta.json`'s `pdfInfo.pdfPath` is empty; the PDF's path
    is in the database (`PdfInfoEntity.pdf_path` and, unless `pdf_path_in_theme` is False,
    each PDF page's `paper_theme.pdfInfo.pdfPath`).

    `layers` sets the visibility of page layers, `{layer id: visible}`, on top of the two
    real layers `paper_theme` and `default` (both visible unless overridden). Elements name
    their layer with a `layer` key (default `"default"`); a layer id that is not in
    `layers` (the real data uses `"unspecified"`) gets no PageLayerEntity row.

    `extra_sql` is a list of SQL statements run after everything else (e.g. to add a
    table the loader does not read).

    `pages` is a list of dicts, one per page in `page_list` order. None means one blank
    A4 page; `[]` means no pages. Keys (all optional; unknown keys raise ValueError):

    - `id`: page id (default `<note id>-p<n>`, n from 1)
    - `width`, `height`: `paper_spec` size in pixels (default A4, 1240 x 1754, or
      1920 x 1920 for an unbounded page)
    - `orientation`: `"VERTICAL"` (default) or `"HORIZONTAL"`; stored in
      `page_orientation` and in the paper theme. Width/height are stored as given.
    - `name`: `paper_spec` name (default `"PDF"` when `pdf_page` is set, `"无界"` when
      `unbounded`, else `"A4"`)
    - `background`: ARGB int of the plain paper theme (default -1, white)
    - `pdf_page`: 0-based page of the imported PDF this page shows. The page then gets a
      `PdfPaperTheme` pointing at the note's PDF; the PDF is bundled only if `pdf` is given.
    - `trashed`: True stores the page with `is_in_trash_bin = 1` (it stays in `page_list`)
    - `unbounded`: True makes an unbounded (`无界`) page, as the app stores it
    - `wal_only`: True writes this page's PageEntity row and all its elements after the
      checkpoint, so they exist only in the `-wal` member (needs `journal="wal"`)
    - `strokes`: list of dicts, one StrokeEntity row each. Either a JSON stroke (stored in
      `record_json`): `points` [(x, y, pressure), ...], `color` (ARGB int, default black),
      `width` (default 2.6), `type` (default 1); or a binary stroke: `blob` (raw bytes
      stored in `ink_stroke_blob`, `record_json` NULL) with optional `bounds`
      (left, top, right, bottom) for the row's bound columns. Both take `layer`.
    - `lines`: list of dicts, one ShapeEntity row each: `p1`, `p2` (x, y), `color`
      (default black), `width` (default 2.1), `type` (default 7, a straight line), `layer`.
    - `images`: list of dicts, one ImageEntity row each: `id` (required; the zip member is
      `note_image_<id>.png` if `images` has bytes for it), `rect` (left, top, right,
      bottom; default (100, 100, 300, 200)), `rotation` (default 0.0), `layer`, and
      `link`: True stores the image as the app stores one linked in from another note:
      ImageEntity `page_id` is `"link_ref"`, layer `"unspecified"`, and a
      DualLinkRefEntity row with `item_id` = the image id places it on this page.
    - `texts`: list of dicts, one TextBoxEntity row each: `html` (the `text` column),
      `rect` (default (100, 100, 600, 150)), `size` (`text_size`, default 30.0), `color`
      (`default_text_color`, default black), `layer`.

    Every note has the two real layers, `paper_theme` and `default`, both visible unless
    `layers` says otherwise.
    """
    if journal not in ("wal", "rollback"):
        raise ValueError(f"journal must be 'wal' or 'rollback', not {journal!r}")
    pages = [{}] if pages is None else pages
    note_ts = modified
    page_ids = []
    for n, page in enumerate(pages, start=1):
        _check_keys("page", page, PAGE_KEYS)
        for kind, allowed in (("strokes", STROKE_KEYS), ("lines", LINE_KEYS),
                              ("images", IMAGE_KEYS), ("texts", TEXT_KEYS)):
            for item in page.get(kind, []):
                _check_keys(kind, item, allowed)
        if page.get("wal_only") and journal != "wal":
            raise ValueError("wal_only pages need journal='wal'")
        page_ids.append(page.get("id", f"{id}-p{n}"))

    uses_pdf = pdf is not None or any(p.get("pdf_page") is not None for p in pages)
    pdf_info_id = f"{id}-pdf" if uses_pdf else None
    # Real bundled PDFs are named `note_pdf_<title>__notein_pdf_<uuid>.pdf`.
    pdf_name = f"{title.replace('/', '_')}__notein_pdf_{id}-pdf.pdf"
    pdf_path = f"{DOCUMENTS_DIR}/{pdf_name}" if uses_pdf else ""
    layer_visibility = {layer: True for layer in LAYERS}
    layer_visibility.update(layers or {})
    unbounded_note = any(p.get("unbounded") for p in pages)

    with tempfile.TemporaryDirectory(prefix="factory-db-") as scratch:
        db_path = Path(scratch) / "note_db"
        con = sqlite3.connect(db_path)
        try:
            if journal == "wal":
                con.execute("PRAGMA journal_mode=WAL")
                # Keep the -wal file on disk until the members are read (see below).
                con.execute("PRAGMA wal_autocheckpoint=0")
            for statement in SCHEMA:
                con.execute(statement)
            con.execute(
                "INSERT INTO NoteContentEntity (id, page_list, page_layer_list, outline_list,"
                " default_page_id, pdf_info_id, saved_page_index, activated_page_layer_id, zoom,"
                " unbounded_page_offset_x, unbounded_page_offset_y, page_offset_json,"
                " unbounded_note, creation_time, last_modification_time, extras)"
                " VALUES (?, ?, ?, '[]', ?, ?, 0, 'default', 1.0, 0.0, 0.0, ?, ?, ?, ?, ?)",
                (
                    id,
                    _compact(page_ids),
                    _compact(LAYERS),
                    page_ids[0] if page_ids else "",
                    pdf_info_id,
                    '{"offset_x":0,"offset_y":0}',
                    int(unbounded_note),
                    note_ts,
                    note_ts,
                    '{"mMap":{"app_version_code":"369"}}',
                ),
            )
            for layer, visible in layer_visibility.items():
                con.execute(
                    "INSERT INTO PageLayerEntity (id, note_id, visible, extras, name)"
                    " VALUES (?, ?, ?, NULL, NULL)",
                    (layer, id, int(bool(visible))),
                )
            if uses_pdf:
                con.execute(
                    "INSERT INTO PdfInfoEntity (id, pdf_path, tn_path, password)"
                    " VALUES (?, ?, '', NULL)",
                    (pdf_info_id, pdf_path),
                )
            theme_pdf_info = {
                "id": pdf_info_id or "",
                "pdfPath": pdf_path if pdf_path_in_theme else "",
                "tnPath": "",
            }

            later = []
            for page_id, page in zip(page_ids, pages):
                if page.get("wal_only"):
                    later.append((page_id, page))
                else:
                    _insert_page(con, id, page_id, page, note_ts, theme_pdf_info)
            con.commit()
            if journal == "wal":
                # Move everything so far into the main file; the rest goes to the -wal only.
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for page_id, page in later:
                    _insert_page(con, id, page_id, page, note_ts, theme_pdf_info)
            for statement in extra_sql or []:
                con.execute(statement)
            con.commit()

            # Read the members while the connection is open: closing the last connection
            # checkpoints the WAL into the main file and deletes the -wal and -shm files,
            # whereas the real backup holds an un-checkpointed snapshot.
            db_members = {"": db_path.read_bytes()}
            if journal == "wal":
                for suffix in ("-wal", "-shm"):
                    db_members[suffix] = Path(f"{db_path}{suffix}").read_bytes()
        finally:
            con.close()

    meta: dict[str, object] = {
        "creationTime": note_ts,
        "favorite": False,
        "id": id,
        "inTrashBin": in_trash,
        "lastModificationTime": modified,
        "lastOpenedTime": modified,
        "lockAdded": False,
    }
    if parent_id is not None:
        meta["parentId"] = parent_id
    meta.update(
        {
            # As in every real note: the meta copy of the PDF path is empty.
            "pdfInfo": {"id": pdf_info_id or "", "pdfPath": "", "tnPath": ""},
            "sortKey": -1,
            "title": title,
            "tnHeight": 260,
            "tnPath": "",
            "tnWidth": 184,
            "totalPagesCount": len(pages),
            "unboundedNote": unbounded_note,
            "userId": "test-user",
            "usingOwnPdf": True,  # true on every real note, PDF or not
        }
    )

    path = Path(dir) / _zip_name(title, id)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for suffix, data in db_members.items():
            zf.writestr(f"note_database_note_{id}_db{suffix}", data)
        # The small members below hold the constant content seen in every real note.
        zf.writestr("note_extra.json", '{"OooO00o":45,"OooO0O0":35}')
        zf.writestr(
            "note_in_flag.json",
            '{"OooO00o":true,"OooO0O0":true,"OooO0OO":true,"OooO0Oo":true,"OooO0o0":true}',
        )
        zf.writestr("note_label.json", '{"note_labels":[],"page_id_to_labels_map":{}}')
        zf.writestr("note_meta.json", _compact(meta))
        zf.writestr("notepdf_lost_info.json", "")
        if pdf is not None:
            zf.writestr(f"note_pdf_{pdf_name}", pdf)
        for image_id, data in (images or {}).items():
            zf.writestr(f"note_image_{image_id}.png", data)
    return path


def _insert_page(
    con: sqlite3.Connection,
    note_id: str,
    page_id: str,
    page: dict,
    ts: int,
    pdf_info: dict,
) -> None:
    """Insert one PageEntity row and the rows of all elements on it."""
    unbounded = bool(page.get("unbounded"))
    default_size = (UNBOUNDED_SIZE, UNBOUNDED_SIZE) if unbounded else (A4_WIDTH, A4_HEIGHT)
    width = page.get("width", default_size[0])
    height = page.get("height", default_size[1])
    orientation = page.get("orientation", "VERTICAL")
    pdf_page = page.get("pdf_page")
    name = "PDF" if pdf_page is not None else "无界" if unbounded else "A4"
    spec = {
        "height": height,
        "iconHorizontal": 2131233173,
        "iconVertical": 2131233174,
        "info": "Ꝏ x Ꝏ" if unbounded else "",
        "isPercentage": False,
        "name": page.get("name", name),
        "width": width,
    }
    if pdf_page is not None:
        theme = {
            "type": THEME_PREFIX + "PdfPaperTheme",
            "name": "",
            "orientation": orientation,
            "pageNum": pdf_page,
            "pdfInfo": pdf_info,
        }
    else:
        theme = {
            "type": THEME_PREFIX + "LambdaPaperTheme",
            "baseTheme": {
                "type": THEME_PREFIX + "ColorPaperTheme",
                "color": page.get("background", WHITE),
            },
            "category": {"id": 4097, "name": "基本"},
            "name": "",
            "orientation": orientation,
            "paperStyle": {
                "type": THEME_PREFIX + "paperstyle.BlankPaperStyle",
                "paperHeight": float(height),
                "paperWidth": float(width),
            },
        }
    con.execute(
        "INSERT INTO PageEntity (id, note_id, paper_spec, page_orientation, paper_theme,"
        " padding_color, paper_padding, padding_style_json, creation_time,"
        " last_modification_time, tn_path, unbounded, source, recover_note_id,"
        " recover_note_title, recover_index, is_in_trash_bin, history_page_id_list,"
        " rotation_direction_index)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?, ?, '', ?, 0, '', '', -1, ?, NULL, 0)",
        (
            page_id,
            note_id,
            _compact(spec),
            orientation,
            _compact(theme),
            '{"bottom":0,"left":0,"right":0,"top":0}',
            ts,
            ts,
            int(unbounded),
            int(bool(page.get("trashed"))),
        ),
    )

    order = 0  # creation_time, so that rows keep the order they were given in

    def next_ts() -> int:
        nonlocal order
        order += 1
        return ts + order

    for n, stroke in enumerate(page.get("strokes", []), start=1):
        stroke_id = f"{page_id}-s{n}"
        layer = stroke.get("layer", "default")
        created = next_ts()
        if "blob" in stroke:
            bounds = stroke.get("bounds", (None, None, None, None))
            con.execute(
                "INSERT INTO StrokeEntity (id, page_id, layer_id, creation_time, record_json,"
                " ink_stroke_json, left, top, right, bottom, ink_stroke_blob)"
                " VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)",
                (stroke_id, page_id, layer, created, *bounds, stroke["blob"]),
            )
            continue
        points = [(float(x), float(y), float(p)) for x, y, p in stroke["points"]]
        width = stroke.get("width", 2.6)
        xs = [p[0] for p in points] or [0.0]
        ys = [p[1] for p in points] or [0.0]
        bounds = (min(xs) - width, min(ys) - width, max(xs) + width, max(ys) + width)
        record = {
            "id": stroke_id,
            "type": stroke.get("type", 1),
            "color": stroke.get("color", BLACK),
            "width": width,
            "points": [
                {"x": x, "y": y, "p": p, "action": 0 if i == 0 else 2}
                for i, (x, y, p) in enumerate(points)
            ],
            "bounds": _ltrb(bounds),
            "layerId": layer,
            "creationTime": created,
        }
        con.execute(
            "INSERT INTO StrokeEntity (id, page_id, layer_id, creation_time, record_json,"
            " ink_stroke_json, left, top, right, bottom, ink_stroke_blob)"
            " VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL)",
            (stroke_id, page_id, layer, created, _compact(record), *bounds),
        )

    for n, line in enumerate(page.get("lines", []), start=1):
        (x1, y1), (x2, y2) = line["p1"], line["p2"]
        width = line.get("width", 2.1)
        con.execute(
            "INSERT INTO ShapeEntity (id, page_id, color, width, points, type, blend_mode,"
            " layer_id, tape_like, style, opacity_of_fill_color, creation_time, left, top,"
            " right, bottom) VALUES (?, ?, ?, ?, ?, ?, -1, ?, 0, 0, 0, ?, ?, ?, ?, ?)",
            (
                f"{page_id}-l{n}",
                page_id,
                line.get("color", BLACK),
                width,
                _compact([{"x": float(x1), "y": float(y1)}, {"x": float(x2), "y": float(y2)}]),
                line.get("type", 7),
                line.get("layer", "default"),
                next_ts(),
                min(x1, x2) - width / 2,
                min(y1, y2) - width / 2,
                max(x1, x2) + width / 2,
                max(y1, y2) + width / 2,
            ),
        )

    for image in page.get("images", []):
        image_id = image["id"]
        rect = image.get("rect", (100, 100, 300, 200))
        link = bool(image.get("link"))
        created = next_ts()
        con.execute(
            "INSERT INTO ImageEntity (id, uri, layer, layer_id, bounds, rotation, page_id,"
            " creation_time, last_modification_time, left, top, right, bottom, fixed)"
            " VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                image_id,
                f"{IMAGES_DIR}/{note_id}/{image_id}.png",
                image.get("layer", "unspecified" if link else "default"),
                _compact(_ltrb(rect)),
                float(image.get("rotation", 0.0)),
                LINK_REF_PAGE if link else page_id,
                created,
                created,
                *map(float, rect),
            ),
        )
        if link:
            left, top, right, bottom = map(float, rect)
            con.execute(
                "INSERT INTO DualLinkRefEntity (id, note_id, page_id, bounds, item_id,"
                " source_id, source_note_id, creation_time, last_modification_time, left,"
                " top, right, bottom) VALUES (?, ?, ?, ?, ?, 'link-source', 'other-note',"
                " ?, ?, ?, ?, ?, ?)",
                (
                    f"{image_id}-ref",
                    note_id,
                    page_id,
                    _compact(_ltrb(rect)),
                    image_id,
                    created,
                    created,
                    left - 42,  # the real rows widen the bound columns for the link icon
                    top,
                    right,
                    bottom,
                ),
            )

    for n, text in enumerate(page.get("texts", []), start=1):
        rect = text.get("rect", (100, 100, 600, 150))
        left, top, right, bottom = map(float, rect)
        con.execute(
            "INSERT INTO TextBoxEntity (id, page_id, text, text_size, box_width, box_height,"
            " line_height, background_color, bounds, layer, layer_id, default_text_color,"
            " creation_time, local_transform_json, left, top, right, bottom)"
            " VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"{page_id}-t{n}",
                page_id,
                text["html"],
                float(text.get("size", 30.0)),
                right - left,
                bottom - top,
                _compact(_ltrb(rect)),
                text.get("layer", "default"),
                text.get("color", BLACK),
                next_ts(),
                '"1.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,1.0"',
                left,
                top,
                right,
                bottom,
            ),
        )
