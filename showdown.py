"""Fetch Pokémon Showdown animated sprite directory listings."""

from __future__ import annotations

import re
from typing import List, Optional

import requests

BASE = "https://play.pokemonshowdown.com/sprites"

# Non-browser User-Agents are often blocked with HTTP 403 by Showdown's CDN.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://play.pokemonshowdown.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

PATHS = {
    "_FRONT": "/ani/",
    "_BACK": "/ani-back/",
    "_SHINY_FRONT": "/ani-shiny/",
    "_SHINY_BACK": "/ani-back-shiny/",
}

_SESSION = requests.Session()
_SESSION.headers.update(BROWSER_HEADERS)

_GIF_HREF = re.compile(r'href="([^"]+\.gif)"')


def fetch_gif_filenames(path_key: str = "_FRONT", timeout: float = 60.0) -> List[str]:
    """Return sorted bare filenames like 'meganium-mega.gif' for the given variant."""
    rel = PATHS[path_key]
    url = f"{BASE}{rel}"
    r = _SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    names = []
    for m in _GIF_HREF.finditer(r.text):
        name = m.group(1).lstrip("./")
        if name.lower().endswith(".gif"):
            names.append(name)
    return sorted(set(names), key=str.lower)


def gif_url(path_key: str, filename: str) -> str:
    rel = PATHS[path_key].rstrip("/")
    return f"{BASE}{rel}/{filename}"


def iter_sprite_filename_candidates(filename: str) -> List[str]:
    """
    Filename sequence to try on Showdown when an exact path may be missing.

    Gendered fronts often use ``species-f.gif`` / ``species-m.gif`` while backs may only
    ship ``species.gif`` (same GIF for both genders).
    """
    if not filename.lower().endswith(".gif"):
        return [filename]
    stem = filename[:-4]
    parts = stem.split("-")
    out: List[str] = [filename]
    if len(parts) >= 2 and parts[-1] in ("f", "m"):
        alt = "-".join(parts[:-1]) + ".gif"
        if alt.lower() != filename.lower():
            out.append(alt)
    return out


def fetch_sprite_bytes_for_variant(
    path_key: str, filename: str, timeout: float = 45.0
) -> tuple[Optional[bytes], str, str]:
    """
    Fetch sprite bytes using filename fallbacks per variant folder.

    Returns ``(data_or_none, last_attempt_url, resolved_filename)``.
    When nothing loads, ``data_or_none`` is None and ``resolved_filename`` is the
    original ``filename``.
    """
    last_url = ""
    for cand in iter_sprite_filename_candidates(filename):
        url = gif_url(path_key, cand)
        last_url = url
        data = fetch_sprite_bytes(url, timeout=timeout)
        if data:
            return data, url, cand
    return None, last_url, filename


def fetch_sprite_bytes(url: str, timeout: float = 45.0) -> Optional[bytes]:
    """
    Download sprite bytes using the same session as directory listings.

    Use this for UI previews: passing bare HTTPS URLs to Streamlit can fail
    (hotlink / referrer rules) even when server-side fetch works.
    """
    try:
        r = _SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def slug_to_default_name(slug: str) -> str:
    """meganium-mega -> Meganium-mega (Showdown slug without .gif)."""
    slug = slug.replace(".gif", "")
    return "-".join(part.capitalize() for part in slug.split("-") if part)
