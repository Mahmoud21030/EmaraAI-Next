"""Draw assets/emaraai.ico (16-256 px) with nothing but the standard library.

    .venv\\Scripts\\python scripts\\make_icon.py

A rounded square with the blue-to-violet brand gradient and a white "E". Small sizes are stored as classic
bitmaps (every Windows dialog can read them), 256 px as PNG.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 256)
A, B = (47, 107, 255), (124, 92, 255)        # --acc, --acc2 of the Control Center
SS = 4                                       # supersampling per axis


def _inside(x: float, y: float) -> tuple[bool, bool]:
    """(inside the rounded square, inside the letter) for a point in the unit square."""
    r = 0.22
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    square = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    stem = 0.30 <= x <= 0.42 and 0.24 <= y <= 0.76
    bars = 0.30 <= x <= 0.72 and (0.24 <= y <= 0.355 or 0.4425 <= y <= 0.5575 or 0.645 <= y <= 0.76)
    short = x <= 0.64 or not 0.4425 <= y <= 0.5575      # the middle bar is shorter
    return square, square and (stem or (bars and short))


def render(n: int) -> list[tuple[int, int, int, int]]:
    px = []
    for j in range(n):
        for i in range(n):
            cover = white = 0
            for sj in range(SS):
                for si in range(SS):
                    sq, letter = _inside((i + (si + 0.5) / SS) / n, (j + (sj + 0.5) / SS) / n)
                    cover += sq
                    white += letter
            if not cover:
                px.append((0, 0, 0, 0))
                continue
            t = (i + j) / (2 * n - 2)
            w = white / cover
            rgb = [round((A[k] + (B[k] - A[k]) * t) * (1 - w) + 255 * w) for k in range(3)]
            px.append((rgb[0], rgb[1], rgb[2], round(255 * cover / (SS * SS))))
    return px


def png(n: int, px) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in px[r * n:(r + 1) * n]) for r in range(n))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def dib(n: int, px) -> bytes:
    head = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = [b"".join(bytes((p[2], p[1], p[0], p[3])) for p in px[r * n:(r + 1) * n]) for r in reversed(range(n))]
    mask_row = b"\x00" * (((n + 31) // 32) * 4)          # alpha channel does the masking
    return head + b"".join(rows) + mask_row * n


def main() -> None:
    images = []
    for n in SIZES:
        px = render(n)
        images.append((n, png(n, px) if n >= 256 else dib(n, px)))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for n, data in images:
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    target = Path(__file__).resolve().parents[1] / "assets" / "emaraai.ico"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(out + b"".join(d for _, d in images))
    (target.parent / "emaraai.png").write_bytes(png(256, render(256)))
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
