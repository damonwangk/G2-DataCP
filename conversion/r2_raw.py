"""Bounded reader for the observed G2 pbdat bundle container; no SDK imports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

import numpy as np

MAX_PAYLOAD = 64 * 1024 * 1024
MAX_ELEMENTS = 8_000_000


@dataclass(frozen=True)
class Bundle:
    index: int
    record_timestamp_ns: int
    timestamp_ns: int
    values: dict[str, np.ndarray]


def varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ValueError("Protobuf varint exceeds uint64")
        result |= (byte & 127) << shift
        if byte < 128:
            return result, offset
    raise ValueError("Oversized protobuf varint")


def wire(data: bytes) -> list[tuple[int, int, bytes | int]]:
    fields = []
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, kind = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise ValueError(f"Invalid protobuf field number: {number}")
        if kind == 0:
            value, offset = varint(data, offset)
        elif kind in (1, 2, 5):
            if kind == 2:
                size, offset = varint(data, offset)
            else:
                size = 8 if kind == 1 else 4
            if size > MAX_PAYLOAD or offset + size > len(data):
                raise ValueError("Truncated or oversized protobuf field")
            value = data[offset:offset + size]
            offset += size
        else:
            raise ValueError(f"Unsupported protobuf wire type: {kind}")
        fields.append((number, kind, value))
    return fields


def _unique_fields(data: bytes, expected: dict[int, int], label: str) -> dict[int, bytes | int]:
    result = {}
    for number, kind, value in wire(data):
        if number not in expected or kind != expected[number]:
            raise ValueError(f"Unexpected {label} field ({number}, {kind})")
        if number in result:
            raise ValueError(f"Duplicate {label} field {number}")
        result[number] = value
    return result


def _tensor(data: bytes) -> np.ndarray:
    fields = _unique_fields(data, {1: 2, 2: 0, 3: 2}, "tensor")
    if 1 not in fields or 2 not in fields:
        raise ValueError("Tensor missing shape or dtype")
    raw_shape = fields[1]
    shape = []
    offset = 0
    while offset < len(raw_shape):
        size, offset = varint(raw_shape, offset)
        shape.append(size)
        if len(shape) > 8:
            raise ValueError("Tensor rank exceeds 8")
    count = 1
    for size in shape:
        count *= size
        if count > MAX_ELEMENTS:
            raise ValueError("Tensor element count exceeds limit")
    dtype_id = fields[2]
    raw = fields.get(3, b"")
    dtype_map = {3: "<f8", 6: "<i4", 10: "<u4", 11: "<u8"}
    if dtype_id in dtype_map:
        dtype = np.dtype(dtype_map[dtype_id])
        if len(raw) != count * dtype.itemsize:
            raise ValueError("Tensor payload size differs from shape")
        return np.frombuffer(raw, dtype=dtype).copy().reshape(shape)
    if dtype_id == 12:
        # Observed bundle names use NUL-separated UTF-8, optionally NUL-terminated.
        encoded = raw[:-1] if raw.endswith(b"\x00") else raw
        strings = encoded.split(b"\x00") if count else []
        if len(strings) != count:
            raise ValueError("String tensor count differs from shape")
        return np.asarray([item.decode("utf-8", errors="strict") for item in strings]).reshape(shape)
    raise ValueError(f"Unsupported bundle tensor dtype {dtype_id}")


def read_bundles(path: str | Path) -> list[Bundle]:
    """Read observed original bundles, retaining absent actions as absent keys.

    Envelope timestamps and internal timestamps are checked independently. Their
    meanings remain recorder/publication time, not verified hardware sample time.
    """
    path = Path(path)
    size = path.stat().st_size
    result = []
    with path.open("rb") as stream:
        first = stream.read(8)
        if len(first) != 8:
            raise ValueError(f"{path}: missing record count")
        declared, = struct.unpack("<q", first)
        if declared < 0 or declared > (size - 8) // 40:
            raise ValueError(f"{path}: invalid declared record count {declared}")
        for index in range(declared):
            header = stream.read(40)
            if len(header) != 40:
                raise ValueError(f"{path}: truncated record header {index}")
            record_ns, _reserved1, _reserved2, _reserved3, length = struct.unpack("<5q", header)
            if not 0 < length <= MAX_PAYLOAD or length > size - stream.tell():
                raise ValueError(f"{path}: invalid payload length at record {index}")
            payload = stream.read(length)
            values = {}
            timestamp_ns = None
            for number, kind, data in wire(payload):
                if number == 2 and kind == 0:
                    if timestamp_ns is not None:
                        raise ValueError("Duplicate bundle timestamp")
                    timestamp_ns = data
                elif number == 3 and kind == 2:
                    entry = _unique_fields(data, {1: 2, 2: 2}, "map entry")
                    if set(entry) != {1, 2}:
                        raise ValueError("Incomplete bundle map entry")
                    key = entry[1].decode("utf-8", errors="strict")
                    if not key or key in values:
                        raise ValueError(f"Empty or duplicate bundle key: {key!r}")
                    values[key] = _tensor(entry[2])
                else:
                    raise ValueError(f"Unexpected bundle field ({number}, {kind})")
            if timestamp_ns is None or timestamp_ns <= 0 or record_ns <= 0:
                raise ValueError("Missing or invalid positive timestamps")
            if result and (timestamp_ns <= result[-1].timestamp_ns or record_ns <= result[-1].record_timestamp_ns):
                raise ValueError(f"{path}: non-increasing bundle timestamps at record {index}")
            result.append(Bundle(index, record_ns, timestamp_ns, values))
        if stream.read(1):
            raise ValueError(f"{path}: trailing bytes after declared records")
    return result
