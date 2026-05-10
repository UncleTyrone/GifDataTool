"""
GifData helper: browse Showdown sprites, build spritesheets, upload Image assets to Roblox,
and patch GifData.lua.

Run: streamlit run app.py
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from credentials_browser import (
    apply_saved_credentials_or_env,
    save_credentials_to_browser,
    should_show_disk_path_expander,
    should_show_server_dotenv_save_ui,
    try_local_storage_manager,
    use_server_environ_for_roblox_defaults,
)
from gifdata_patch import format_entry_line, upsert_multi_section
from roblox_upload import upload_image_asset
from showdown import fetch_gif_filenames, fetch_sprite_bytes_for_variant, slug_to_default_name
from spritesheet import PackResult, pack_gif_from_bytes

load_dotenv()

HERE = Path(__file__).resolve().parent
DEFAULT_LUA = HERE / "GifData.lua"
ENV_FILE = HERE / ".env"


def _format_env_line(key: str, val: str) -> str:
    if not val:
        return f"{key}="
    if any(c in val for c in ' \t\n\r#"\'') or key == "GIFDATA_LUA_PATH":
        norm = val.replace("\\", "/").replace('"', '\\"')
        return f'{key}="{norm}"'
    return f"{key}={val}"


def merge_dotenv(path: Path, updates: dict[str, str]) -> None:
    """Upsert keys in a .env file; leave comments and unrelated entries intact."""
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    handled = {k: False for k in updates}
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                out.append(_format_env_line(k, updates[k]))
                handled[k] = True
                continue
        out.append(line)
    for k, v in updates.items():
        if not handled[k]:
            out.append(_format_env_line(k, v))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _initial_lua_path_str() -> str:
    if use_server_environ_for_roblox_defaults():
        env_p = os.environ.get("GIFDATA_LUA_PATH", "").strip()
        if env_p:
            return env_p
    if DEFAULT_LUA.is_file():
        return str(DEFAULT_LUA.resolve())
    return ""


VARIANT_LABELS = [
    ("Front (ani)", "_FRONT"),
    ("Back (ani-back)", "_BACK"),
    ("Shiny front (ani-shiny)", "_SHINY_FRONT"),
    ("Shiny back (ani-back-shiny)", "_SHINY_BACK"),
]

_CUSTOM_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _sanitize_custom_slug(raw: str) -> str:
    s = raw.strip().lower()
    if not s or not _CUSTOM_SLUG_RE.match(s):
        raise ValueError(
            "Slug must use lowercase letters, digits, and hyphens only "
            "(1–63 chars, no leading/trailing hyphen). Example: `my-boss`."
        )
    return s


def _resolve_sprite_bytes(path_key: str, filename: str) -> tuple[bytes | None, str, str]:
    """Showdown fetch with optional per-file custom GIF overrides from session state."""
    raw = (st.session_state.get("custom_sprite_gifs") or {}).get(filename, {}).get(path_key)
    if raw is not None:
        return raw, "(custom upload)", filename
    return fetch_sprite_bytes_for_variant(path_key, filename)


@st.cache_data(ttl=3600, show_spinner="Fetching Showdown /ani/ listing…")
def list_front_gifs():
    return fetch_gif_filenames("_FRONT")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_showdown_front_bytes(filename: str) -> bytes | None:
    """Cached Showdown /ani/ fetch — custom uploads bypass this."""
    blob, _, _ = fetch_sprite_bytes_for_variant("_FRONT", filename)
    return blob


def _front_sprite_thumbnail_bytes(filename: str) -> bytes | None:
    """Browse thumbnails: custom front GIF if set, else cached Showdown."""
    custom = (st.session_state.get("custom_sprite_gifs") or {}).get(filename, {}).get("_FRONT")
    if custom is not None:
        return custom
    return _cached_showdown_front_bytes(filename)


def _browse_sprite_filenames() -> list[str]:
    """Showdown /ani/ listing plus any custom-only slugs so they appear in Browse & search."""
    base = list_front_gifs()
    extra = list((st.session_state.get("custom_sprite_gifs") or {}).keys())
    return sorted(set(base) | set(extra), key=str.lower)


def _toggle_sprite_pick(fn: str) -> None:
    """Immutable update so session state always tracks a fresh list (avoids stale refs)."""
    cur = list(st.session_state.get("gif_sprite_picks") or [])
    if fn in cur:
        st.session_state.gif_sprite_picks = [x for x in cur if x != fn]
    else:
        st.session_state.gif_sprite_picks = [*cur, fn]


def _sprite_browse_load_more(n_items: int) -> None:
    """Extend how many sprites are rendered; ``n_items`` is len(filtered)."""
    cur = int(st.session_state.sprite_browse_show_count)
    remaining = max(0, n_items - cur)
    step = min(_SPRITE_BROWSE_STEP, remaining)
    if step <= 0:
        return
    st.session_state.sprite_browse_show_count = cur + step
    st.session_state.sprite_browse_open = True


def _sprite_pick_button_key(fn: str) -> str:
    # Full SHA256 — avoid any chance of key collisions from a truncated hash.
    h = hashlib.sha256(fn.encode("utf-8")).hexdigest()
    return f"dlg_pick_{h}"


def _env(key: str, ui_val: str) -> str:
    v = (ui_val or "").strip()
    if v:
        return v
    if key.startswith("ROBLOX_") and not use_server_environ_for_roblox_defaults():
        return ""
    return (os.environ.get(key, "") or "").strip()


def _preview_animated_gif(gif_bytes: bytes, width: int = 120) -> None:
    """
    Streamlit's st.image() often shows only the first GIF frame. Embedding as an
    HTML img with a data URL lets the browser animate normally.
    """
    b64 = base64.standard_b64encode(gif_bytes).decode("ascii")
    html = (
        '<div style="text-align:center;line-height:0;">'
        f'<img src="data:image/gif;base64,{b64}" width="{width}" '
        'style="image-rendering:pixelated;image-rendering:-webkit-optimize-contrast;" />'
        "</div>"
    )
    # Sprites vary in height; leave room for tall backs / megas.
    iframe_h = min(max(int(width * 2.4), width + 40), 420)
    st.iframe(html, height=iframe_h, width="stretch")


def _sprite_browse_gif_only(
    gif_bytes: bytes, *, box: int, picked: bool
) -> None:
    """Square GIF only (title rendered separately). Used with an overlaid transparent button."""
    b64 = base64.standard_b64encode(gif_bytes).decode("ascii")
    r = max(10, min(box // 5, 16))
    border = (
        "2px solid rgba(59,130,246,0.95)"
        if picked
        else "1px solid rgba(80,90,110,0.22)"
    )
    bg = "rgba(59,130,246,0.18)" if picked else "rgba(120,130,150,0.12)"
    # Stretch + flex-center matches the label; content-width shrink-wrap left-aligns the sprite.
    html_block = (
        '<div style="width:100%;box-sizing:border-box;display:flex;justify-content:center;padding:0;">'
        f'<div style="width:{box}px;height:{box}px;flex-shrink:0;box-sizing:border-box;'
        f'display:flex;align-items:center;justify-content:center;'
        f'background:{bg};border-radius:{r}px;border:{border};overflow:hidden;">'
        f'<img src="data:image/gif;base64,{b64}" alt="" draggable="false" '
        'style="max-width:92%;max-height:92%;object-fit:contain;'
        'image-rendering:pixelated;image-rendering:-webkit-optimize-contrast;" />'
        "</div></div>"
    )
    st.html(html_block, width="stretch")


def _sprite_browse_tile_label(
    display: str,
    *,
    box: int,
    margin_top_px: float = 0,
) -> None:
    """Name under the sprite; full display string (wraps inside a centered ``box``-wide column)."""
    esc = html.escape(display.strip())
    html_block = (
        f'<div style="width:100%;box-sizing:border-box;display:flex;justify-content:center;'
        f'margin:{margin_top_px}px 0 0;padding:0;direction:ltr;">'
        f'<div style="width:{box}px;box-sizing:border-box;text-align:center;flex-shrink:0;">'
        f'<span style="font-size:0.9rem;line-height:1.25;font-weight:600;'
        'word-wrap:break-word;overflow-wrap:anywhere;">'
        f"{esc}</span></div></div>"
    )
    st.html(html_block, width="stretch")


st.set_page_config(page_title="GifData Tool", layout="wide")

_ls_mgr = try_local_storage_manager()
if _ls_mgr is not None and not _ls_mgr.ready():
    st.stop()

apply_saved_credentials_or_env(
    _ls_mgr,
    initial_disk_path=_initial_lua_path_str(),
)

st.session_state.setdefault("gif_sprite_picks", [])
st.session_state.setdefault("custom_sprite_gifs", {})
_SPRITE_BROWSE_INITIAL = 50
_SPRITE_BROWSE_STEP = 50
st.session_state.setdefault("sprite_browse_show_count", _SPRITE_BROWSE_INITIAL)
st.session_state.setdefault("sprite_browse_open", False)

_SPRITE_BROWSE_FILTER_SNAP = "_sprite_browse_filter_snap"


@st.dialog("Browse Pokémon sprites", width="large", dismissible=False)
def sprite_browse_dialog() -> None:
    names = _browse_sprite_filenames()

    with st.container(border=True):
        st.markdown("##### Sprite library")
        # Transparent tertiary buttons sit over the GIF; style is scoped to this modal only.
        _tile = 96
        _overlay_h = 108  # covers GIF + st.html wrapper slack so the hit target matches the sprite
        # Narrow hit targets to the sprite width (centered). Full-width invisible buttons +
        # negative margins were spilling into neighboring columns and toggling the wrong Pokémon.
        # Pull the caption up under the art (tertiary + iframe wrappers leave a large gap otherwise).
        _mb_pull = _overlay_h - _tile + 132
        st.markdown(
            "<style>"
            "[data-testid='stDialog'] button[kind='tertiary'],"
            "[role='dialog'] button[kind='tertiary']{"
            f"margin-top:-{_overlay_h}px!important;"
            f"margin-bottom:-{_mb_pull}px!important;"
            f"min-height:{_overlay_h}px!important;"
            f"width:{_tile}px!important;"
            f"max-width:{_tile}px!important;"
            f"min-width:{_tile}px!important;"
            "margin-left:auto!important;"
            "margin-right:auto!important;"
            "display:block!important;"
            "opacity:0.06!important;"
            "position:relative!important;"
            "z-index:10!important;"
            "}"
            "</style>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Search by filename, slug, or display name. **Click the sprite** to toggle selection. "
            "Close with **Done** only — Esc/backdrop are disabled so actions like Load more / Clear all "
            "stay inside this window."
        )
        filter_raw = st.text_input(
            "Search",
            key="sprite_browse_filter_input",
            placeholder="Filename, slug, or display name…",
        )
        filter_q = (filter_raw or "").strip().lower()

        # Reset pagination only when the search string actually changes — not on Load more /
        # fragment reruns (``text_input`` ``on_change`` can fire spuriously inside ``@st.dialog``).
        prev_snap = st.session_state.get(_SPRITE_BROWSE_FILTER_SNAP)
        if prev_snap != filter_q:
            st.session_state[_SPRITE_BROWSE_FILTER_SNAP] = filter_q
            if prev_snap is not None:
                st.session_state.sprite_browse_show_count = _SPRITE_BROWSE_INITIAL

        def _row_matches(fn: str) -> bool:
            if not filter_q:
                return True
            slug = fn.replace(".gif", "").lower()
            if filter_q in slug or filter_q in fn.lower():
                return True
            label = slug_to_default_name(slug).lower()
            return filter_q in label

        filtered = [n for n in names if _row_matches(n)]
        if filter_q and not filtered:
            st.warning("No matches — try a shorter search.")

        COLS = 5
        TILE = _tile
        n_items = len(filtered)
        want = int(st.session_state.sprite_browse_show_count)
        # Only clamp down when the filtered list shrank (e.g. new search); never overwrite with
        # ``shown`` every run — that raced Load more and kept the count stuck at the first page.
        if n_items and want > n_items:
            st.session_state.sprite_browse_show_count = n_items
            want = n_items
        shown = min(want, n_items) if n_items else 0
        slice_items = filtered[:shown] if n_items else []

        if n_items:
            st.caption(
                f"**{shown}** of **{n_items}** "
                f"({'matching search' if filter_q else 'sprites'}) — scroll inside the area below."
            )

        if slice_items:
            with st.container(height=520):
                for row_i in range(0, len(slice_items), COLS):
                    row_fns = slice_items[row_i : row_i + COLS]
                    cols = st.columns(COLS)
                    for ci, fn in enumerate(row_fns):
                        with cols[ci]:
                            # Side gutters center the 96px tile in the grid cell (fixes label skew).
                            _gutter_l, tile_mid, _gutter_r = st.columns([3, 10, 3])
                            with tile_mid:
                                slug = fn.replace(".gif", "")
                                display = slug_to_default_name(slug)
                                blob = _front_sprite_thumbnail_bytes(fn)
                                picked = fn in st.session_state.gif_sprite_picks
                                if blob:
                                    _sprite_browse_gif_only(blob, box=TILE, picked=picked)
                                    st.button(
                                        "\u200b",
                                        key=_sprite_pick_button_key(fn),
                                        type="tertiary",
                                        width=TILE,
                                        on_click=_toggle_sprite_pick,
                                        args=(fn,),
                                    )
                                    _sprite_browse_tile_label(
                                        display,
                                        box=TILE,
                                        margin_top_px=-18,
                                    )
                                else:
                                    _sprite_browse_tile_label(display, box=TILE, margin_top_px=0)
                                    st.caption("No preview")
                                    st.button(
                                        "Select" if not picked else "Deselect",
                                        key=_sprite_pick_button_key(fn),
                                        use_container_width=True,
                                        on_click=_toggle_sprite_pick,
                                        args=(fn,),
                                    )

            if shown < n_items:
                rest = n_items - shown
                # Use ``if st.button`` + ``st.rerun()`` instead of ``on_click``: dialog code runs as a
                # fragment; fragment-scoped reruns can leave ``sprite_browse_show_count`` unsynced so
                # the next batch label/caption never advance. A full app rerun matches session state.
                if st.button(
                    f"Load more — showing {shown}, {rest} left",
                    key="dlg_sprite_load_more",
                    use_container_width=True,
                ):
                    _sprite_browse_load_more(n_items)
                    st.rerun()

        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            st.caption(f"**{len(st.session_state.gif_sprite_picks)}** selected")
        with b2:
            if st.button("Clear all picks", key="dlg_sprite_clear_all"):
                st.session_state.gif_sprite_picks = []
                st.session_state.sprite_browse_open = True
                st.rerun()
        with b3:
            if st.button(
                "Done",
                type="primary",
                use_container_width=True,
                key="dlg_sprite_done",
            ):
                st.session_state.sprite_browse_open = False
                st.rerun()


st.title("GifData sprite importer")
st.caption(
    "Lists Pokémon Showdown animated sprites, previews GIFs, builds spritesheets, "
    "uploads **Image** assets via Roblox Open Cloud, and patches `GifData.lua`."
)

with st.sidebar:
    st.subheader("Roblox Open Cloud")
    api_key = st.text_input(
        "API key (`x-api-key`)",
        type="password",
        key="roblox_api_key",
        help="Creator Hub → Open Cloud → API Keys. Scope: asset write.",
    )
    user_id = st.text_input(
        "Creator user ID",
        key="roblox_user_id",
        help="Numeric Roblox user id for asset ownership.",
    )
    group_id = st.text_input(
        "Creator group ID (optional)",
        key="roblox_group_id",
        help="If set, uploads as group asset instead of user.",
    )
    if _ls_mgr is not None:
        if st.button("Save credentials (this browser)"):
            save_credentials_to_browser(_ls_mgr)
        if st.session_state.get("_cred_saved_banner"):
            st.success(
                "Saved in **this browser** (localStorage). Only you can see these values."
            )
            if st.button("Dismiss", key="dismiss_cred_saved_banner"):
                del st.session_state["_cred_saved_banner"]
                st.rerun()
        st.caption(
            "Stores API key, user/group IDs, and optional disk path in **your browser** "
            "(localStorage). Each visitor only sees their own saved values."
        )
    else:
        st.warning(
            "`streamlit-extras` is missing — install dependencies so credentials "
            "can be saved per browser."
        )

    if should_show_server_dotenv_save_ui():
        with st.expander("Advanced: write `.env` on the server (local dev)"):
            st.caption(
                "Only for running this app on your own machine. "
                "Not shown on Streamlit Community Cloud."
            )
            if st.button("Save to `.env` file next to app"):
                try:
                    merge_dotenv(
                        ENV_FILE,
                        {
                            "ROBLOX_API_KEY": st.session_state.roblox_api_key,
                            "ROBLOX_USER_ID": st.session_state.roblox_user_id,
                            "ROBLOX_GROUP_ID": st.session_state.roblox_group_id,
                            "GIFDATA_LUA_PATH": str(
                                Path(
                                    st.session_state.gifdata_disk_path.strip()
                                ).expanduser()
                            )
                            if st.session_state.get("gifdata_disk_path", "").strip()
                            else "",
                        },
                    )
                    load_dotenv(ENV_FILE, override=True)
                    st.success(f"Wrote `{ENV_FILE.name}` on the server.")
                except OSError as e:
                    st.error(f"Could not write `.env`: {e}")

st.subheader("GifData.lua source")
lua_upload = st.file_uploader(
    "Upload `GifData.lua`",
    type=["lua"],
    help=(
        "Upload the Lua module from your PC. On Streamlit Cloud there is no shared disk — "
        "you download the patched file afterward. UTF-8 encoding."
    ),
)
if lua_upload is not None:
    try:
        st.session_state.gifdata_lua_content = lua_upload.getvalue().decode("utf-8")
        st.session_state.gifdata_lua_upload_name = lua_upload.name
    except UnicodeDecodeError:
        st.error("Uploaded file must be valid UTF-8 text.")
        st.session_state.pop("gifdata_lua_content", None)
        st.session_state.pop("gifdata_lua_upload_name", None)
else:
    st.session_state.pop("gifdata_lua_content", None)
    st.session_state.pop("gifdata_lua_upload_name", None)

if should_show_disk_path_expander():
    with st.expander("Optional: patch a file on disk instead (local runs)", expanded=False):
        st.text_input(
            "Path to GifData.lua on this machine",
            key="gifdata_disk_path",
            placeholder="e.g. C:/Projects/MyGame/GifData.lua",
            help=(
                "If set and the file exists, it is used **only when nothing is uploaded** above. "
                "The patched file is written back to this path. Bundled repo `GifData.lua` is "
                "prefilled below only when that file exists beside `app.py`."
            ),
        )
        _dp = st.session_state.get("gifdata_disk_path", "").strip()
        if _dp:
            _exp = Path(_dp).expanduser()
            if not _exp.is_file():
                st.warning(
                    f"Path not found — will not be used until it exists: `{_exp}`"
                )

_has_upload = bool(st.session_state.get("gifdata_lua_content"))
_dp2 = st.session_state.get("gifdata_disk_path", "").strip()
_has_disk = bool(_dp2 and Path(_dp2).expanduser().is_file())
if not _has_upload and not _has_disk:
    if should_show_disk_path_expander():
        st.info(
            "**Upload** your `GifData.lua` above (recommended), "
            "or open **Optional: patch a file on disk** and set a valid path."
        )
    else:
        st.info("**Upload** your `GifData.lua` above (required on hosted Streamlit).")

col_a, col_b = st.columns([1, 2])

with col_a:
    if st.button("Refresh sprite list (front /ani/)"):
        list_front_gifs.clear()
        _cached_showdown_front_bytes.clear()

    names = list_front_gifs()
    st.metric("Sprites in /ani/", len(names))

    with st.expander("Custom GIF uploads", expanded=False):
        st.caption(
            "Upload an animated GIF and assign **front**, **back**, or shiny variants. "
            "The slug becomes the filename (e.g. `my-boss` → `my-boss.gif`). "
            "That variant uses your file when building; other variants still come from Showdown."
        )
        cu = st.file_uploader("GIF file", type=["gif"], key="custom_gif_uploader_widget")
        cs = st.text_input(
            "Slug (without .gif)",
            key="custom_gif_slug_input",
            placeholder="e.g. my-trainer or pikachu",
            help="Lowercase letters, digits, hyphens. Match an existing Showdown slug to override one variant only.",
        )
        vi = st.selectbox(
            "Variant",
            options=list(range(len(VARIANT_LABELS))),
            format_func=lambda i: VARIANT_LABELS[i][0],
            key="custom_gif_variant_sel",
        )
        b1, b2 = st.columns(2)
        with b1:
            do_add = st.button(
                "Add to library & selection",
                type="primary",
                key="custom_gif_add_btn",
                use_container_width=True,
            )
        with b2:
            do_clear = st.button(
                "Clear all custom GIFs",
                key="custom_gif_clear_btn",
                use_container_width=True,
            )

        if do_clear:
            st.session_state.custom_sprite_gifs = {}
            st.rerun()

        if do_add:
            if cu is None:
                st.warning("Choose a GIF file to upload.")
            else:
                try:
                    slug = _sanitize_custom_slug(cs)
                except ValueError as e:
                    st.error(str(e))
                else:
                    fn = f"{slug}.gif"
                    vk = VARIANT_LABELS[vi][1]
                    cg = st.session_state.setdefault("custom_sprite_gifs", {})
                    cg.setdefault(fn, {})[vk] = cu.getvalue()
                    picks = list(st.session_state.get("gif_sprite_picks") or [])
                    if fn not in picks:
                        st.session_state.gif_sprite_picks = [*picks, fn]
                    st.success(
                        f"`{fn}` saved as **{VARIANT_LABELS[vi][0]}** and added to selection."
                    )
                    st.rerun()

        cg = st.session_state.get("custom_sprite_gifs") or {}
        if cg:
            st.markdown("**Stored custom GIFs**")
            for fn in sorted(cg.keys(), key=str.lower):
                labels = [
                    next(l for l, kk in VARIANT_LABELS if kk == k)
                    for k in sorted(cg[fn].keys())
                ]
                st.caption(f"`{fn}` — " + ", ".join(labels))
            rm = st.selectbox(
                "Remove overrides for file",
                options=["—"] + sorted(cg.keys(), key=str.lower),
                key="custom_gif_remove_sel",
            )
            if st.button("Remove selected file’s uploads", key="custom_gif_remove_btn"):
                if rm != "—" and rm in st.session_state.custom_sprite_gifs:
                    del st.session_state.custom_sprite_gifs[rm]
                    st.rerun()

    if st.button(
        "Browse sprites…",
        type="secondary",
        use_container_width=True,
        help="Opens a modal with square thumbnails, names, and search.",
    ):
        st.session_state.sprite_browse_open = True

    if st.session_state.get("sprite_browse_open"):
        sprite_browse_dialog()

    n_sel = len(st.session_state.gif_sprite_picks)
    st.caption("Selection is stored in this session until you clear it.")
    st.markdown(f"**{n_sel}** Pokémon selected")

    if n_sel:
        if st.button("Clear selection"):
            st.session_state.gif_sprite_picks = []
            st.rerun()
        with st.expander("Selected list", expanded=False):
            for fn in sorted(st.session_state.gif_sprite_picks, key=str.lower):
                s = fn.replace(".gif", "")
                st.text(f"{slug_to_default_name(s)}  ({s})")

    picks = sorted(st.session_state.gif_sprite_picks, key=str.lower)

with col_b:
    if picks:
        if len(picks) == 1:
            slug = picks[0].replace(".gif", "")
            default_key = slug_to_default_name(slug)
            display_name = st.text_input(
                "GifData key name",
                value=default_key,
                help='Lua table key, e.g. Meganium-mega — must match your battle engine naming.',
            )
        else:
            display_name = ""
            st.info(
                "Batch mode uses the same auto capitalization as single-mode defaults "
                "(slug → `Pikachu`, `Meganium-mega`, …). Adjust keys in `GifData.lua` afterward if needed."
            )

        variant_choice = st.multiselect(
            "Variants to build & patch",
            options=[v for _, v in VARIANT_LABELS],
            format_func=lambda k: next(l for l, kk in VARIANT_LABELS if kk == k),
            default=["_FRONT"],
        )

        st.subheader("Previews")
        for row_idx, pick_fn in enumerate(picks):
            if row_idx > 0:
                st.divider()
            slug_label = pick_fn.replace(".gif", "")
            st.markdown(f"**{slug_label}**")
            preview_cols = st.columns(min(4, len(VARIANT_LABELS)))
            for i, (label, vkey) in enumerate(VARIANT_LABELS):
                blob, tried_url, resolved_file = _resolve_sprite_bytes(vkey, pick_fn)
                with preview_cols[i % len(preview_cols)]:
                    st.caption(label.split("(")[0].strip())
                    if blob:
                        _preview_animated_gif(blob, width=120)
                        if tried_url == "(custom upload)":
                            st.caption("Custom GIF")
                        elif resolved_file != pick_fn:
                            st.caption(
                                f"→ {resolved_file.replace('.gif', '')} (fallback)"
                            )
                    else:
                        st.caption("Could not load")
                        if tried_url == "(custom upload)":
                            st.caption("—")
                        else:
                            short = tried_url[-72:] if len(tried_url) > 72 else tried_url
                            st.caption("…" + short if len(tried_url) > 72 else short)

        dry_run = st.checkbox(
            "Dry run (build sheets only — no Roblox upload, no file write)",
            value=False,
        )

        if st.button("Build spritesheets, upload, patch GifData.lua", type="primary"):
            lua_text: str | None = None
            lua_disk_path: Path | None = None
            if st.session_state.get("gifdata_lua_content"):
                lua_text = str(st.session_state.gifdata_lua_content)
            else:
                _dp = st.session_state.get("gifdata_disk_path", "").strip()
                if _dp:
                    _p = Path(_dp).expanduser()
                    if _p.is_file():
                        lua_text = _p.read_text(encoding="utf-8")
                        lua_disk_path = _p

            if len(picks) == 1 and not display_name.strip():
                st.error("Set a GifData key name.")
            elif not variant_choice:
                st.error("Select at least one variant.")
            elif lua_text is None:
                st.error(
                    "Upload **GifData.lua** above, or set a valid path under "
                    "**Optional: patch a file on disk**."
                )
            elif not dry_run and not _env("ROBLOX_API_KEY", api_key):
                st.error("Configure Roblox API key or enable dry run.")
            elif (
                not dry_run
                and not _env("ROBLOX_GROUP_ID", group_id)
                and not _env("ROBLOX_USER_ID", user_id)
            ):
                st.error("Set creator user ID or group ID for uploads.")
            else:
                if len(picks) == 1:
                    jobs = [(picks[0], display_name.strip())]
                else:
                    jobs = [
                        (fn, slug_to_default_name(fn.replace(".gif", "")))
                        for fn in picks
                    ]

                updates: list[tuple[str, str, PackResult, list[int]]] = []
                errors: list[str] = []
                progress = st.progress(0.0)
                step = 0
                total_steps = len(jobs) * len(variant_choice)

                for pick_fn, lua_key in jobs:
                    slug_disp = pick_fn.replace(".gif", "")
                    for vk in variant_choice:
                        label = next(l for l, kk in VARIANT_LABELS if kk == vk)
                        blob, _, resolved_file = _resolve_sprite_bytes(vk, pick_fn)
                        if not blob:
                            errors.append(
                                f"`{slug_disp}` · {label}: no GIF on Showdown"
                                + (
                                    f" (tried fallbacks ending with `{resolved_file}`)"
                                    if resolved_file != pick_fn
                                    else ""
                                )
                            )
                            step += 1
                            progress.progress(step / max(total_steps, 1))
                            continue
                        try:
                            pack = pack_gif_from_bytes(blob)
                        except Exception as e:
                            errors.append(
                                f"`{slug_disp}` · {label}: pack failed — {e}"
                            )
                            step += 1
                            progress.progress(step / max(total_steps, 1))
                            continue

                        aids: list[int] = []
                        if dry_run:
                            aids = [0] * len(pack.sheets)
                        else:
                            ak = _env("ROBLOX_API_KEY", api_key)
                            uid = _env("ROBLOX_USER_ID", user_id)
                            gid = _env("ROBLOX_GROUP_ID", group_id)
                            for si, sheet in enumerate(pack.sheets):
                                suffix = f"{vk}_{si}" if len(pack.sheets) > 1 else vk
                                variant_tag = vk.lstrip("_")
                                roblox_name = (
                                    f"_{variant_tag}_{si}"
                                    if len(pack.sheets) > 1
                                    else f"_{variant_tag}"
                                )[:100]
                                aid_str = upload_image_asset(
                                    sheet.png_bytes,
                                    display_name=roblox_name,
                                    description=(
                                        f"Sheet ({suffix})"
                                    )[:1000],
                                    api_key=ak,
                                    creator_user_id=uid if not gid else None,
                                    creator_group_id=gid if gid else None,
                                )
                                aids.append(int(aid_str))

                        updates.append((vk, lua_key, pack, aids))
                        step += 1
                        progress.progress(step / max(total_steps, 1))

                if errors:
                    for e in errors:
                        st.warning(e)

                if updates:
                    new_text = upsert_multi_section(lua_text, updates)
                    preview_lines = [
                        format_entry_line(u[1], u[2], u[3]) for u in updates
                    ]
                    st.code("\n".join(preview_lines), language="lua")
                    if lua_disk_path is None:
                        out_fn = (
                            st.session_state.get("gifdata_lua_upload_name") or "GifData.lua"
                        )
                        st.download_button(
                            label="Download patched GifData.lua",
                            data=new_text.encode("utf-8"),
                            file_name=out_fn,
                            mime="text/plain",
                            key="download_patched_gifdata_lua",
                        )
                        if dry_run:
                            st.success(
                                "Dry run OK — download includes placeholder asset ids **0**; "
                                f"**{len(updates)}** row(s) ({len(jobs)} Pokémon × variants)."
                            )
                        else:
                            st.success(
                                f"Patched in memory — **{len(updates)}** row(s) "
                                f"({len(jobs)} Pokémon × variants). Download and replace your file."
                            )
                    else:
                        if lua_disk_path is None:
                            st.error("Internal error: lost disk path for Lua file.")
                        elif dry_run:
                            st.success(
                                "Dry run OK — disk file not modified (asset ids shown as 0 above)."
                            )
                        else:
                            lua_disk_path.write_text(new_text, encoding="utf-8")
                            st.success(
                                f"Patched `{lua_disk_path}` — **{len(updates)}** row(s) "
                                f"({len(jobs)} Pokémon × variants)."
                            )

    else:
        st.info(
            "Select Pokémon from **Browse sprites** or add **Custom GIF uploads** (left column)."
        )
