"""Industry-neutral share panel (L3): units per entity x segment x quarter, as knowable at a date.

A panel is the transaction evidence of ONE market universe (e.g. "first UK registrations of cars",
"GB retail litres of plant-based milk"): for each quarter, the market total, the market's focal
segment, and each competitor's total and segment units, with the evidence ids they rest on and the
date each value became available. Adapters build panels from:

- ``dft``        DfT VEH0160 observations in the store, through the as-of evidence retriever;
- ``csv_panel``  any CSV with columns ``period,entity,segment,units,available_at`` (an operator- or
                 client-supplied panel for any industry; content-addressed by sha256).

Metric templates (the closed MEASUREMENT grammar; mechanisms stay free text):

  share                 entity units / market units
  segment_share         entity segment units / market segment units
  segment_mix           entity segment units / entity units
  market_segment_share  market segment units / market units   (entity = "market")
  segment_share_gap     segment_share(entity) - segment_share(entity_b)

Values are pooled over a set of periods (sum of numerators / sum of denominators). Every metric
exposed to agents carries a recomputable definition; EI recomputes it from the panel.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..data.store import to_timestamp

TEMPLATES = ("share", "segment_share", "segment_mix", "market_segment_share", "segment_share_gap")
WINDOW_IDS = {"share": "share", "segment_share": "segment_share", "segment_mix": "segment_mix",
              "market_segment_share": "market_segment_share"}
QUARTER = "quarter"


def next_quarters(last: str, n: int = 2) -> list[str]:
    y, q = int(last[:4]), int(last[-1])
    out = []
    for _ in range(n):
        q += 1
        if q == 5:
            y, q = y + 1, 1
        out.append(f"{y}-Q{q}")
    return out


def _is_quarter(p: str) -> bool:
    return len(p) == 7 and p[4:6] == "-Q" and p[:4].isdigit() and p[-1] in "1234"


@dataclass
class SharePanel:
    market_id: str
    source: str
    as_of: str
    universe: str
    segment: str | None
    entities: list[str]
    # cells[(entity_or_MARKET, "all"|"seg")][period] = (units, evidence_ids, available_at)
    cells: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)  # provenance of the underlying data

    MARKET = "__market__"

    @property
    def periods(self) -> list[str]:
        return sorted(self.cells.get((self.MARKET, "all"), {}))

    def windows(self) -> tuple[list[str], list[str]]:
        q = self.periods
        return q[-4:], q[-8:-4]

    def _units(self, ent: str, part: str, periods) -> tuple[float | None, list[str], str | None]:
        cell = self.cells.get((ent, part), {})
        if not all(p in cell for p in periods):
            return None, [], None
        tot, ids, latest = 0.0, [], None
        for p in periods:
            v, i, a = cell[p]
            tot += v
            ids += i
            latest = max(latest or "", a or "")
        return tot, ids, latest

    def value(self, template: str, entity: str, periods, entity_b: str | None = None):
        """(value, evidence_ids, latest_available_at); value None if any period/denominator is missing."""
        periods = list(periods)
        M = self.MARKET
        if template == "segment_share_gap":
            a = self.value("segment_share", entity, periods)
            b = self.value("segment_share", entity_b or "", periods)
            if a[0] is None or b[0] is None:
                return None, [], None
            return a[0] - b[0], a[1] + b[1], max(a[2], b[2])
        num_key, den_key = {"share": ((entity, "all"), (M, "all")), "segment_share": ((entity, "seg"), (M, "seg")),
                            "segment_mix": ((entity, "seg"), (entity, "all")),
                            "market_segment_share": ((M, "seg"), (M, "all"))}[template]
        n, ni, na = self._units(*num_key, periods)
        d, di, da = self._units(*den_key, periods)
        if n is None or d is None or d <= 0:
            return None, [], None
        return n / d, ni + di, max(na or "", da or "")

    def series(self, template: str, entity: str, entity_b: str | None = None) -> list[tuple[str, float]]:
        out = []
        for p in self.periods:
            v = self.value(template, entity, [p], entity_b)[0]
            if v is not None:
                out.append((p, v))
        return out

    def metric_id(self, template: str, window: str, entity: str) -> str:
        return f"metric:{WINDOW_IDS[template]}:{window}" + ("" if template == "market_segment_share" else f":{entity}")

    def metrics(self) -> dict:
        """Window metrics exposed to agents: id -> {value, definition, inputs}."""
        L4, P4 = self.windows()
        out = {}
        if len(L4) < 4 or len(P4) < 4:
            return out
        for w, periods in (("L4", L4), ("P4", P4)):
            combos = [("market_segment_share", "market")] if self.segment else []
            for e in self.entities:
                combos += [("share", e)] + ([("segment_share", e), ("segment_mix", e)] if self.segment else [])
            for t, e in combos:
                v, ids, _ = self.value(t, e, periods)
                if v is None:
                    continue
                out[self.metric_id(t, w, e)] = {"id": self.metric_id(t, w, e), "value": v, "template": t, "entity": e,
                                                "window": w, "periods": list(periods), "inputs": ids,
                                                "definition": {"kind": "panel", "template": t, "entity": e,
                                                               "periods": list(periods), "source": self.source}}
        return out

    def recompute(self, metric: dict) -> float | None:
        d = metric["definition"]
        return self.value(d["template"], d["entity"], d["periods"])[0]

    def exists(self, evidence_id: str) -> bool:
        return evidence_id in self._ids

    @property
    def _ids(self) -> set:
        if not hasattr(self, "_idset"):
            self._idset = {i for cell in self.cells.values() for (_, ids, _) in cell.values() for i in ids}
        return self._idset


def from_dft(retriever, market) -> SharePanel:
    from ..intel.signals import TX_UNIVERSE, _fuel_of, _make_of  # noqa: PLC0415
    body = market.transactions.get("body_type", "Cars")
    segment = market.transactions.get("segment")
    universe = TX_UNIVERSE.format(body=body)
    make_to = {c.dft_make: c.id for c in market.competitors if c.dft_make}
    cells: dict = defaultdict(dict)
    M = SharePanel.MARKET

    def add(key, period, v, oid, avail):
        u, ids, a = cells[key].get(period, (0.0, [], ""))
        cells[key][period] = (u + v, ids + [oid], max(a, avail))
    for s in retriever.series_ids(universe):
        ent = make_to.get(_make_of(s))
        seg = bool(segment) and _fuel_of(s) == segment
        for o in retriever.observations(s, market.geography):
            if o["value"] is None or not _is_quarter(o["period"]):
                continue
            for key in [(M, "all")] + ([(M, "seg")] if seg else []) + ([(ent, "all")] if ent else []) + \
                       ([(ent, "seg")] if ent and seg else []):
                add(key, o["period"], o["value"], o["id"], o["available_at"])
    return SharePanel(market.id, "dft", retriever.as_of, universe, segment, [c.id for c in market.competitors], dict(cells),
                      {"provider": "dft", "universe": universe})


def from_csv(path, market, as_of: str) -> SharePanel:
    """CSV panel: period (YYYY-Qn), entity (competitor id or 'other'), segment ('' or the market segment),
    units, available_at (publication date). Rows published after ``as_of`` are invisible."""
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    t = to_timestamp(as_of)
    segment = market.transactions.get("segment")
    ids_ok = {c.id for c in market.competitors}
    cells: dict = defaultdict(dict)
    M = SharePanel.MARKET
    for i, r in enumerate(csv.DictReader(raw.decode().splitlines()), start=2):
        if not _is_quarter(r["period"]):
            raise ValueError(f"{path.name}:{i}: period must be YYYY-Qn (R2 loop supports quarterly panels)")
        avail = to_timestamp(r["available_at"])
        if avail > t:
            continue
        v, ent, seg = float(r["units"]), r["entity"], (r.get("segment") or "") == (segment or "\0")
        oid = f"panel:{sha[:12]}:{i}"
        keys = [(M, "all")] + ([(M, "seg")] if seg else [])
        if ent in ids_ok:
            keys += [(ent, "all")] + ([(ent, "seg")] if seg else [])
        for key in keys:
            u, ids, a = cells[key].get(r["period"], (0.0, [], ""))
            cells[key][r["period"]] = (u + v, ids + [oid], max(a, avail))
    return SharePanel(market.id, "csv_panel", t, f"csv_panel:{path.name}", segment, [c.id for c in market.competitors],
                      dict(cells), {"provider": "csv_panel", "file": path.name, "sha256": sha})


def build_panel(store, market, as_of: str) -> SharePanel:
    """The market's transaction panel as knowable at ``as_of`` (provider from the market spec)."""
    prov = (market.transactions or {}).get("provider")
    if prov == "dft":
        from ..intel.retrieval import StoreEvidenceRetriever  # noqa: PLC0415
        return from_dft(StoreEvidenceRetriever(store, as_of), market)
    if prov == "csv_panel":
        return from_csv(Path(market.path).parent / market.transactions["path"], market, as_of)
    raise ValueError(f"no panel adapter for transactions provider {prov!r}")
