import json
import math
import random
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import pytest

from notes_cli import inkblob, notedb
from notes_cli.backup import scan
from notes_cli.inkblob import decode_stroke, read_fields
from notes_cli.notedb import Stroke, load_note

from factory import make_note_zip

BLACK = -16777216  # ARGB 0xFF000000 as a signed Android int
IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)


# -- protobuf wire reader ------------------------------------------------------------------


def test_read_fields_varint_fixed32_bytes():
    # field 1 varint 150 | field 2 fixed32 1.0f | field 3 length-delimited "abc"
    blob = bytes.fromhex("089601" "150000803f" "1a03616263")

    assert read_fields(blob) == [(1, 0, 150), (2, 5, b"\x00\x00\x80\x3f"), (3, 2, b"abc")]


def test_read_fields_fixed64_and_wide_varint():
    # field 4 fixed64; field 5 a 10-byte varint (a 64-bit value with the top bit set, as
    # real colour values are); field 6 an empty length-delimited value.
    blob = (bytes.fromhex("21") + bytes(range(8))
            + bytes.fromhex("28") + bytes([0x80] * 8 + [0xFF, 0x01])
            + bytes.fromhex("3200"))

    assert read_fields(blob) == [(4, 1, bytes(range(8))), (5, 0, 0xFF000000 << 32), (6, 2, b"")]
    assert read_fields(b"") == []


@pytest.mark.parametrize("blob", [
    bytes.fromhex("0896"),           # varint cut in the middle
    bytes.fromhex("15000080"),       # fixed32 one byte short
    bytes.fromhex("19000000"),       # fixed64 short
    bytes.fromhex("1a036162"),       # length-delimited value shorter than its length
    bytes.fromhex("1a"),             # length missing
    bytes.fromhex("80"),             # key cut in the middle
])
def test_truncated_raises(blob):
    with pytest.raises(ValueError):
        read_fields(blob)


@pytest.mark.parametrize("blob", [
    bytes.fromhex("0b"),                          # wire type 3 (group start): not supported
    bytes.fromhex("0c"),                          # wire type 4 (group end)
    bytes.fromhex("0e00"),                        # wire type 6 does not exist
    bytes.fromhex("0700"),                        # wire type 7 does not exist
    bytes.fromhex("0001"),                        # field number 0
    bytes.fromhex("08") + b"\xff" * 10 + b"\x01",  # varint longer than 10 bytes
    bytes.fromhex("08") + b"\xff" * 9 + b"\x7f",   # 10-byte varint over 64 bits
    bytes.fromhex("1a") + b"\xff" * 9 + b"\x01",   # declared length 2**64 - 1: no allocation
])
def test_malformed_wire_data_raises(blob):
    with pytest.raises(ValueError):
        read_fields(blob)


# -- a test-local encoder following docs/inkblob-format.md -----------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        low, value = value & 0x7F, value >> 7
        if not value:
            out.append(low)
            return bytes(out)
        out.append(low | 0x80)


def _record(number: int, wire: int, value) -> bytes:
    key = _varint(number << 3 | wire)
    if wire == 0:
        return key + _varint(value)
    if wire == 5:
        return key + struct.pack("<f", value)
    return key + _varint(len(value)) + value


