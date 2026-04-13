"""
Generate Chrome extension PNG icons from a simple raster draw routine.

No external dependencies. Writes:
  extension/icons/icon16.png
  extension/icons/icon48.png
  extension/icons/icon128.png
"""

from __future__ import annotations

import os
import struct
import zlib


def _png_bytes_rgba(w: int, h: int, rgba: bytes) -> bytes:
    # Each row starts with filter byte 0x00
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba[y * stride : (y + 1) * stride])
    comp = zlib.compress(bytes(raw), level=9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", comp),
            chunk(b"IEND", b""),
        ]
    )


def _blend(dst: bytearray, idx: int, r: int, g: int, b: int, a: int) -> None:
    # alpha blend over dst (premult not used)
    da = dst[idx + 3]
    if a == 255 or da == 0:
        dst[idx : idx + 4] = bytes((r, g, b, a))
        return
    out_a = a + da * (255 - a) // 255
    if out_a == 0:
        dst[idx : idx + 4] = b"\x00\x00\x00\x00"
        return
    out_r = (r * a + dst[idx] * da * (255 - a) // 255) // out_a
    out_g = (g * a + dst[idx + 1] * da * (255 - a) // 255) // out_a
    out_b = (b * a + dst[idx + 2] * da * (255 - a) // 255) // out_a
    dst[idx : idx + 4] = bytes((out_r, out_g, out_b, out_a))


def _fill_round_rect(
    img: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    rw: int,
    rh: int,
    r: int,
    col: tuple[int, int, int, int],
) -> None:
    cr, cg, cb, ca = col
    r2 = r * r
    for yy in range(y, y + rh):
        if yy < 0 or yy >= h:
            continue
        for xx in range(x, x + rw):
            if xx < 0 or xx >= w:
                continue
            # corner reject
            dx = 0
            dy = 0
            if xx < x + r:
                dx = (x + r - 1) - xx
            elif xx >= x + rw - r:
                dx = xx - (x + rw - r)
            if yy < y + r:
                dy = (y + r - 1) - yy
            elif yy >= y + rh - r:
                dy = yy - (y + rh - r)
            if dx and dy and (dx * dx + dy * dy) >= r2:
                continue
            idx = (yy * w + xx) * 4
            _blend(img, idx, cr, cg, cb, ca)


def _fill_rect(
    img: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    rw: int,
    rh: int,
    col: tuple[int, int, int, int],
) -> None:
    cr, cg, cb, ca = col
    for yy in range(y, y + rh):
        if yy < 0 or yy >= h:
            continue
        row = (yy * w) * 4
        for xx in range(x, x + rw):
            if xx < 0 or xx >= w:
                continue
            idx = row + xx * 4
            _blend(img, idx, cr, cg, cb, ca)


def render_icon(size: int) -> bytes:
    # Base colors from spec
    SNU = (0x00, 0x38, 0x76, 255)
    WHITE = (255, 255, 255, 255)
    # Start transparent
    img = bytearray(b"\x00" * (size * size * 4))

    # Scale spec coords from 128
    def sc(v: int) -> int:
        return max(1, round(v * size / 128))

    _fill_round_rect(img, size, size, 0, 0, size, size, sc(28), SNU)
    _fill_round_rect(img, size, size, sc(24), sc(36), sc(80), sc(68), sc(8), WHITE)
    _fill_rect(img, size, size, sc(24), sc(52), sc(80), sc(4), (0x00, 0x38, 0x76, round(255 * 0.15)))
    _fill_round_rect(img, size, size, sc(44), sc(24), sc(8), sc(24), sc(4), WHITE)
    _fill_round_rect(img, size, size, sc(76), sc(24), sc(8), sc(24), sc(4), WHITE)
    dark70 = (0x00, 0x38, 0x76, round(255 * 0.7))
    dark40 = (0x00, 0x38, 0x76, round(255 * 0.4))
    _fill_round_rect(img, size, size, sc(36), sc(66), sc(16), sc(16), sc(3), dark70)
    _fill_round_rect(img, size, size, sc(56), sc(66), sc(16), sc(16), sc(3), dark70)
    _fill_round_rect(img, size, size, sc(76), sc(66), sc(16), sc(16), sc(3), dark70)
    _fill_round_rect(img, size, size, sc(36), sc(86), sc(16), sc(10), sc(3), dark40)
    _fill_round_rect(img, size, size, sc(56), sc(86), sc(16), sc(10), sc(3), dark40)

    return _png_bytes_rgba(size, size, bytes(img))


def main() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out_dir = os.path.join(root, "extension", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for size, name in [(16, "icon16.png"), (48, "icon48.png"), (128, "icon128.png")]:
        p = os.path.join(out_dir, name)
        with open(p, "wb") as f:
            f.write(render_icon(size))
        print("wrote", p)


if __name__ == "__main__":
    main()

