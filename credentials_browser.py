"""Store Roblox Open Cloud credentials and optional disk path in the user's browser (localStorage)."""

from __future__ import annotations

import os
from typing import Any


def likely_streamlit_cloud_or_hosted() -> bool:
    """Best-effort detection of Streamlit Community Cloud / Snowflake-hosted runtimes."""
    e = os.environ
    if str(e.get("STREAMLIT_COMMUNITY_CLOUD", "")).lower() in ("1", "true", "yes"):
        return True
    if str(e.get("STREAMLIT_CLOUD_DEPLOYMENT", "")).lower() in ("1", "true", "yes"):
        return True
    for var in (
        "STREAMLIT_SERVER_BASE_URL",
        "STREAMLIT_BROWSER_SERVER_URL",
        "STREAMLIT_SERVER_URL",
    ):
        url = (e.get(var) or "").lower()
        if "streamlit.app" in url or "snowflakecomputing" in url:
            return True
    if e.get("SNOWFLAKE_ACCOUNT"):
        return True
    return False


def should_show_server_dotenv_save_ui() -> bool:
    """
    Server-side `.env` writing only makes sense for local `streamlit run`.
    Hide it on Streamlit Cloud unless explicitly forced (debug).
    """
    if os.environ.get("GIFDATA_SHOW_DOTENV_SAVE", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("GIFDATA_HIDE_DOTENV_SAVE", "").lower() in ("1", "true", "yes"):
        return False
    return not likely_streamlit_cloud_or_hosted()

# Single JSON blob in localStorage (namespaced per Streamlit app URL path).
CREDENTIALS_STORAGE_KEY = "gifdata_credentials_v1"


def try_local_storage_manager() -> Any | None:
    try:
        from streamlit_extras.local_storage_manager import local_storage_manager
    except ImportError:
        return None
    return local_storage_manager(key="gifdata_ls_credentials_v1")


def apply_saved_credentials_or_env(
    ls: Any | None,
    *,
    initial_disk_path: str,
) -> None:
    """Once per session: prefer saved browser bundle, else environment defaults."""
    import streamlit as st

    if st.session_state.get("_gifdata_cred_applied"):
        return

    def _from_env() -> None:
        if "roblox_api_key" not in st.session_state:
            st.session_state.roblox_api_key = os.environ.get("ROBLOX_API_KEY", "")
        if "roblox_user_id" not in st.session_state:
            st.session_state.roblox_user_id = os.environ.get("ROBLOX_USER_ID", "")
        if "roblox_group_id" not in st.session_state:
            st.session_state.roblox_group_id = os.environ.get("ROBLOX_GROUP_ID", "")
        if "gifdata_disk_path" not in st.session_state:
            st.session_state.gifdata_disk_path = initial_disk_path

    if ls is not None and ls.ready():
        bundle = ls.get(CREDENTIALS_STORAGE_KEY)
        if isinstance(bundle, dict) and (
            (bundle.get("ROBLOX_API_KEY") or "").strip()
            or (bundle.get("ROBLOX_USER_ID") or "").strip()
            or (bundle.get("ROBLOX_GROUP_ID") or "").strip()
            or (bundle.get("GIFDATA_LUA_PATH") or "").strip()
        ):
            st.session_state.roblox_api_key = str(bundle.get("ROBLOX_API_KEY") or "")
            st.session_state.roblox_user_id = str(bundle.get("ROBLOX_USER_ID") or "")
            st.session_state.roblox_group_id = str(bundle.get("ROBLOX_GROUP_ID") or "")
            dp = str(bundle.get("GIFDATA_LUA_PATH") or "").strip()
            st.session_state.gifdata_disk_path = dp if dp else initial_disk_path
        else:
            _from_env()
    else:
        _from_env()

    st.session_state._gifdata_cred_applied = True


def save_credentials_to_browser(ls: Any) -> None:
    """Persist current sidebar values into this browser's localStorage."""
    import streamlit as st

    ls[CREDENTIALS_STORAGE_KEY] = {
        "ROBLOX_API_KEY": st.session_state.get("roblox_api_key", ""),
        "ROBLOX_USER_ID": st.session_state.get("roblox_user_id", ""),
        "ROBLOX_GROUP_ID": st.session_state.get("roblox_group_id", ""),
        "GIFDATA_LUA_PATH": st.session_state.get("gifdata_disk_path", ""),
    }
    st.session_state["_cred_saved_flash"] = True
    st.rerun()
