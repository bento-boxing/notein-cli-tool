"""Decode Notein's binary ink strokes (`StrokeEntity.ink_stroke_blob`).

The blob is an uncompressed protobuf message without a published schema; the field
meanings were established from the real backup. See `docs/inkblob-format.md` for the
format and the evidence behind each decision here.
"""

from __future__ import annotations

import math
import struct
import sys
from array import array
from itertools import repeat

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

_MAX_VARINT_BYTES = 10  # a 64-bit value needs at most 10 groups of 7 bits

# Stroke fields this decoder reads, with the wire type each must have.
F_WIDTH, F_COLOR, F_XY, F_POINT_DATA, F_M12, F_M13 = 4, 5, 10, 11, 12, 13
_WIRE = {F_WIDTH: WIRE_FIXED32, F_COLOR: WIRE_VARINT, F_XY: WIRE_BYTES,
         F_POINT_DATA: WIRE_BYTES, F_M12: WIRE_BYTES, F_M13: WIRE_BYTES}
POINT_DATA_FLOATS = 5  # field 11 per point: elapsed ms, pressure, three unknown values
PRESSURE_INDEX = 1
DEFAULT_PRESSURE = 1.0  # as for a JSON point without "p"
# Sanity limits, in page pixels. Real ink stays within a few thousand pixels (the largest
# unbounded page reaches 3823) with widths near 2.6; a damaged blob can decode to finite
# but absurd values, and one such stroke would blow up an unbounded page's size.
MAX_COORD = 1e6
MAX_WIDTH = 1000.0
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
_FLOAT = struct.Struct("<f")
_MATRIX = struct.Struct("<9f")
_SWAP_FLOATS = sys.byteorder != "little"  # the blob's floats are little-endian


def _float32s(data: bytes) -> array:
    """Packed little-endian float32 values as an array (sliced and summed in C, with no
    Python object per value until one is used). `len(data)` must be a multiple of 4."""
    values = array("f")
    values.frombytes(data)
    if _SWAP_FLOATS:
        values.byteswap()
    return values


def decode_stroke(blob: bytes) -> Stroke:
    """The stroke stored in an `ink_stroke_blob`, in page pixels.

    Points are field 10's raw (x, y) mapped through M13 · M12 (fields 13 and 12, each
    the identity when missing), with field 11's pressure; the width (field 4) is scaled
    by the transform; the colour (field 5, an Android ColorLong) becomes a signed 32-bit
    ARGB int like a `record_json` colour.

    Raises ValueError (only) for a blob that is malformed or truncated, lacks field 10, 4
    or 5, has an odd number of floats in field 10, or decodes to non-finite coordinates.
    """
    xy_parts: list[bytes] = []
    point_data_parts: list[bytes] = []
    width = color = None
    m12 = m13 = _IDENTITY
    for number, wire, value in read_fields(blob):
        expected = _WIRE.get(number)
        if expected is None:
            continue  # ids, timestamps, brush and unknown fields
        if wire != expected:
            raise ValueError(f"field {number} has wire type {wire}, expected {expected}")
        if number == F_XY:
            xy_parts.append(value)
        elif number == F_POINT_DATA:
            point_data_parts.append(value)
        elif number == F_WIDTH:
            (width,) = _FLOAT.unpack(value)
        elif number == F_COLOR:
            color = value
        elif number == F_M12:
            m12 = _matrix(value, number)
        else:
            m13 = _matrix(value, number)
    if not xy_parts:
        raise ValueError("no points (field 10)")
    if width is None or color is None:
        raise ValueError("no width (field 4)" if width is None else "no colour (field 5)")
    if not (math.isfinite(width) and 0 <= width <= MAX_WIDTH):
        raise ValueError(f"bad width {width}")

    xy = b"".join(xy_parts)
    if len(xy) % 8:
        raise ValueError(f"field 10 holds {len(xy)} bytes, not a whole number of x, y float32 pairs")
    count = len(xy) // 8
    raw = _float32s(xy)
    xs, ys = raw[0::2], raw[1::2]

    a, b, c, d, e, f, g, h, i = _multiply(m13, m12)
    if g == 0.0 and h == 0.0:  # affine, as in every real blob
        if i == 0.0:
            raise ValueError("transform maps every point to infinity")
        if i != 1.0:
            a, b, c, d, e, f = a / i, b / i, c / i, d / i, e / i, f / i
        if b == 0.0 and d == 0.0:  # no rotation or skew: the real case
            px = [a * x + c for x in xs]
            py = [e * y + f for y in ys]
        else:
            px = [a * x + b * y + c for x, y in zip(xs, ys)]
            py = [d * x + e * y + f for x, y in zip(xs, ys)]
        scale = math.sqrt(abs(a * e - b * d))
    else:
        px, py, scale = _projective((a, b, c, d, e, f, g, h, i), xs, ys)
    if not math.isfinite(sum(px) + sum(py)):  # NaN and ±inf propagate through the sums
        raise ValueError("non-finite coordinate")
    if px and not (-MAX_COORD <= min(px) and max(px) <= MAX_COORD
                   and -MAX_COORD <= min(py) and max(py) <= MAX_COORD):
        raise ValueError("coordinate far outside any page")
    width *= scale
    if not (math.isfinite(width) and width <= MAX_WIDTH):
        raise ValueError("bad width after transform")

    return Stroke(list(zip(px, py, _pressures(point_data_parts, count))), _argb(color), width)