def _blob(points, *, color=BLACK, width=2.5, m12=IDENTITY, m13=IDENTITY, drop=(), replace=None):
    """A blob shaped like a real one (fields 1-14 in the real order). `points` are
    (x, y, pressure); `color` is an ARGB int, stored as an sRGB ColorLong. `drop` leaves
    fields out; `replace` maps a field number to the raw record(s) stored instead."""
    xy = struct.pack(f"<{2 * len(points)}f", *(v for x, y, _ in points for v in (x, y)))
    extra = struct.pack(f"<{5 * len(points)}f", *(
        v for n, (_, _, p) in enumerate(points) for v in (8.0 * n, p, 0.4, 0.6, 0.01347)))
    records = {
        1: _record(1, 2, b"0b3c8e2a-stroke"),
        2: _record(2, 2, b"default"),
        3: _record(3, 0, 1767002156820),
        4: _record(4, 5, width),
        5: _record(5, 0, (color & 0xFFFFFFFF) << 32),
        6: _record(6, 5, 0.02),
        7: _record(7, 2, b"notein-ballpoint-v1"),
        8: _record(8, 0, 1),
        9: _record(9, 5, 0.01347),
        10: _record(10, 2, xy),
        11: _record(11, 2, extra),
        12: _record(12, 2, struct.pack("<9f", *m12)),
        13: _record(13, 2, struct.pack("<9f", *m13)),
        14: _record(14, 2, bytes(16)),
    }
    records.update(replace or {})
    return b"".join(rec for number, rec in records.items() if number not in drop)


# Values exact in float32, so the round trip is exact.
POINTS = [(10.5, 20.25, 0.5), (30.0, 40.125, 0.25), (-3.5, 1754.0, 0.75)]


# -- decode_stroke ---------------------------------------------------------------------------


def test_decode_synthetic_roundtrip():
    stroke = decode_stroke(_blob(POINTS, color=BLACK, width=2.5))

    assert stroke == Stroke(points=POINTS, color=BLACK, width=2.5)


def test_colour_is_signed_argb_like_json_strokes():
    # sRGB ColorLong (ARGB << 32), as in every real blob; stored signed like record_json.
    assert decode_stroke(_blob(POINTS, color=0x806300FF)).color == 0x806300FF - (1 << 32)
    assert decode_stroke(_blob(POINTS, color=0x00FFFFFF)).color == 0x00FFFFFF
    # A value that fits in 32 bits is taken as a plain ARGB int.
    plain = _blob(POINTS, replace={5: _record(5, 0, 0xFF00FFA5)})
    assert decode_stroke(plain).color == 0xFF00FFA5 - (1 << 32)


def test_transform_applied():
    m12 = (2, 0, 0, 0, 2, 0, 0, 0, 1)        # scale 2
    m13 = (0.5, 0, 0, 0, 0.5, -5, 0, 0, 1)   # scale 0.5, then y - 5
    stroke = decode_stroke(_blob([(10, 20, 1.0)], m12=m12, m13=m13))

    assert stroke.points == [(10.0, 15.0, 1.0)]
    assert stroke.width == 2.5  # combined scale 1


def test_transform_order_is_m13_after_m12():
    m12 = (1, 0, 100, 0, 1, 0, 0, 0, 1)      # x + 100 first
    m13 = (2, 0, 0, 0, 2, 0, 0, 0, 1)        # then scale 2
    stroke = decode_stroke(_blob([(10, 20, 1.0)], m12=m12, m13=m13))

    assert stroke.points == [(220.0, 40.0, 1.0)]  # M12 then M13 would give (120, 40)


def test_full_matrix_rotation_and_projective_division():
    rotate = (0, -1, 0, 1, 0, 0, 0, 0, 1)    # 90 degrees: (x, y) -> (-y, x)
    assert decode_stroke(_blob([(10, 20, 1.0)], m12=rotate)).points == [(-20.0, 10.0, 1.0)]

    halve = (1, 0, 0, 0, 1, 0, 0, 0, 2)      # homogeneous W = 2
    stroke = decode_stroke(_blob([(10, 20, 1.0)], m13=halve))
    assert stroke.points == [(5.0, 10.0, 1.0)]
    assert stroke.width == 1.25

    perspective = (1, 0, 0, 0, 1, 0, 0.1, 0, 1)   # W = 0.1 x + 1
    (x, y, _), = decode_stroke(_blob([(10, 20, 1.0)], m12=perspective)).points
    assert (x, y) == pytest.approx((5.0, 10.0))


