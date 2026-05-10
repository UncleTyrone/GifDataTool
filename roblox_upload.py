"""Upload PNG spritesheets to Roblox via Open Cloud Assets API (Image assets)."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

ASSETS_URL = "https://apis.roblox.com/assets/v1/assets"
OPERATIONS_URL = "https://apis.roblox.com/assets/v1/operations"


def upload_image_asset(
    png_bytes: bytes,
    display_name: str,
    description: str,
    api_key: str,
    creator_user_id: Optional[str] = None,
    creator_group_id: Optional[str] = None,
    timeout_sec: float = 180.0,
    poll_interval_sec: float = 1.5,
) -> str:
    """
    Upload a PNG as an Image asset. Returns numeric asset id string.
    Pass either creator_user_id or creator_group_id (not both required — Roblox uses one creator).
    """
    if not creator_user_id and not creator_group_id:
        raise ValueError("Provide creator_user_id or creator_group_id")

    creator: dict[str, str] = {}
    if creator_group_id:
        creator["groupId"] = str(creator_group_id)
    if creator_user_id:
        creator["userId"] = str(creator_user_id)

    request_body: dict[str, Any] = {
        "assetType": "Image",
        "displayName": display_name[:100],
        "description": description[:1000],
        "creationContext": {"creator": creator},
    }

    headers = {"x-api-key": api_key}
    files = {
        "request": (None, json.dumps(request_body), "application/json"),
        "fileContent": ("sheet.png", png_bytes, "image/png"),
    }

    r = requests.post(ASSETS_URL, headers=headers, files=files, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Create asset failed {r.status_code}: {r.text}")

    data = r.json()
    op_path = data.get("path") or data.get("operationId")
    if not op_path:
        aid = (
            data.get("assetId")
            or data.get("asset_id")
            or (data.get("response") or {}).get("assetId")
        )
        if aid:
            return str(aid)
        raise RuntimeError(f"Unexpected create response: {data}")

    op_id = op_path.split("/")[-1]
    deadline = time.monotonic() + timeout_sec

    while time.monotonic() < deadline:
        gr = requests.get(f"{OPERATIONS_URL}/{op_id}", headers=headers, timeout=60)
        gr.raise_for_status()
        op = gr.json()
        if op.get("done"):
            err = op.get("error")
            if err:
                raise RuntimeError(f"Roblox operation error: {err}")
            resp = op.get("response") or {}
            if isinstance(resp, dict):
                aid = (
                    resp.get("assetId")
                    or resp.get("asset_id")
                    or resp.get("Asset", {}).get("assetId")
                )
                if aid:
                    return str(aid)
            raise RuntimeError(f"No assetId in completed operation: {op}")
        time.sleep(poll_interval_sec)

    raise TimeoutError(f"Roblox asset upload timed out after {timeout_sec}s")


def upload_many(
    sheets: list[tuple[bytes, str]],
    api_key: str,
    creator_user_id: Optional[str],
    creator_group_id: Optional[str],
    base_display_name: str,
) -> list[str]:
    """Upload multiple PNGs; display names get a numeric suffix."""
    ids: list[str] = []
    for i, (png, suffix) in enumerate(sheets):
        label = f"{base_display_name}_{suffix}" if suffix else base_display_name
        aid = upload_image_asset(
            png,
            display_name=label[:100],
            description=f"Sheet {label}",
            api_key=api_key,
            creator_user_id=creator_user_id,
            creator_group_id=creator_group_id,
        )
        ids.append(aid)
    return ids
