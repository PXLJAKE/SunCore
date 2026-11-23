# SPDX-License-Identifier: Apache-2.0
"""32-bit values as two Modbus registers for ``modbus.write_register`` (pure).

HA's ``modbus.write_register`` takes raw unsigned 16-bit words only (no float32, research
``ha-websocket-api.md`` "Modbus write action"); the adapter encodes. Kostal register 5 tells the
word order (``kostal-plenticore-modbus.md`` "Connection"): ``0x00`` little-endian = CDAB (the
low word first, the default), ``0x01`` big-endian = ABCD (the high word first, SunSpec). The
same word order is assumed for the unsigned 32-bit timeout (``UNVERIFIED``: the document states
the byte order for "float/32-bit registers").
"""

from __future__ import annotations

import math
import struct

from suncore.core.battery import ModbusByteOrder


def _ordered(high: int, low: int, order: ModbusByteOrder) -> list[int]:
    return [high, low] if order is ModbusByteOrder.BIG else [low, high]


def float32_words(value: float, order: ModbusByteOrder) -> list[int]:
    """IEEE 754 single precision as two 16-bit words in the given word order."""
    if not math.isfinite(value):
        msg = "a register value must be finite"
        raise ValueError(msg)
    (raw,) = struct.unpack(">I", struct.pack(">f", value))
    return _ordered(raw >> 16, raw & 0xFFFF, order)


def words_float32(words: list[int], order: ModbusByteOrder) -> float:
    """The inverse of :func:`float32_words` (tests, the simulator)."""
    high, low = (words[0], words[1]) if order is ModbusByteOrder.BIG else (words[1], words[0])
    (value,) = struct.unpack(">f", struct.pack(">I", (high << 16) | low))
    return float(value)


def uint32_words(value: int, order: ModbusByteOrder) -> list[int]:
    """An unsigned 32-bit value as two 16-bit words in the given word order."""
    if not 0 <= value <= 0xFFFFFFFF:
        msg = "an U32 register value must lie in 0..2**32-1"
        raise ValueError(msg)
    return _ordered(value >> 16, value & 0xFFFF, order)


def words_uint32(words: list[int], order: ModbusByteOrder) -> int:
    high, low = (words[0], words[1]) if order is ModbusByteOrder.BIG else (words[1], words[0])
    return (high << 16) | low


__all__ = ["float32_words", "uint32_words", "words_float32", "words_uint32"]