def test_width_scales_with_transform():
    # The real data shows the width is in the stroke's untransformed units (see the doc).
    m12 = (1.4129032, 0, 0, 0, 1.4129032, 0, 0, 0, 1)
    m13 = (0.5730, 0, 0, 0, 0.5730, 812.5, 0, 0, 1)
    stroke = decode_stroke(_blob(POINTS, width=2.6227462, m12=m12, m13=m13))

    s = struct.unpack("<f", struct.pack("<f", 1.4129032))[0] * struct.unpack("<f", struct.pack("<f", 0.5730))[0]
    assert stroke.width == pytest.approx(2.6227462 * s, rel=1e-6)
    assert stroke.points[0][:2] == pytest.approx((10.5 * s, 20.25 * s + 812.5), rel=1e-6)


def test_missing_matrices_are_identity():
    for drop in ((12,), (13,), (12, 13)):
        assert decode_stroke(_blob(POINTS, drop=drop)) == Stroke(POINTS, BLACK, 2.5)
    only_m13 = _blob([(10, 20, 1.0)], m13=(0.5, 0, 0, 0, 0.5, -5, 0, 0, 1), drop=(12,))
    assert decode_stroke(only_m13).points == [(5.0, 5.0, 1.0)]


def test_pressure_is_second_value_of_field_11():
    assert [p for _, _, p in decode_stroke(_blob(POINTS)).points] == [0.5, 0.25, 0.75]


@pytest.mark.parametrize("field_11", [
    None,
    struct.pack("<10f", *range(10)),                             # 5 floats for 2 points, not 3
    struct.pack("<15f", *([0.5] * 6 + [math.nan] + [0.5] * 8)),  # point 2's pressure is NaN
    b"\x00\x00\x80",                                             # not even whole floats
], ids=["missing", "two-points-of-data", "nan-pressure", "partial-float"])
def test_unusable_pressure_defaults_to_one_and_keeps_the_ink(field_11):
    replace = {} if field_11 is None else {11: _record(11, 2, field_11)}
    drop = (11,) if field_11 is None else ()
    stroke = decode_stroke(_blob(POINTS, drop=drop, replace=replace))

    assert stroke.points == [(x, y, 1.0) for x, y, _ in POINTS]


def test_packed_fields_split_over_records_are_concatenated():
    xy = struct.pack("<6f", *(v for x, y, _ in POINTS for v in (x, y)))
    split = _record(10, 2, xy[:8]) + _record(10, 2, xy[8:])

    assert decode_stroke(_blob(POINTS, replace={10: split})).points == POINTS


def test_unknown_fields_ignored_and_empty_points_allowed():
    blob = _blob(POINTS, replace={14: _record(99, 0, 7) + _record(15, 2, b"future")})
    assert decode_stroke(blob).points == POINTS

    # Present but empty: a stroke with no points (the loader reports it as no ink).
    assert decode_stroke(_blob([])).points == []


@pytest.mark.parametrize("blob", [
    _blob(POINTS, drop=(10,)),                                       # no field 10
    _blob(POINTS, replace={10: _record(10, 2, struct.pack("<3f", 1, 2, 3))}),  # odd floats
    _blob(POINTS, replace={10: _record(10, 2, b"\x00" * 7)}),        # not whole floats
    _blob(POINTS, replace={10: _record(10, 0, 5)}),                  # wrong wire type
    _blob(POINTS, replace={10: _record(10, 2, struct.pack("<2f", math.nan, 1))}),
    _blob(POINTS, replace={10: _record(10, 2, struct.pack("<2f", 1, math.inf))}),
    _blob(POINTS, drop=(4,)),                                        # no width
    _blob(POINTS, drop=(5,)),                                        # no colour
    _blob(POINTS, width=-1.0),
    _blob(POINTS, width=math.nan),
    _blob(POINTS, replace={4: _record(4, 0, 3)}),                    # width as a varint
    _blob(POINTS, replace={5: _record(5, 0, (0xFF000000 << 32) | 7)}),  # colour space 7
    _blob(POINTS, replace={12: _record(12, 2, struct.pack("<8f", *IDENTITY[:8]))}),
    _blob(POINTS, m13=(1, 0, 0, 0, 1, 0, 0, 0, math.inf)),           # non-finite matrix
    _blob(POINTS, m13=(1, 0, 0, 0, 1, 0, 0, 0, 0)),                  # W = 0 everywhere
    _blob([(0, 5, 1.0)], m13=(1, 0, 0, 0, 1, 0, 1, 0, 0)),           # W = x, 0 at x = 0
    _blob(POINTS)[:-5],                                              # truncated
    b"\x0a\x01a",
    b"",
])
def test_malformed_blob_raises(blob):
    with pytest.raises(ValueError):
        decode_stroke(blob)


