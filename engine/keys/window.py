"""Quarterly key rotation with a policy-defined overlap window.

A store is a plain dictionary with three fields:

    policy     {"overlap_days": int}
    keys       list of key records
    active_id  the key most recently activated, or None

A key record is a plain dictionary:

    key_id       non-empty string
    created_at   timestamp string
    effective_at timestamp string
    activated_at timestamp string or None
    retired_at   timestamp string or None

Timestamps are accepted in the forms YYYY-M-D and YYYY-M-DTH:M:S.  Callers
are not required to zero-pad months, days, hours, minutes, or seconds.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_TIMESTAMP = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$"
)


def _parse_time(value: Any) -> datetime:
    """Parse one timestamp string into a comparable datetime."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    match = _TIMESTAMP.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    year, month, day, hour, minute, second = match.groups()
    return datetime(
        int(year),
        int(month),
        int(day),
        int(hour or 0),
        int(minute or 0),
        int(second or 0),
    )


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _time_le(left: Any, right: Any) -> bool:
    return _parse_time(left) <= _parse_time(right)


def _overlap_days(policy: Any) -> int:
    if isinstance(policy, dict):
        value = policy.get("overlap_days", 0)
    else:
        value = policy
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _find(store: dict, key_id: Any) -> dict | None:
    for record in store.get("keys", []):
        if record.get("key_id") == key_id:
            return record
    return None


def _copy(record: dict | None) -> dict | None:
    return dict(record) if record is not None else None


def _ok(record: dict) -> dict:
    return {"ok": True, "key": dict(record)}


def _fail(reason: str) -> dict:
    return {"ok": False, "error": reason, "key": None}


def register(store: dict, key_id: str, created_at: str, policy: dict) -> dict:
    """Register a key and record the time at which it becomes usable."""
    if not isinstance(store, dict):
        raise TypeError("store must be a dict")
    if not isinstance(key_id, str) or not key_id:
        raise ValueError("key_id must be a non-empty string")

    records = store.setdefault("keys", [])
    store["policy"] = policy

    if _find(store, key_id) is not None:
        return _fail("duplicate_key")

    record = {
        "key_id": key_id,
        "created_at": created_at,
        "effective_at": created_at,
        "activated_at": None,
        "retired_at": None,
    }
    records.append(record)
    return _ok(record)


def activate(store: dict, key_id: str, now: str) -> dict:
    """Activate a registered key and arrange for the old key to remain usable."""
    record = _find(store, key_id)
    if record is None:
        return _fail("unknown_key")

    moment = _parse_time(now)
    record["effective_at"] = now
    record["activated_at"] = now
    record["retired_at"] = None
    store["active_id"] = key_id

    # The old key stays usable for the policy-defined overlap window: its
    # retirement takes effect only after the new key is already effective.
    overlap = _overlap_days(store.get("policy"))
    retired_at = _format_time(moment + timedelta(days=overlap))
    for other in store.get("keys", []):
        if other is record:
            continue
        if other.get("retired_at") is None:
            other["retired_at"] = retired_at

    return _ok(record)


def select(store: dict, now: str) -> dict:
    """Select the key that should serve a request made at the given time."""
    current = _parse_time(now)
    candidates = []
    for record in store.get("keys", []):
        effective_at = record.get("effective_at")
        if effective_at is None or _parse_time(effective_at) > current:
            continue
        retired_at = record.get("retired_at")
        if retired_at is not None and _parse_time(retired_at) <= current:
            continue
        candidates.append(record)
    if not candidates:
        return _fail("no_usable_key")
    # Most recently effective key wins; key_id tie-break keeps the choice
    # deterministic regardless of registration or rotation order.
    candidates.sort(
        key=lambda record: (
            _parse_time(record["effective_at"]),
            str(record.get("key_id")),
        )
    )
    return _ok(candidates[-1])


def retire(store: dict, key_id: str, now: str) -> dict:
    """Retire a key at the given time."""
    record = _find(store, key_id)
    if record is None:
        return _fail("unknown_key")

    record["retired_at"] = now
    if store.get("active_id") == key_id:
        store["active_id"] = None
    return _ok(record)


def overlap_ok(store: dict, now: str, policy: dict) -> bool:
    """Return whether now is covered by the active key's overlap window."""
    active = _find(store, store.get("active_id"))
    if active is None or active.get("activated_at") is None:
        return False
    start = _parse_time(active["activated_at"])
    current = _parse_time(now)
    days = _overlap_days(policy if policy is not None else store.get("policy"))
    return start <= current < start + timedelta(days=days)


def validate_store(store: dict) -> list[str]:
    """Return a list of problems found in the store."""
    records = store.get("keys", [])
    problems: list[str] = []
    seen: set[str] = set()

    for record in records:
        key_id = str(record.get("key_id"))
        if key_id in seen:
            problems.append(f"duplicate_id:{key_id}")
        seen.add(key_id)

    active_id = store.get("active_id")
    for record in records:
        key_id = record.get("key_id")
        if (
            record.get("activated_at") is not None
            and key_id != active_id
            and record.get("retired_at") is None
        ):
            problems.append(f"missing_retired_at:{key_id}")

    return problems


def purge_expired(store: dict, now: str) -> int:
    """Remove records whose retirement time has passed and return the count."""
    current = _parse_time(now)
    records = store.get("keys", [])
    kept = []
    expired = 0
    for record in records:
        retired_at = record.get("retired_at")
        if retired_at is not None and _parse_time(retired_at) <= current:
            expired += 1
        else:
            kept.append(record)
    if expired:
        store["keys"] = kept
        if _find(store, store.get("active_id")) is None:
            store["active_id"] = None
    return expired


def window_batch(requests: list[dict], policy: dict) -> dict:
    """Answer several independent selection requests."""
    results = []
    for request in requests:
        store = request["store"]
        now = request["now"]
        selection = select(store, now)
        results.append(
            {
                "ok": selection["ok"],
                "key": _copy(selection["key"]),
                "overlap": overlap_ok(store, now, policy),
            }
        )
    return {"results": results}
