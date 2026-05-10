"""Store Roblox Open Cloud credentials and optional disk path in the user's browser (localStorage)."""

from __future__ import annotations

import os
from typing import Any


def likely_streamlit_cloud_or_hosted() -> bool:
    """Best-effort detection of hosted Streamlit / cloud runtimes (never rely on this alone)."""
    e = os.environ
    if str(e.get("STREAMLIT_COMMUNITY_CLOUD", "")).lower() in ("1", "true", "yes"):
        return True
    if str(e.get("STREAMLIT_CLOUD_DEPLOYMENT", "")).lower() in ("1", "true", "yes"):
        return True
    if e.get("STREAMLIT_CLOUD_REPOSITORY") or e.get("STREAMLIT_CLOUD_REPOSITORY_BRANCH"):
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
    if e.get("K_SERVICE") or e.get("K_REVISION"):  # Cloud Run / similar
        return True
    if str(e.get("CODESPACES", "")).lower() == "true":
        return True
    return False


def hide_local_filesystem_ui() -> bool:
    """
    Hide server `.env` writer + optional disk path when app is public / hosted.

    Set in Streamlit **Secrets** (recommended for Community Cloud)::

        GIFDATA_PUBLIC_DEPLOY = "1"

    Optional: ``GIFDATA_SHOW_LOCAL_DISK_UI = "1"`` forces those panels on (debug).
    """
    if os.environ.get("GIFDATA_SHOW_LOCAL_DISK_UI", "").lower() in ("1", "true", "yes"):
        return False
    if os.environ.get("GIFDATA_PUBLIC_DEPLOY", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("GIFDATA_HIDE_LOCAL_DISK_UI", "").lower() in ("1", "true", "yes"):
        return True
    return likely_streamlit_cloud_or_hosted()


def should_show_server_dotenv_save_ui() -> bool:
    return not hide_local_filesystem_ui()


def should_show_disk_path_expander() -> bool:
    return not hide_local_filesystem_ui()


def use_server_environ_for_roblox_defaults() -> bool:
    """
    When False, ``ROBLOX_*`` from the Python process environment are **not** copied into
    session/widget defaults and must not be used as a fallback for uploads.

    This prevents a single ``.env`` file or Streamlit Secrets from acting as global
    credentials for every browser session on hosted/multi-user Streamlit (one process).

    Opt in explicitly with ``GIFDATA_USE_SERVER_ENV=1`` (trusted single-tenant server only).
    Opt out on a private VPS with ``GIFDATA_ISOLATE_SESSION_CREDENTIALS=1``.
    """
    o = os.environ.get("GIFDATA_USE_SERVER_ENV", "").strip().lower()
    if o in ("1", "true", "yes", "on"):
        return True
    if o in ("0", "false", "no", "off"):
        return False
    if os.environ.get("GIFDATA_ISOLATE_SESSION_CREDENTIALS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    if os.environ.get("GIFDATA_PUBLIC_DEPLOY", "").strip().lower() in ("1", "true", "yes"):
        return False
    if hide_local_filesystem_ui():
        return False
    if likely_streamlit_cloud_or_hosted():
        return False
    return True


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
        use_srv = use_server_environ_for_roblox_defaults()
        if "roblox_api_key" not in st.session_state:
            st.session_state.roblox_api_key = (
                os.environ.get("ROBLOX_API_KEY", "") if use_srv else ""
            )
        if "roblox_user_id" not in st.session_state:
            st.session_state.roblox_user_id = (
                os.environ.get("ROBLOX_USER_ID", "") if use_srv else ""
            )
        if "roblox_group_id" not in st.session_state:
            st.session_state.roblox_group_id = (
                os.environ.get("ROBLOX_GROUP_ID", "") if use_srv else ""
            )
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
    # Sidebar banner survives the rerun from localStorage sync (unlike one-shot st.success).
    st.session_state["_cred_saved_banner"] = True
    st.rerun()