def test_fuzzed_blobs_raise_only_value_error():
    """Truncations, byte flips, insertions and deletions of a real-shaped blob either
    decode to finite points or raise ValueError: never another exception, never a hang."""
    rng = random.Random(20261114)
    points = [(100 + 7.25 * n, 300 - 3.5 * n, n / 20) for n in range(20)]
    base = _blob(points, color=0xFF6300FF, width=2.6227462,
                 m12=(1.4129032, 0, 0, 0, 1.4129032, 0, 0, 0, 1),
                 m13=(0.7077625, 0, 0, 0, 0.7077625, -1234.5, 0, 0, 1))
    cases = [base[:n] for n in range(len(base))]
    for _ in range(3000):
        data = bytearray(base)
        for _ in range(rng.randint(1, 3)):
            pos = rng.randrange(len(data))
            action = rng.random()
            if action < 0.7:
                data[pos] = rng.randrange(256)
            elif action < 0.85:
                data.insert(pos, rng.randrange(256))
            else:
                del data[pos]
        cases.append(bytes(data))
    cases += [rng.randbytes(rng.randrange(1, 64)) for _ in range(500)]

    decoded = rejected = 0
    for data in cases:
        try:
            stroke = decode_stroke(data)
        except ValueError:
            rejected += 1
            continue
        decoded += 1
        assert all(math.isfinite(v) for point in stroke.points for v in point[:2])
        assert math.isfinite(stroke.width) and stroke.width >= 0
        assert -(1 << 31) <= stroke.color < 1 << 31
    assert decoded > 1000 and rejected > 500  # both paths exercised


# -- wiring into the note loader -----------------------------------------------------------


def test_loader_decodes_blobs_by_default(tmp_path):
    assert notedb.BLOB_DECODER is decode_stroke
    blob = _blob(POINTS, color=0xFF00FFA5)
    note = load_note(make_note_zip(tmp_path, "W1", "n-1", None, pages=[
        {"strokes": [{"blob": blob, "bounds": (-5, 18, 32, 1756)}, {"points": [(1, 2, 0.5)]}]},
    ]))

    assert note.pages[0].strokes == [
        Stroke(POINTS, 0xFF00FFA5 - (1 << 32), 2.5),
        Stroke([(1.0, 2.0, 0.5)], BLACK, 2.6),
    ]
    assert note.warnings == []


def test_undecodable_blob_is_one_skipped_stroke_with_warning(tmp_path):
    note = load_note(make_note_zip(tmp_path, "W1", "n-1", None, pages=[
        {"strokes": [{"blob": _blob(POINTS)}, {"blob": _blob(POINTS)[:-3]}]},
    ]))

    assert len(note.pages[0].strokes) == 1
    assert note.warnings == ["1 binary ink stroke(s) could not be decoded; skipped"]


