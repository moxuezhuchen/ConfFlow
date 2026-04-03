#!/usr/bin/env python3

"""XYZ comment metadata and CID helpers."""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "ensure_conformer_cids",
    "parse_comment_metadata",
    "upsert_comment_kv",
]

_IDENTIFIER_METADATA_KEYS = {"CID"}
_LEGACY_NUMERIC_CID_RE = re.compile(r"^\d+(?:\.0+)?$")


def _is_supported_cid_value(value: Any) -> bool:
    """Return True only for current-generation CID strings."""
    if not isinstance(value, str):
        return False
    cid = value.strip()
    return bool(cid) and _LEGACY_NUMERIC_CID_RE.fullmatch(cid) is None


def upsert_comment_kv(comment: str, key: str, value: Any) -> str:
    """Update or insert a key=value pair in a comment line."""
    comment = (comment or "").strip()
    key = str(key)
    val_str = str(value)

    pattern = re.compile(rf"(?P<prefix>^|[\s|,])(?P<k>{re.escape(key)})\s*=\s*(?P<v>[^\s|,]+)")
    match = pattern.search(comment)
    if not match:
        if not comment:
            return f"{key}={val_str}"
        return f"{comment} | {key}={val_str}"

    start, end = match.span("v")
    return comment[:start] + val_str + comment[end:]


def parse_comment_metadata(comment: str) -> dict[str, Any]:
    """Parse key=value metadata from an XYZ comment line."""
    meta: dict[str, Any] = {}
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s|,]+)", comment or ""):
        key, value = match.group(1), match.group(2)
        if key in _IDENTIFIER_METADATA_KEYS:
            meta[key] = value
            continue
        try:
            numeric_value = float(value)
            meta[key] = numeric_value
            if key == "Energy":
                meta["E"] = numeric_value
        except (ValueError, TypeError):
            meta[key] = value
    return meta


def _ensure_metadata_dict(conf: dict[str, Any]) -> dict[str, Any]:
    meta = conf.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        conf["metadata"] = meta
    return meta


def _extract_existing_cid(conf: dict[str, Any]) -> str | None:
    meta = _ensure_metadata_dict(conf)

    existing_meta_cid = meta.get("CID")
    if _is_supported_cid_value(existing_meta_cid):
        return str(existing_meta_cid).strip()

    comment_meta = parse_comment_metadata(conf.get("comment", ""))
    comment_cid = comment_meta.get("CID")
    if not _is_supported_cid_value(comment_cid):
        return None

    cid_str = str(comment_cid).strip()
    meta["CID"] = cid_str
    return cid_str


def _comment_cid_matches(conf: dict[str, Any], cid: str) -> bool:
    comment_cid = parse_comment_metadata(conf.get("comment", "")).get("CID")
    return comment_cid == cid


def ensure_conformer_cids(
    conformers: list[dict[str, Any]],
    *,
    prefix: str = "A",
    start: int = 1,
    width: int = 6,
) -> list[dict[str, Any]]:
    """Ensure every conformer has a current-generation CID."""
    used_cids = {
        cid
        for cid in (_extract_existing_cid(conf) for conf in conformers)
        if cid is not None and cid != ""
    }
    next_id = start

    for conf in conformers:
        existing_cid = _extract_existing_cid(conf)
        if existing_cid:
            if not _comment_cid_matches(conf, existing_cid):
                conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", existing_cid)
            continue

        meta = _ensure_metadata_dict(conf)
        new_cid = f"{prefix}{next_id:0{width}d}"
        while new_cid in used_cids:
            next_id += 1
            new_cid = f"{prefix}{next_id:0{width}d}"
        next_id += 1
        used_cids.add(new_cid)
        meta["CID"] = new_cid
        conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", new_cid)

    return conformers


def xyz_needs_cid_rewrite(conformers: list[dict[str, Any]]) -> bool:
    """Return True when any frame is missing a current-generation CID."""
    for conf in conformers:
        meta = conf.get("metadata") or {}
        cid = meta.get("CID")
        if not _is_supported_cid_value(cid):
            return True
        if parse_comment_metadata(conf.get("comment", "")).get("CID") != str(cid).strip():
            return True
    return False
