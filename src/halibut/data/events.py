"""Sourced event files (policy, infrastructure, context), with leakage-safe availability."""

from __future__ import annotations

from datetime import date

from ..config import ConfigError, _Table, load_raw
from .store import to_timestamp

CATEGORIES = ("purchase_tax", "vat", "annual_tax", "road_toll", "parking", "access", "ferry",
              "target", "infrastructure", "macro", "other")
STATUSES = ("in_force", "modified", "ended", "target", "proposed", "data_gap")
VERIFICATION = ("primary", "secondary", "conflicting")


def _date(t: _Table, key: str, required: bool) -> str | None:
    v = t.raw(key) if required else t.raw(key, None)
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError as exc:
        raise ConfigError(f"{t.at(key)}: expected YYYY-MM-DD") from exc


def load_events(path) -> list[dict]:
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != "1.0":
        raise ConfigError("events schema_version must be '1.0'")
    geo = root.string("geo")
    events = []
    for t in root.tables("events"):
        e = {
            "id": t.string("id"), "geo": geo, "category": t.string("category", choices=CATEGORIES),
            "title": t.string("title"), "status": t.string("status", choices=STATUSES),
            "verification": t.string("verification", choices=VERIFICATION),
            "observed": t.string("observed"), "assumed_effect": t.string("assumed_effect"),
            "notes": t.raw("notes", "") or "",
            "effective_date": _date(t, "effective_date", True),
            "announced_date": _date(t, "announced_date", False),
            "end_date": _date(t, "end_date", False),
        }
        sources = t.raw("sources")
        if not isinstance(sources, list) or not sources or not all(str(s).startswith("http") for s in sources):
            raise ConfigError(f"{t.at('sources')}: at least one source URL is required")
        e["sources"] = list(sources)
        explicit = _date(t, "available_at", False)
        # Knowledge date: explicit > announcement > effective date (conservative).
        known = explicit or e["announced_date"] or e["effective_date"]
        e["available_at"] = to_timestamp(known)
        e["available_at_basis"] = "explicit" if explicit else ("announced" if e["announced_date"] else "effective_date")
        t.done()
        events.append(e)
    root.done()
    ids = [e["id"] for e in events]
    if len(set(ids)) != len(ids):
        raise ConfigError("events: ids must be unique")
    return events
