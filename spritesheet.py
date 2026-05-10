"""GIF → PNG spritesheets + GifData.lua-style metrics."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import requests
from PIL import Image, ImageSequence

from showdown import BROWSER_HEADERS

MAX_SHEET = 1024


@dataclass
class SheetSpec:
    png_bytes: bytes
    rows: int


@dataclass
class PackResult:
    sheets: List[SheetSpec]
    n_frames: int
    f_width: int
    f_height: int
    frames_per_row: int


def _load_gif_frames(data: bytes) -> List[Image.Image]:
    im = Image.open(io.BytesIO(data))
    return [frame.convert("RGBA") for frame in ImageSequence.Iterator(im)]


def download_gif_frames(url: str, timeout: float = 120.0) -> List[Image.Image]:
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    r.raise_for_status()
    return _load_gif_frames(r.content)


def pack_gif_from_bytes(data: bytes) -> PackResult:
    """Same as downloading from URL but avoids a second network fetch."""
    return pack_gif_frames(_load_gif_frames(data))


def _cell_size(frames: Sequence[Image.Image]) -> Tuple[int, int]:
    mw = max(f.size[0] for f in frames)
    mh = max(f.size[1] for f in frames)
    return mw, mh


def _paste_frames_grid(
    frames: Sequence[Image.Image],
    cell_w: int,
    cell_h: int,
    frames_per_row: int,
) -> Tuple[Image.Image, int]:
    n = len(frames)
    rows = math.ceil(n / frames_per_row) if n else 0
    sheet = Image.new("RGBA", (frames_per_row * cell_w, rows * cell_h), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        x = (i % frames_per_row) * cell_w
        y = (i // frames_per_row) * cell_h
        ox = (cell_w - fr.size[0]) // 2
        oy = (cell_h - fr.size[1]) // 2
        sheet.paste(fr, (x + ox, y + oy), fr)
    return sheet, rows


def _choose_frames_per_row(
    n_frames: int,
    cell_w: int,
    cell_h: int,
    max_dim: int = MAX_SHEET,
) -> Tuple[int, int]:
    """
    Return (frames_per_row, max_frames_that_fit_on_one_sheet).
    Prefer larger frames_per_row (more columns) to reduce rows, matching typical sheets.
    """
    max_fpr = min(n_frames, max_dim // cell_w)
    max_rows = max_dim // cell_h
    if max_rows <= 0 or max_fpr <= 0:
        raise ValueError(
            f"Frame size {cell_w}x{cell_h} exceeds Roblox {max_dim}x{max_dim} sheet limit"
        )

    for fpr in range(max_fpr, 0, -1):
        per_sheet = fpr * max_rows
        rem = n_frames
        ok = True
        while rem > 0:
            take = min(per_sheet, rem)
            rows = math.ceil(take / fpr)
            if rows > max_rows or fpr * cell_w > max_dim:
                ok = False
                break
            rem -= take
        if ok:
            return fpr, per_sheet

    raise ValueError("Could not find a valid frames-per-row layout")


def pack_gif_frames(
    frames: Sequence[Image.Image],
    subtract_one_for_lua: bool = True,
) -> PackResult:
    """
    Pack frames into sheets; all sheets share the same framesPerRow (GifData convention).
    """
    if not frames:
        raise ValueError("No frames in GIF")

    cell_w, cell_h = _cell_size(frames)
    n = len(frames)

    fpr, per_sheet = _choose_frames_per_row(n, cell_w, cell_h)
    max_rows = MAX_SHEET // cell_h

    sheets: List[SheetSpec] = []
    offset = 0
    while offset < n:
        take = min(per_sheet, n - offset)
        chunk = frames[offset : offset + take]
        offset += take
        sheet_img, rows_used = _paste_frames_grid(chunk, cell_w, cell_h, fpr)
        if rows_used > max_rows:
            raise RuntimeError("Sheet row overflow; logic error")
        buf = io.BytesIO()
        sheet_img.save(buf, format="PNG", optimize=True)
        sheets.append(SheetSpec(png_bytes=buf.getvalue(), rows=rows_used))

    f_width = max(cell_w - 1, 1) if subtract_one_for_lua else cell_w
    f_height = max(cell_h - 1, 1) if subtract_one_for_lua else cell_h

    return PackResult(
        sheets=sheets,
        n_frames=n,
        f_width=f_width,
        f_height=f_height,
        frames_per_row=fpr,
    )


def pack_gif_from_url(url: str) -> PackResult:
    frames = download_gif_frames(url)
    return pack_gif_frames(frames)
