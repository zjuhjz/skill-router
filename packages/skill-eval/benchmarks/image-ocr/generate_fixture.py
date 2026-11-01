#!/usr/bin/env python3
"""Generate the code-owned OCR fixture without fonts or image dependencies."""

from __future__ import annotations

import struct
import math
import zlib
from pathlib import Path


TEXT = "SKILLCALL OCR\nVERSION 0.4\nDATE 2026-11-01"
SCALE = 6
MARGIN = 24
GLYPHS = {
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
}


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def _render(foreground: int = 0, background: int = 255) -> tuple[int, int, bytearray]:
    lines = TEXT.splitlines()
    unknown = sorted(set("".join(lines)) - set(GLYPHS))
    if unknown:
        raise ValueError(f"missing glyphs: {unknown}")
    cell_width = 6 * SCALE
    line_height = 9 * SCALE
    width = MARGIN * 2 + max(len(line) for line in lines) * cell_width
    height = MARGIN * 2 + len(lines) * line_height - 2 * SCALE
    pixels = bytearray([background] * (width * height * 3))
    for line_index, line in enumerate(lines):
        top = MARGIN + line_index * line_height
        for char_index, char in enumerate(line):
            left = MARGIN + char_index * cell_width
            for row, bits in enumerate(GLYPHS[char]):
                for column, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    for offset_y in range(SCALE):
                        for offset_x in range(SCALE):
                            x = left + column * SCALE + offset_x
                            y = top + row * SCALE + offset_y
                            position = (y * width + x) * 3
                            pixels[position : position + 3] = bytes([foreground]) * 3
    return width, height, pixels


def _rotate(
    width: int, height: int, pixels: bytearray, degrees: float, background: int = 255
) -> tuple[int, int, bytearray]:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    new_width = math.ceil(abs(width * cosine) + abs(height * sine))
    new_height = math.ceil(abs(width * sine) + abs(height * cosine))
    rotated = bytearray([background] * (new_width * new_height * 3))
    source_cx, source_cy = (width - 1) / 2, (height - 1) / 2
    target_cx, target_cy = (new_width - 1) / 2, (new_height - 1) / 2
    for target_y in range(new_height):
        for target_x in range(new_width):
            relative_x, relative_y = target_x - target_cx, target_y - target_cy
            source_x = round(relative_x * cosine + relative_y * sine + source_cx)
            source_y = round(-relative_x * sine + relative_y * cosine + source_cy)
            if 0 <= source_x < width and 0 <= source_y < height:
                source = (source_y * width + source_x) * 3
                target = (target_y * new_width + target_x) * 3
                rotated[target : target + 3] = pixels[source : source + 3]
    return new_width, new_height, rotated


def _write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    rows = b"".join(b"\x00" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, level=9))
        + _chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def generate(path: Path) -> None:
    _write_png(path, *_render())


def generate_all(directory: Path) -> None:
    basic = _render()
    _write_png(directory / "ocr-basic.png", *basic)
    _write_png(directory / "ocr-low-contrast.png", *_render(foreground=150, background=235))
    _write_png(directory / "ocr-rotated.png", *_rotate(*basic, degrees=3.0))


if __name__ == "__main__":
    generate_all(Path(__file__).resolve().parent / "fixtures")
