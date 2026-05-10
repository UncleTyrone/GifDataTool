"""Insert or replace entries inside GifData.lua animation sections."""

from __future__ import annotations

import re
from typing import Tuple

from spritesheet import PackResult

SECTION_MARKERS = ["_FRONT", "_BACK", "_SHINY_FRONT", "_SHINY_BACK"]


def lua_key_expr(name: str) -> str:
    """Return Lua table key syntax for `name`, matching project style."""
    if "'" in name:
        esc = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'["{esc}"]'
    return f"['{name}']"


_LINE_KEY = re.compile(r"^\s*\[(['\"])(.*?)\1\]\s*=")


def _line_matches_lua_key(line: str, name: str) -> bool:
    """True if this line starts a table entry for the given logical key name."""
    m = _LINE_KEY.match(line)
    if not m:
        return False
    return m.group(2) == name


def format_entry_line(name: str, pack: PackResult, asset_ids: list[int]) -> str:
    """Single-line Lua entry matching existing formatting."""
    key = lua_key_expr(name)
    parts = [f"{{id={aid},rows={s.rows}}}" for aid, s in zip(asset_ids, pack.sheets)]
    inner = ",".join(parts)
    return (
        f"\t\t{key}={{sheets={{{inner}}},nFrames={pack.n_frames},"
        f"fWidth={pack.f_width},fHeight={pack.f_height},framesPerRow={pack.frames_per_row}}},"
    )


def _find_section_inner_bounds(content: str, section: str) -> Tuple[int, int]:
    marker = f"\t{section} = {{"
    m_start = content.find(marker)
    if m_start < 0:
        raise ValueError(f"Could not find section {section}")
    open_brace = m_start + len(marker) - 1
    depth = 0
    j = open_brace
    while j < len(content):
        c = content[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, j
        j += 1
    raise ValueError(f"Unclosed section {section}")


def _upsert_inner_block(inner: str, name: str, new_line: str) -> str:
    """
    Replace every line that assigns to this key (['key'] or ["key"]), deduping
    leftovers when the file mixed quote styles.
    """
    lines = inner.splitlines(keepends=True)
    insert_at = 0
    for idx, line in enumerate(lines):
        if "Z-A MEGAS" in line:
            insert_at = idx + 1

    nl = new_line if new_line.endswith("\n") else new_line + "\n"
    out: list[str] = []
    replaced = False
    for line in lines:
        if _line_matches_lua_key(line, name):
            if not replaced:
                out.append(nl)
                replaced = True
            continue
        out.append(line)

    if not replaced:
        out.insert(insert_at, nl)
    return "".join(out)


def upsert_gifdata_entry(
    file_content: str,
    section: str,
    name: str,
    pack: PackResult,
    asset_ids: list[int],
) -> str:
    if section not in SECTION_MARKERS:
        raise ValueError(f"Unknown section {section}")
    inner_start, inner_end = _find_section_inner_bounds(file_content, section)
    inner = file_content[inner_start:inner_end]
    new_line = format_entry_line(name, pack, asset_ids)
    new_inner = _upsert_inner_block(inner, name, new_line)
    return file_content[:inner_start] + new_inner + file_content[inner_end:]


def upsert_multi_section(
    file_content: str,
    updates: list[tuple[str, str, PackResult, list[int]]],
) -> str:
    """
    Apply multiple upserts. Each tuple is (section, display_name, pack, asset_ids).
    Later updates see earlier edits in `file_content` chain — pass cumulative content.
    """
    cur = file_content
    for section, name, pack, aids in updates:
        cur = upsert_gifdata_entry(cur, section, name, pack, aids)
    return cur
