"""
GifData helper: browse Showdown sprites, build spritesheets, upload Image assets to Roblox,
and patch GifData.lua.

Run: streamlit run app.py
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from credentials_browser import (
    apply_saved_credentials_or_env,
    save_credentials_to_browser,
    should_show_server_dotenv_save_ui,
    try_local_storage_manager,
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


@st.cache_data(ttl=3600, show_spinner="Fetching Showdown /ani/ listing…")
def list_front_gifs():
    return fetch_gif_filenames("_FRONT")


def _env(key: str, ui_val: str) -> str:
    v = (ui_val or "").strip() or os.environ.get(key, "")
    return v.strip()


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
    components.html(html, height=iframe_h)


st.set_page_config(page_title="GifData Tool", layout="wide")

_ls_mgr = try_local_storage_manager()
if _ls_mgr is not None and not _ls_mgr.ready():
    st.stop()

apply_saved_credentials_or_env(
    _ls_mgr,
    initial_disk_path=_initial_lua_path_str(),
)

if st.session_state.pop("_cred_saved_flash", False):
    st.success(
        "Saved credentials in **this browser** (localStorage). "
        "They stay on your device and are not shared with other visitors."
    )

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
            st.warning(f"Path not found — will not be used until it exists: `{_exp}`")

_has_upload = bool(st.session_state.get("gifdata_lua_content"))
_dp2 = st.session_state.get("gifdata_disk_path", "").strip()
_has_disk = bool(_dp2 and Path(_dp2).expanduser().is_file())
if not _has_upload and not _has_disk:
    st.info(
        "**Upload** your `GifData.lua` above (recommended), "
        "or open **Optional: patch a file on disk** and set a valid path."
    )

col_a, col_b = st.columns([1, 2])

with col_a:
    if st.button("Refresh sprite list (front /ani/)"):
        list_front_gifs.clear()

    names = list_front_gifs()
    st.metric("Sprites in /ani/", len(names))

    filter_q = st.text_input("Search filename", "").strip().lower()
    filtered = [n for n in names if filter_q in n.lower()] if filter_q else names
    choices = filtered if filtered else names

    picks = st.multiselect(
        "Pokémon (GIF slug)",
        choices,
        format_func=lambda x: x.replace(".gif", ""),
        help="Select one or many. Hold Ctrl (Windows) or ⌘ (Mac) to choose multiple.",
    )

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
                blob, tried_url, resolved_file = fetch_sprite_bytes_for_variant(
                    vkey, pick_fn
                )
                with preview_cols[i % len(preview_cols)]:
                    st.caption(label.split("(")[0].strip())
                    if blob:
                        _preview_animated_gif(blob, width=120)
                        if resolved_file != pick_fn:
                            st.caption(
                                f"→ {resolved_file.replace('.gif', '')} (fallback)"
                            )
                    else:
                        st.caption("Could not load")
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
                        blob, _, resolved_file = fetch_sprite_bytes_for_variant(
                            vk, pick_fn
                        )
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
        st.info("Select one or more Pokémon from the list (left column).")