@pytest.mark.parametrize("first", ["notes_cli.inkblob", "notes_cli.notedb"])
def test_import_order_does_not_matter(first):
    # inkblob imports Stroke from notedb and notedb imports decode_stroke from inkblob.
    code = (f"import {first}\n"
            "from notes_cli import inkblob, notedb\n"
            "assert notedb.BLOB_DECODER is inkblob.decode_stroke\n"
            "assert inkblob.Stroke is notedb.Stroke\n")
    src = Path(inkblob.__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", code], cwd=src, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# -- real backup ---------------------------------------------------------------------------


@contextmanager
def _note_db(zip_path: Path, scratch: Path):
    """The note zip's database, from a temp copy opened read-only (as notedb does)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        (member,) = [n for n in names if n.startswith("note_database_note_") and n.endswith("_db")]
        with tempfile.TemporaryDirectory(dir=scratch) as d:
            path = Path(d) / "note.db"
            for suffix in ("", "-wal", "-shm"):
                if member + suffix in names:
                    Path(f"{path}{suffix}").write_bytes(zf.read(member + suffix))
            con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                yield con
            finally:
                con.close()


@pytest.mark.realdata
def test_decoded_bounds_match_row_bounds(real_sync_dir, tmp_path):
    # Every blob stroke in every note: the decoded points' bounding box is within
    # 1.5 x width + 2 px of the row's left/top/right/bottom on each side. 100% must pass.
    # Also on every stroke, since no other real-data check can see them: the colour is
    # one of the app's opaque pen colours (reading the wrong half of field 5 gives
    # transparent black), and the row box is the decoded box grown by half the scaled
    # width (so the width scaling is right).
    checked = 0
    failures = []
    colours = Counter()
    worst_margin = 0.0
    for entry in scan(real_sync_dir).notes:
        with _note_db(entry.zip_path, tmp_path) as con:
            rows = con.execute("SELECT id, ink_stroke_blob, left, top, right, bottom"
                               " FROM StrokeEntity WHERE ink_stroke_blob IS NOT NULL").fetchall()
        for stroke_id, blob, *row in rows:
            checked += 1
            try:
                stroke = decode_stroke(blob)
                xs = [p[0] for p in stroke.points]
                ys = [p[1] for p in stroke.points]
                box = (min(xs), min(ys), max(xs), max(ys))
                deviation = max(abs(a - b) for a, b in zip(box, row))
                colours[stroke.color & 0xFFFFFFFF] += 1
                margin = max(box[0] - row[0], box[1] - row[1], row[2] - box[2], row[3] - box[3])
                worst_margin = max(worst_margin, margin - stroke.width / 2)
            except (ValueError, TypeError) as e:  # TypeError: a NULL bound column
                failures.append((entry.title, stroke_id, repr(e)))
                continue
            if not deviation <= 1.5 * stroke.width + 2.0:
                failures.append((entry.title, stroke_id, box, tuple(row), stroke.width))

    assert checked > 390_000
    assert failures == [], f"{len(failures)} of {checked} blob strokes off: {failures[:10]}"
    assert all(colour >> 24 == 0xFF for colour in colours), [hex(c) for c in colours]
    assert set(colours) <= PEN_COLOURS, [hex(c) for c in colours]
    assert colours[0xFF000000] > 300_000  # mostly black ink
    assert worst_margin <= 0.05, worst_margin


def _expected_strokes_per_page(con, note_id: str) -> list[int]:
    """StrokeEntity rows per page, in the order the loader returns pages."""
    (page_list,) = con.execute("SELECT page_list FROM NoteContentEntity WHERE id = ?",
                               (note_id,)).fetchone()
    trashed = dict(con.execute("SELECT id, is_in_trash_bin FROM PageEntity"))
    counts = Counter(page for (page,) in con.execute("SELECT page_id FROM StrokeEntity"))
    return [counts[p] for p in json.loads(page_list) if p in trashed and not trashed[p]]


# The opaque ARGB pen colours found in the backup (the first four also occur verbatim in
# the record_json strokes).
PEN_COLOURS = {0xFF000000, 0xFF00FFA5, 0xFF6300FF, 0xFF845152, 0xFF44D7B6}


@pytest.mark.realdata
def test_blob_only_modules_fully_decoded(real_sync_dir, real_facts, tmp_path):
    # Every note under the listed folders (modules written entirely with binary strokes).
    backup = scan(real_sync_dir)
    folders = {tuple(f) for f in real_facts["fully_decoded_folders"]}
    notes = [n for n in backup.notes
             if any(backup.folder_path(n)[:len(f)] == f for f in folders)]
    assert {f for f in folders
            if any(backup.folder_path(n)[:len(f)] == f for n in notes)} == folders

    total = 0
    for entry in notes:
        note = load_note(entry.zip_path)
        with _note_db(entry.zip_path, tmp_path) as con:
            expected = _expected_strokes_per_page(con, entry.id)
        assert note.warnings == [], entry.title
        assert [len(p.strokes) for p in note.pages] == expected, entry.title
        total += sum(expected)
    assert total > real_facts["fully_decoded_min_strokes"]


@pytest.mark.realdata
def test_unbounded_blob_note_fits_its_ink(real_sync_dir, real_facts):
    # A note with one unbounded page of blob strokes whose row bounds reach far beyond the
    # nominal page: it is sized to its decoded ink plus a 40 px margin.
    fact = real_facts["unbounded_note"]
    backup = scan(real_sync_dir)
    (entry,) = [n for n in backup.notes if n.title.strip() == fact["title"]]
    note = load_note(entry.zip_path)

    (page,) = note.pages
    assert len(page.strokes) == fact["strokes"]
    assert (page.width, page.height) == (pytest.approx(fact["width"], abs=1),
                                         pytest.approx(fact["height"], abs=1))
    assert note.warnings == []


@pytest.mark.realdata
def test_matches_json_twin_when_available(real_data_root, tmp_path):
    # Ground truth: a stroke stored as ink_stroke_blob in NoteInDataSync and, under the same
    # id, as record_json in an older export in data/Notein/*/*.in.
    blobs = {}
    for entry in scan(real_data_root / "NoteInDataSync").notes:
        with _note_db(entry.zip_path, tmp_path) as con:
            blobs.update(con.execute("SELECT id, ink_stroke_blob FROM StrokeEntity"
                                     " WHERE ink_stroke_blob IS NOT NULL"))
    exports = sorted(real_data_root.glob("Notein/*/*.in"))
    twins = {}
    json_strokes = 0
    for path in exports:
        with _note_db(path, tmp_path) as con:
            for stroke_id, record in con.execute(
                    "SELECT id, record_json FROM StrokeEntity WHERE record_json IS NOT NULL"):
                json_strokes += 1
                if stroke_id in blobs:
                    twins[stroke_id] = record
    if not twins:
        pytest.skip(f"no JSON twins: none of {len(blobs)} blob stroke ids in NoteInDataSync is"
                    f" stored as record_json in the {len(exports)} data/Notein exports"
                    f" ({json_strokes} JSON strokes)")

    for stroke_id, record in twins.items():
        data = json.loads(record)
        stroke = decode_stroke(blobs[stroke_id])
        assert len(stroke.points) == len(data["points"]), stroke_id
        for (x, y, _), point in zip(stroke.points, data["points"]):
            assert (x, y) == (pytest.approx(point["x"], abs=0.01),
                              pytest.approx(point["y"], abs=0.01)), stroke_id
        assert stroke.color == data["color"], stroke_id
        assert stroke.width == pytest.approx(data["width"], abs=0.01), stroke_id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width": 1e30},                                   # absurd width
        {"m13": (3e8, 0, 0, 0, 3e8, 0, 0, 0, 1)},          # absurd scale: far-off points
        {"m13": (1, 0, 5e6, 0, 1, 0, 0, 0, 1)},            # translated far off any page
    ],
)
def test_finite_but_absurd_stroke_raises(kwargs):
    # A damaged blob can decode to finite nonsense; one such stroke would make an
    # unbounded page enormous, so it is rejected like any other malformed blob.
    with pytest.raises(ValueError):
        decode_stroke(_blob([(10, 20, 1.0), (30, 40, 1.0)], **kwargs))
