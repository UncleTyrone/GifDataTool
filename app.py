"""
GifData helper: browse Showdown sprites, build spritesheets, upload Image assets to Roblox,
and patch GifData.lua.

Run: streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from gifdata_patch import format_entry_line, upsert_multi_section
from roblox_upload import upload_image_asset
from showdown import fetch_gif_filenames, fetch_sprite_bytes_for_variant, slug_to_default_name
from spritesheet import PackResult, pack_gif_from_bytes

load_dotenv()

VARIANT_LABELS = [
    ("Front (ani)", "_FRONT"),
    ("Back (ani-back)", "_BACK"),
    ("Shiny front (ani-shiny)", "_SHINY_FRONT"),
    ("Shiny back (ani-back-shiny)", "_SHINY_BACK"),
]

HERE = Path(__file__).resolve().parent
DEFAULT_LUA = HERE / "GifData.lua"


@st.cache_data(ttl=3600, show_spinner="Fetching Showdown /ani/ listing…")
def list_front_gifs():
    return fetch_gif_filenames("_FRONT")


def _env(key: str, ui_val: str) -> str:
    v = (ui_val or "").strip() or os.environ.get(key, "")
    return v.strip()


st.set_page_config(page_title="GifData Tool", layout="wide")
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
        value=os.environ.get("ROBLOX_API_KEY", ""),
        help="Creator Hub → Open Cloud → API Keys. Scope: asset write.",
    )
    user_id = st.text_input(
        "Creator user ID",
        value=os.environ.get("ROBLOX_USER_ID", ""),
        help="Numeric Roblox user id for asset ownership.",
    )
    group_id = st.text_input(
        "Creator group ID (optional)",
        value=os.environ.get("ROBLOX_GROUP_ID", ""),
        help="If set, uploads as group asset instead of user.",
    )

lua_path = st.text_input(
    "GifData.lua path",
    value=str(DEFAULT_LUA),
    help="File to patch with new animation entries.",
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

    pick = st.selectbox(
        "Pokémon (GIF slug)",
        choices,
        format_func=lambda x: x.replace(".gif", ""),
        index=0,
    )

with col_b:
    if pick:
        slug = pick.replace(".gif", "")
        default_key = slug_to_default_name(slug)
        display_name = st.text_input(
            "GifData key name",
            value=default_key,
            help='Lua table key, e.g. Meganium-mega — must match your battle engine naming.',
        )

        variant_choice = st.multiselect(
            "Variants to build & patch",
            options=[v for _, v in VARIANT_LABELS],
            format_func=lambda k: next(l for l, kk in VARIANT_LABELS if kk == k),
            default=["_FRONT"],
        )

        preview_cols = st.columns(min(4, len(VARIANT_LABELS)))
        for i, (label, vkey) in enumerate(VARIANT_LABELS):
            blob, tried_url, resolved_file = fetch_sprite_bytes_for_variant(vkey, pick)
            with preview_cols[i % len(preview_cols)]:
                st.caption(label.split("(")[0].strip())
                if blob:
                    st.image(blob, width=120)
                    if resolved_file != pick:
                        st.caption(f"→ {resolved_file.replace('.gif', '')} (fallback)")
                else:
                    st.caption("Could not load")
                    short = tried_url[-72:] if len(tried_url) > 72 else tried_url
                    st.caption("…" + short if len(tried_url) > 72 else short)

        dry_run = st.checkbox(
            "Dry run (build sheets only — no Roblox upload, no file write)",
            value=False,
        )

        if st.button("Build spritesheets, upload, patch GifData.lua", type="primary"):
            if not display_name.strip():
                st.error("Set a GifData key name.")
            elif not variant_choice:
                st.error("Select at least one variant.")
            elif not dry_run and not _env("ROBLOX_API_KEY", api_key):
                st.error("Configure Roblox API key or enable dry run.")
            elif (
                not dry_run
                and not _env("ROBLOX_GROUP_ID", group_id)
                and not _env("ROBLOX_USER_ID", user_id)
            ):
                st.error("Set creator user ID or group ID for uploads.")
            else:
                updates: list[tuple[str, str, PackResult, list[int]]] = []
                errors: list[str] = []
                progress = st.progress(0.0)
                step = 0
                total_steps = len(variant_choice)

                for vk in variant_choice:
                    label = next(l for l, kk in VARIANT_LABELS if kk == vk)
                    blob, _, resolved_file = fetch_sprite_bytes_for_variant(vk, pick)
                    if not blob:
                        errors.append(
                            f"{label}: no GIF on Showdown for `{pick}`"
                            + (
                                f" (tried fallbacks ending with `{resolved_file}`)"
                                if resolved_file != pick
                                else ""
                            )
                        )
                        step += 1
                        progress.progress(step / max(total_steps, 1))
                        continue
                    try:
                        pack = pack_gif_from_bytes(blob)
                    except Exception as e:
                        errors.append(f"{label}: pack failed — {e}")
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

                    updates.append((vk, display_name.strip(), pack, aids))
                    step += 1
                    progress.progress(step / max(total_steps, 1))

                if errors:
                    for e in errors:
                        st.warning(e)

                if updates:
                    path = Path(lua_path)
                    text = path.read_text(encoding="utf-8")
                    new_text = upsert_multi_section(text, updates)
                    preview_lines = [
                        format_entry_line(display_name.strip(), u[2], u[3])
                        for u in updates
                    ]
                    st.code("\n".join(preview_lines), language="lua")
                    if dry_run:
                        st.success(
                            "Dry run OK — spritesheet metrics shown above (asset ids are 0 placeholders)."
                        )
                    else:
                        path.write_text(new_text, encoding="utf-8")
                        st.success(f"Patched `{path}` with {len(updates)} variant(s).")

    else:
        st.info("Pick a sprite from the list.")
