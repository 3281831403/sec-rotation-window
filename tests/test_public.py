from __future__ import annotations

from datetime import datetime

from engine.keys.window import (
    activate,
    overlap_ok,
    register,
    retire,
    select,
    window_batch,
)

POLICY = {"overlap_days": 30}


def _key_id(result: dict) -> str:
    assert result["ok"] is True
    assert result["key"] is not None
    return result["key"]["key_id"]


def _record(store: dict, key_id: str) -> dict:
    for record in store["keys"]:
        if record["key_id"] == key_id:
            return record
    raise AssertionError(f"missing key record: {key_id}")


def test_registered_key_is_selectable() -> None:
    store: dict = {}
    register(store, "quarter-1", "2026-9-30", POLICY)

    selected = select(store, "2026-10-1")

    assert _key_id(selected) == "quarter-1"
    assert selected["key"]["effective_at"] == "2026-9-30"


def test_rotation_selects_new_key() -> None:
    store: dict = {}
    register(store, "quarter-2", "2026-01-01", POLICY)
    register(store, "quarter-1", "2026-01-01", POLICY)
    activate(store, "quarter-1", "2026-01-01")
    activate(store, "quarter-2", "2026-01-02")

    selected = select(store, "2026-01-03")

    assert _key_id(selected) == "quarter-2"


def test_old_key_usable_during_overlap() -> None:
    store: dict = {}
    register(store, "quarter-1", "2026-01-01", POLICY)
    activate(store, "quarter-1", "2026-01-01")
    register(store, "quarter-2", "2026-01-10", POLICY)
    activate(store, "quarter-2", "2026-01-10")

    old = _record(store, "quarter-1")
    assert overlap_ok(store, "2026-01-20", POLICY) is True
    assert old["retired_at"] is not None
    assert datetime.fromisoformat(old["retired_at"]) > datetime(2026, 1, 20)


def test_retired_key_not_selectable() -> None:
    store: dict = {}
    register(store, "quarter-1", "2026-01-01", POLICY)
    activate(store, "quarter-1", "2026-01-01")
    retire(store, "quarter-1", "2026-01-10")

    selected = select(store, "2026-01-11")

    assert selected["ok"] is False
    assert selected["key"] is None


def test_batch_requests_are_independent() -> None:
    store: dict = {}
    register(store, "quarter-1", "2026-01-01", POLICY)
    register(store, "quarter-2", "2026-01-02", POLICY)
    requests = [
        {"store": store, "now": "2026-01-01"},
        {"store": store, "now": "2026-01-02"},
    ]

    outcome = window_batch(requests, POLICY)

    assert [_key_id(item) for item in outcome["results"]] == [
        "quarter-1",
        "quarter-2",
    ]