def _matrix(value: bytes, number: int) -> tuple[float, ...]:
    if len(value) != _MATRIX.size:
        raise ValueError(f"field {number} is {len(value)} bytes, not a 3x3 float32 matrix")
    matrix = _MATRIX.unpack(value)
    if not all(map(math.isfinite, matrix)):
        raise ValueError(f"field {number} has a non-finite entry")
    return matrix


def _multiply(p: tuple[float, ...], q: tuple[float, ...]) -> tuple[float, ...]:
    """The row-major 3x3 product p · q."""
    return tuple(p[r] * q[col] + p[r + 1] * q[col + 3] + p[r + 2] * q[col + 6]
                 for r in (0, 3, 6) for col in (0, 1, 2))


def _projective(m, xs, ys) -> tuple[list[float], list[float], float]:
    """Points through a matrix whose bottom row is not (0, 0, i); never seen in real data.
    The width scale is the transform's local scale at the first point."""
    a, b, c, d, e, f, g, h, i = m
    ws = [g * x + h * y + i for x, y in zip(xs, ys)]
    if 0.0 in ws:
        raise ValueError("transform maps a point to infinity")
    px = [(a * x + b * y + c) / w for x, y, w in zip(xs, ys, ws)]
    py = [(d * x + e * y + f) / w for x, y, w in zip(xs, ys, ws)]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    try:
        scale = math.sqrt(abs(det)) / abs(ws[0] if ws else i) ** 1.5
    except (ZeroDivisionError, OverflowError):
        raise ValueError("degenerate transform") from None
    return px, py, scale


def _pressures(parts: list[bytes], count: int):
    """Field 11's pressure per point, or 1.0 for every point when field 11 is missing,
    does not hold exactly 5 floats per point, or has a non-finite pressure."""
    data = b"".join(parts)
    if len(data) != 4 * POINT_DATA_FLOATS * count or not count:
        return repeat(DEFAULT_PRESSURE)
    pressures = _float32s(data)[PRESSURE_INDEX::POINT_DATA_FLOATS]
    if not math.isfinite(sum(pressures)):
        return repeat(DEFAULT_PRESSURE)
    return pressures


def _argb(value: int) -> int:
    """Field 5 as a signed 32-bit ARGB int. Real blobs hold an Android sRGB ColorLong,
    the ARGB int shifted left by 32 (colour space id 0 in the low 6 bits); a value that
    fits in 32 bits is taken as a plain ARGB int."""
    if value >> 32:
        space = value & 0x3F
        if space:
            raise ValueError(f"colour in colour space {space}; only sRGB (0) is supported")
        value >>= 32
    return value - (1 << 32) if value & 0x80000000 else value


def read_fields(blob: bytes) -> list[tuple[int, int, bytes | int]]:
    """The top-level fields of a protobuf message as (field number, wire type, value),
    in stored order: a varint as an int, fixed64/fixed32/length-delimited values as raw
    bytes. No schema; nested messages are not expanded.

    Raises ValueError on truncated or malformed input (a cut-off value, a length past the
    end, a varint over 64 bits, field number 0, or a group or unknown wire type).
    """
    data = bytes(blob)
    end = len(data)
    pos = 0
    fields: list[tuple[int, int, bytes | int]] = []
    while pos < end:
        key = data[pos]
        if key < 0x80:  # one-byte key: every field number below 16 (all real fields)
            pos += 1
        else:
            key, pos = _varint(data, pos, end)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError(f"field number 0 at byte {pos}")
        if wire == WIRE_VARINT:
            value, pos = _varint(data, pos, end)
            fields.append((number, wire, value))
            continue
        if wire == WIRE_BYTES:
            if pos < end and data[pos] < 0x80:  # one-byte length
                length = data[pos]
                pos += 1
            else:
                length, pos = _varint(data, pos, end)
        elif wire == WIRE_FIXED32:
            length = 4
        elif wire == WIRE_FIXED64:
            length = 8
        else:
            raise ValueError(f"unsupported wire type {wire} for field {number}")
        stop = pos + length
        if stop > end:
            raise ValueError(f"field {number} needs {length} bytes, {end - pos} left")
        fields.append((number, wire, data[pos:stop]))
        pos = stop
    return fields


def _varint(data: bytes, pos: int, end: int) -> tuple[int, int]:
    """The base-128 varint at `pos` and the position after it."""
    result = 0
    for shift in range(0, 7 * _MAX_VARINT_BYTES, 7):
        if pos >= end:
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            if result >> 64:
                raise ValueError("varint over 64 bits")
            return result, pos
    raise ValueError(f"varint longer than {_MAX_VARINT_BYTES} bytes")


# Imported last on purpose: notedb imports decode_stroke from this module (at its end) and
# this module needs notedb's Stroke. With both imports after the definitions they need,
# the cycle resolves whichever module is imported first.
from notes_cli.notedb import Stroke  # noqa: E402
