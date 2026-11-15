# Notein binary ink strokes (`ink_stroke_blob`)

Notein (app version from about 381 on) stores a stroke in `StrokeEntity.ink_stroke_blob`
instead of `StrokeEntity.record_json`. A row never has both. In the backup surveyed on
2026-11-14 (`data/NoteInDataSync`, 423 notes) there are 391,918 blob strokes in 198
notes (14,347,983 points), including every note of the most recent modules.
`src/notes_cli/inkblob.py` decodes them. This document is the format it implements and
the evidence for it.

## Wire format

The blob is one uncompressed [protobuf](https://protobuf.dev/programming-guides/encoding/)
message: a sequence of `key, value` records, where `key = field_number << 3 | wire_type`
is a base-128 varint. Wire types used: 0 (varint), 2 (length-delimited: varint length,
then that many bytes) and 5 (fixed32, 4 bytes little-endian). No nested messages, no
compression. `read_fields` reads this generically (also wire type 1, fixed64), without a
schema.

## Fields

Every real blob has fields 1–13, once each, in this order; 160,795 also end with field 14.

| # | Wire | Meaning | Real data |
|---|------|---------|-----------|
| 1 | bytes | stroke id (UTF-8) | equals `StrokeEntity.id` in all 391,918 |
| 2 | bytes | layer id (UTF-8) | equals `StrokeEntity.layer_id` in all |
| 3 | varint | creation time (ms) | equals `StrokeEntity.creation_time` in all |
| 4 | fixed32 float | stroke width, in the stroke's own (untransformed) units | always 2.6227 |
| 5 | varint | colour as an Android `ColorLong` (see below) | 5 colours |
| 6 | fixed32 float | unknown | always 0.02 |
| 7 | bytes | brush name | always `notein-ballpoint-v1` |
| 8 | varint | unknown | always 1 |
| 9 | fixed32 float | unknown | 0.00750–0.0215 (mostly 0.013470); equals the 5th value of every point in field 11 |
| 10 | bytes | packed little-endian float32 `x, y` pairs, raw (untransformed) | 1 or more points |
| 11 | bytes | packed float32, 5 per point (see below) | exactly 5 × the point count of field 10 in every blob |
| 12 | bytes | 3 × 3 float32 matrix `M12`, row-major (36 bytes) | a uniform scale, 0.8–2.26 (mostly 1.4129), no translation |
| 13 | bytes | 3 × 3 float32 matrix `M13`, row-major (36 bytes) | a uniform scale, 0.44–1.27 (mostly 0.7078), plus a translation; `M13 · M12` scales by 0.81–1.00 |
| 14 | bytes | 16 bytes, unknown | present in 160,795 blobs |

## Decoding

**Position.** A raw point `(x, y)` from field 10 is placed on the page at

```
(X, Y, W) = M13 · M12 · (x, y, 1),   page point = (X / W, Y / W)
```

with page coordinates in `paper_spec` pixels (150 dpi), like JSON strokes. In real data
the bottom row of both matrices is `(0, 0, 1)` (so `W = 1`) and there is no rotation or
skew: `M12` is a scale near 1.4129 with no translation, and `M13` is a scale near 0.7078
with a translation (x mostly 0, y anywhere; the page scrolled while writing). The decoder
still applies the full matrix, dividing by `W` when the bottom row is not `(0, 0, 1)`.
The combined scale `s = sqrt(|det|)` of the linear part of `M13 · M12` is 1.0 for almost
every stroke, 0.81 at the lowest (4 strokes below 0.95, in four notes).

**Width.** `Stroke.width = field 4 × s`. The width is in the raw, untransformed units and
scales with the points. Evidence: the row's `left/top/right/bottom` is the decoded
bounding box grown by half the drawn width, and on the strokes whose `s` differs from 1
the largest margin is `s × width / 2`, not `width / 2` (margin / (width / 2) = 0.810 at
s = 0.81, 0.880 at 0.88, 0.918 at 0.92, 0.916 at 0.93, 0.959 at 0.96, 0.981–0.983 at
0.98, and at most 1.000 at s = 1.00). Decoded widths are 2.62 (2.12 at the lowest
scale), which is the most common JSON stroke width (2.62 on 281,754 of 439,638 JSON
strokes; the other common one is 3.67), so the two encodings render alike.

**Colour.** Field 5 is an Android `ColorLong`. For the sRGB colour space (id 0 in the
low 6 bits) it is the 32-bit ARGB colour shifted left by 32 bits, so its low 32 bits
are zero. In real data the low 32 bits are 0 in all 391,918 blobs, and the high 32 bits
give exactly the JSON palette: `0xFF000000` (305,282 strokes), `0xFF00FFA5` (48,765),
`0xFF6300FF` (33,935), `0xFF845152` (3,892), `0xFF44D7B6` (44). (The survey note in the
build plan said "low 32 bits"; that reading makes every stroke transparent black.) The
decoder:

- value below 2³²: taken as a plain ARGB int;
- otherwise colour space id (`value & 0x3F`) 0: ARGB = `value >> 32`;
- any other colour space: `ValueError` (never seen).

`Stroke.color` is then stored as a **signed** 32-bit int (`0xFF000000` → -16777216),
the same convention as `record_json` colours, so a note that mixes both encodings is
uniform; consumers mask with `0xFFFFFFFF`.

**Pressure (field 11).** Five float32 per point:

| Column | Range in real data | Reading |
|--------|-------------------|---------|
| 0 | 0 at the first point, never decreasing, up to 9255 | elapsed time in ms |
| 1 | 0.00024–0.933, mean 0.234 | **pen pressure**: every value is a multiple of 1/4095 (a 4096-level pen), like JSON `p` (all but 6 of 11,154,900 JSON points), and has a similar spread (JSON mean 0.167) |
| 2 | 0–1.26, mostly 0.3–0.6 | unknown (tilt or orientation?) |
| 3 | 0–6.25, mostly 0.4–1.0 | unknown (not bounded by 1, so not a pressure) |
| 4 | = field 9 | unknown |

The decoder puts column 1 in the pressure slot of `Stroke.points` (`(x, y, pressure)`).
Pressure is not rendered. If field 11 is missing, does not hold exactly 5 floats per point
of field 10, or contains a non-finite value, every point gets pressure 1.0 (the same
default as a JSON point without `p`). The ink is never dropped for a pressure problem.
This does not happen in real data.

## Errors

`decode_stroke` raises `ValueError` (and nothing else) when:

- the wire data is truncated or malformed (see `read_fields`);
- field 10 is missing, or its length is not a whole number of `x, y` float32 pairs;
- field 4 (width) or 5 (colour) is missing, the width is negative or not finite, or the
  colour is in a colour space other than sRGB;
- a known field has an unexpected wire type, or field 12/13 is not 36 bytes;
- a decoded coordinate is not finite, or the matrix maps a point to `W = 0`.

A missing field 12 or 13 is the identity matrix. Fields 10 and 11 may be split over
several records, which are concatenated, as protobuf does for packed repeated fields.
For any other field, the last record wins. Unknown fields are ignored. A present but empty
field 10 gives a stroke with no points, which the loader reports as a row with no ink.

The note loader (`notedb.load_note`) counts a blob that raises as one skipped stroke and
adds the warning `N binary ink stroke(s) could not be decoded; skipped` to the note.

## Verification

- **Row bounds.** `tests/test_inkblob.py::test_decoded_bounds_match_row_bounds` decodes
  every blob in the real backup and checks that the bounding box of the decoded points
  is within `1.5 × width + 2 px` of the row's `left/top/right/bottom` on every side.
  All 391,918 pass. The deviation is 0.71–1.31 px (median 1.31), which is at most 0.221
  of the tolerance. Grown by `width / 2`, the decoded box matches the row with a median
  error of 0.24 px (99th percentile 0.90, maximum 1.96). The row's box is usually a
  little inside the grown box, presumably because the app grows each point by its own
  pressure-dependent radius, which is at most `width / 2`. On 127 strokes, one side of
  the row's box lies inside the decoded points themselves, by up to 0.64 px. The app's
  bounds probably come from a smoothed outline; the difference is far below a line
  width.
- **Rendering.** The survey rendered FM2 W1 page 1 and IAS W2 page 2 with this position
  decoding, and both are fully legible. With this decoder, a plain polyline preview of FM2
  W1 page 1 (383 strokes) is upright and legible, in black, purple (`0xFF6300FF`) and
  green (`0xFF00FFA5`) ink. With the decoder on, the unbounded page of "DBA CW 1 ER" (951
  blob strokes) becomes a 3115 × 3654 px page (3114.6 × 3654.1).
- **Loading.** With the decoder on, all 423 notes load 831,556 strokes (439,638
  `record_json` + 391,918 blob, every row) with no warning of any kind.
- **JSON twins.** `data/Notein/*/*.in` holds older exports of 123 notes (263,941 JSON
  strokes, 51,544 blob strokes). No stroke id is stored as a blob in one copy and as
  `record_json` in the other, in either direction. So there is no stroke-level ground
  truth, and `test_matches_json_twin_when_available` skips with that reason (it compares
  positions automatically if a later export contains such twins).

Further checks added after independent review:

- **Colour and width on real data.** `test_decoded_bounds_match_row_bounds` also asserts,
  for every real blob stroke, that the colour is one of the five opaque pen colours and
  that the row box is the decoded box grown by at most half the scaled width (+0.05 px).
  Reading the low half of field 5 (as the build plan first said) fails that test.
- **Pages with both encodings.** Eight pages in six notes hold `record_json` and blob strokes together.
  Drawn in two colours, the blob ink continues the JSON handwriting on the same
  baselines, at the same letter height and margins. This is the only evidence that ties
  blob positions to the JSON encoding, and it was judged by eye, not by a test.
- **Sanity limits.** A blob that decodes to a coordinate beyond ±1e6 px or a width over
  1000 px raises `ValueError` (damaged data; real ink stays within a few thousand px).

## Still unknown

Fields 6, 8, 9 and 14, columns 2–4 of field 11, and whether a non-identity bottom
matrix row or a non-sRGB colour ever occurs. The decoder does not need any of these to
place and colour the ink.
