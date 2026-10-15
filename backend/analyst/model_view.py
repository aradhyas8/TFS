"""Bounded model-facing views of stored artifacts.

The stored artifact stays complete for persistence and the UI. The chat model gets a
deterministic summary, can read one section at a time, and every tool result is fitted to
the remaining context budget by omitting whole subtrees behind retrievable section paths.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from .persistence import DecimalEncoder

SUMMARIZED_KINDS = {"analysis_result", "calculation_result"}
# Model-written case inputs and step-by-step paths; the computed figures stay in the summary.
DETAIL_KEYS = {"judgment", "judgments", "path", "components", "contributing_funds"}
# A smaller result is clearer in full; past this, per-holding rows and case paths dominate.
SUMMARY_THRESHOLD = 12_000
LIST_LIMIT = 20
TOP_POSITIONS = 10
SUMMARY_NOTE = ("This is a summary of the stored artifact. Call get_artifact with this artifact_id and an "
                "omitted section path to read details; use offset and limit for long lists.")
FIT_NOTE = ("Some sections were omitted to fit the context budget. For an artifact, call get_artifact with "
            "an omitted section path (offset and limit page long lists); otherwise narrow the request.")


def _size(value: Any) -> int:
    return len(json.dumps(value, cls=DecimalEncoder, ensure_ascii=False))


def _dotted(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def outline(value: Any, path: tuple[str | int, ...]) -> dict[str, Any]:
    """A pointer to an omitted subtree: where it is and what shape it has."""
    shape: dict[str, Any] = {"omitted": _dotted(path), "chars": _size(value)}
    if isinstance(value, dict):
        shape["keys"] = list(value)[:LIST_LIMIT]
    elif isinstance(value, list):
        shape["items"] = len(value)
    return shape


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("-Infinity")


def _rank(item: Any) -> tuple[int, Decimal]:
    """Breaches first, then unknown checks, then the largest weight."""
    if not isinstance(item, dict):
        return (0, Decimal(0))
    status = {"breached": 0, "unknown": 1}.get(str(item.get("status")), 2)
    weight = next((item[key] for key in ("total_weight", "current_weight", "weight") if item.get(key) is not None), None)
    return (status, -_number(weight))


def _positions(rows: list[dict[str, Any]], path: tuple[str | int, ...]) -> dict[str, Any]:
    def row(item: dict[str, Any]) -> dict[str, Any]:
        supplied = item.get("supplied") or {}
        return {"id": supplied.get("id"), "ticker": supplied.get("ticker"), "listing": supplied.get("listing"),
                "account_id": supplied.get("account_id"), "kind": supplied.get("kind"),
                "shares": supplied.get("shares"), "value": item.get("value"), "weight": item.get("weight"),
                "identity_status": item.get("identity_status"), "issues": item.get("issues") or []}
    ranked = sorted(rows, key=lambda item: -_number(item.get("weight")))
    flagged = [item for item in rows if item.get("issues") or item.get("identity_status") == "unresolved"]
    return {"count": len(rows), "top_by_weight": [row(item) for item in ranked[:TOP_POSITIONS]],
            "with_issues": [(item.get("supplied") or {}).get("id") for item in flagged][:LIST_LIMIT],
            "with_issues_count": len(flagged), "section": _dotted(path)}


def _compact(value: Any, path: tuple[str | int, ...]) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            where = (*path, key)
            if key in DETAIL_KEYS and item:
                compact[key] = outline(item, where)
            elif key == "positions" and isinstance(item, list) and item and isinstance(item[0], dict) and "supplied" in item[0]:
                compact[key] = _positions(item, where)
            else:
                compact[key] = _compact(item, where)
        return compact
    if isinstance(value, list):
        order = sorted(range(len(value)), key=lambda i: _rank(value[i])) if len(value) > LIST_LIMIT else range(len(value))
        items = [_compact(value[i], (*path, i)) for i in order]
        if len(value) > LIST_LIMIT:
            return {"count": len(value), "top": items[:LIST_LIMIT], "section": _dotted(path)}
        return items
    return value


def summarize(kind: str, payload: Any) -> Any:
    """Deterministic compact payload for a large workflow or calculation result; others pass through."""
    return _compact(payload, ()) if kind in SUMMARIZED_KINDS and _size(payload) > SUMMARY_THRESHOLD else payload


def select(payload: Any, section: str) -> Any:
    """Read one dotted section of a model projection."""
    node = payload
    for part in section.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise ValueError("Unknown artifact section.")
    return node


def fit(result: dict[str, Any], limit: int, size: Callable[[Any], int]) -> dict[str, Any] | None:
    """Omit the smallest sufficient subtrees until `size(result) <= limit`; None if impossible.

    Envelope references are kept, and omitted paths are relative to the artifact payload so
    they can be passed back to get_artifact as a section.
    """
    result = copy.deepcopy(result)
    envelope = isinstance(result.get("reference"), dict) and "payload" in result
    root: tuple[str | int, ...] = ("payload",) if envelope else ()
    prefix: tuple[str | int, ...] = tuple(str(result.get("section")).split(".")) if envelope and result.get("section") else ()
    offset = result.get("offset") if envelope and isinstance(result.get("offset"), int) else 0
    while size(result) > limit:
        excess = size(result) - limit
        path = root
        node = _at(result, path)
        while True:
            children = list(node.items()) if isinstance(node, dict) else list(enumerate(node)) if isinstance(node, list) else []
            # The fit note itself and envelope metadata are never omitted.
            children = [(key, child) for key, child in children
                        if not (path == () and key in {"retrieval", "reference", "section", "count", "offset"})]
            if not children:
                break
            key, child = max(children, key=lambda pair: _size(pair[1]))
            if path != root and _size(child) < excess:
                break
            # At the root, always descend: several smaller omissions can still fit.
            path = (*path, key)
            node = child
            if _size(child) < excess:
                break
        relative = path[len(root):]
        if offset and relative and isinstance(relative[0], int):
            relative = (relative[0] + offset, *relative[1:])
        replacement = outline(node, prefix + relative)
        if path == root or _size(replacement) >= _size(node):
            return None
        _set(result, path, replacement)
        result["retrieval"] = FIT_NOTE
    return result


def _at(value: Any, path: tuple[str | int, ...]) -> Any:
    for part in path:
        value = value[part]
    return value


def _set(value: Any, path: tuple[str | int, ...], replacement: Any) -> None:
    _at(value, path[:-1])[path[-1]] = replacement


def model_envelope(envelope: dict[str, Any], section: str | None = None,
                   offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
    """The model-facing form of an artifact envelope: its summary, or one requested section."""
    payload = envelope["payload"]
    if section:
        node = select(payload, section)
        page: dict[str, int] = {}
        if isinstance(node, list) and (offset is not None or limit is not None):
            page = {"count": len(node), "offset": offset or 0}
            node = node[page["offset"]:page["offset"] + limit if limit else None]
        return {"reference": envelope["reference"], "section": section, **page, "payload": node}
    summary = summarize(envelope["reference"]["kind"], payload)
    if summary == payload:
        return envelope
    return {**envelope, "payload": summary, "retrieval": SUMMARY_NOTE}
