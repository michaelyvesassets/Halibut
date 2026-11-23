"""Intelligence report: Markdown for people, JSON for agents. Every claim cites evidence ids."""

from __future__ import annotations

import json
from pathlib import Path

LABEL = ("Observed facts come from the cited sources. Derived metrics are computed by Halibut from those facts "
         "(definitions in report.json). Hypotheses are PROPOSED and untested. Scenario drafts are SYNTHETIC and "
         "uncalibrated. Nothing here is a forecast or a causal finding.")


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.2f}%"


def write_report(r: dict, out_dir) -> Path:
    path = Path(out_dir) / f"{r['market']['id']}_{r['market']['as_of']}_{r['run_id'].split(':')[1]}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "report.json").write_text(json.dumps({"label": LABEL, **r}, indent=1, default=str) + "\n")
    m, tx = r["market"], r["signals"]["transactions"]
    L = [f"# Market intelligence: {m['name']}", "", f"> {LABEL}", "",
         f"- Run `{r['run_id']}` (experiment `{r['experiment_id']}`), as of **{m['as_of']}**, geography {m['geography']}",
         f"- Strategic mode: **{m['mode']}**, focal `{m['focal']}`, target `{m['target']}`",
         f"- Competitors: {', '.join(m['competitors'])}",
         f"- Usage: {r['budget']['requests']} requests, {r['budget']['bytes'] / 1e6:.1f} MB, {r['seconds']:.0f}s", "",
         "## 1. Evidence collection", "", "| Step | Status | Seconds | Note |", "|---|---|---|---|"]
    for k, v in r["steps"].items():
        note = v.get("error", "")[:120] if v.get("status") != "ok" else ""
        L.append(f"| {k} | {v['status']} | {v.get('seconds', '')} | {note} |")
    L += ["", "## 2. Competitor graph (brand -> operating company -> parent / country / industry)", "",
          "| Competitor | Resolution | Company (Wikidata) |", "|---|---|---|"]
    for cid, res in r["resolutions"].items():
        L.append(f"| {cid} | {res.get('method')} | {res.get('label') or '-'} {('`' + res['qid'] + '`') if res.get('qid') else ''} |")
    L += ["", "Relationships (id: subject --predicate--> object [status, source]):", ""]
    for g in r["graph"][:60]:
        L.append(f"- `{g['id']}`: {g['subject']} --{g['predicate']}--> {g['object_name']} [{g['status']}, {g['produced_by']}]")
    L += ["", "## 3. Observed market signals", ""]
    if tx.get("available"):
        L += [f"Universe `{tx['universe']}` (first UK registrations, including imported used vehicles). "
              f"Latest four quarters L4 = {', '.join(tx['L4'])}; prior four P4 = {', '.join(tx['P4'])}.", "",
              f"{tx['segment']} share of the market: P4 {_pct(tx['segment_share_P4'])} -> L4 {_pct(tx['segment_share_L4'])}", "",
              f"| Competitor | Share P4 | Share L4 | {tx['segment']} share P4 | {tx['segment']} share L4 | {tx['segment']} mix L4 | Registrations L4 |",
              "|---|---|---|---|---|---|---|"]
        for cid, e in tx["competitors"].items():
            if not e.get("available"):
                L.append(f"| {cid} | unavailable: {e.get('reason')} | | | | | |")
                continue
            L.append(f"| {cid} | {_pct(e['share_P4'])} | {_pct(e['share_L4'])} | {_pct(e.get('segment_share_P4'))} | "
                     f"{_pct(e.get('segment_share_L4'))} | {_pct(e.get('segment_mix_L4'))} | {e['registrations_L4']:,.0f} |")
        tp = r["signals"].get("products", {}).get("top_products", {})
        for cid, rows in tp.items():
            L.append(f"\nTop product lines ({cid}, L4): " + ", ".join(f"{x['product_line']} ({x['registrations_L4']:,.0f})" for x in rows))
    else:
        L.append(f"Transactions unavailable: {tx.get('reason')}")
    fp, md, mc = r["signals"]["footprint"], r["signals"]["media"], r["signals"]["macro"]
    L += ["", "**Footprint (presence, OSM, ODbL; mapped locations, not the full network):** " +
          (", ".join(f"{c}={v['mapped_locations']:.0f}" for c, v in fp.get("competitors", {}).items() if v["mapped_locations"] is not None)
           if fp.get("available") else "unavailable"),
          "", "**Media attention (proxy, GDELT sample):** " +
          (f"{md['total_matched']} matched records in {md['sample'].get('n_files')} sampled files; " +
           ", ".join(f"{c}={n}" for c, n in md["counts"].items()) if md.get("available") else "unavailable or no matches"),
          "", "**Macro (World Bank):** " + ("; ".join(f"{k} {v['latest_period']}: {v['latest']:,.2f}" for k, v in mc.get("indicators", {}).items())
                                            if mc.get("available") else "unavailable")]
    L += ["", "## 4. Prior lessons considered", ""]
    L += [f"- [{k['status']}] {k['kind']}: {k['statement']} (`{k['knowledge_id']}`)" for k in r["lessons"]] or ["- none"]
    pol = next((h.get("forecast_policy") for h in r["hypotheses"] if h.get("forecast_policy")), None)
    if pol:
        L += ["", f"**Effect on this run:** predictions use `{pol['method']}`; simulation use: `{pol['simulation_use']}`. {pol['note']}"
              + (f" (because of {', '.join('`' + b + '`' for b in pol['because'])})" if pol["because"] else "")]
    if r.get("prior_hypotheses"):
        L += ["", f"Prior hypotheses for this market in the learning memory: {len(r['prior_hypotheses'])} "
              f"(statuses: {', '.join(sorted({p['status'] for p in r['prior_hypotheses']}))})."]
    L += ["", "## 5. Hypotheses (proposed, validated against evidence; untested)", ""]
    for h in r["hypotheses"]:
        L += [f"### `{h['id']}` [{h['mode']}, confidence {h['confidence']}]", "", h["claim"], "",
              f"- Evidence: {', '.join('`' + e + '`' for e in h['evidence'][:12])}{' …' if len(h['evidence']) > 12 else ''}",
              f"- Prediction (testable): {json.dumps(h['prediction'])}",
              f"- Rationale: {h['rationale']}"]
        if h.get("levers"):
            L.append(f"- Legitimate levers to examine: {', '.join(h['levers'])}; candidate mechanisms: {', '.join(h['mechanism_candidates'])}")
        for u in h.get("uncertainties", []):
            L.append(f"- Uncertainty: {u}")
        for c in h.get("critiques", []):
            L.append(f"- Critique ({'reference critic'}): {c['statement']}")
        L.append("")
    if r["rejected_hypotheses"]:
        L += ["### Rejected by validation", ""] + [f"- `{h['id']}`: {'; '.join(h['validation']['problems'])}" for h in r["rejected_hypotheses"]]
    L += ["", "## 6. Draft simulation scenarios (SYNTHETIC, uncalibrated)", ""]
    for d in r["scenario_drafts"]:
        L += [f"From `{d['hypothesis']}`: config validated by the engine; final expected shares in a synthetic preview:", "",
              "| Scenario | " + " | ".join(next(iter(d["preview_final_expected_shares"].values())).keys()) + " |",
              "|---|" + "---|" * len(next(iter(d["preview_final_expected_shares"].values())))]
        for s, v in d["preview_final_expected_shares"].items():
            L.append(f"| {s} | " + " | ".join(f"{x:.3f}" for x in v.values()) + " |")
        L.append("\nThese previews only show the draft is runnable; effect sizes are assumptions until calibrated (Phase 3 pipeline).\n")
    L += ["## 7. Opportunities and uncertainties", ""]
    if tx.get("available") and m["focal"] and m["target"]:
        f, t = tx["competitors"].get(m["focal"], {}), tx["competitors"].get(m["target"], {})
        if f.get("available") and t.get("available"):
            L.append(f"- Opportunity to test: {m['focal']} has {_pct(f.get('segment_mix_L4'))} {tx['segment']} mix vs "
                     f"{m['target']} {_pct(t.get('segment_mix_L4'))}; the segment is {_pct(tx['segment_share_L4'])} of the market.")
    L += ["- Unobservable here: prices and discounts, private vs fleet buyers, household switching, dealer counts beyond OSM, "
          "marketing spend, supply constraints.",
          "- Media evidence is a small sample of 15-minute GDELT files; footprint is OSM mapping completeness.",
          "", "## 8. Sources and licences", ""]
    L += [f"- {s['publisher']} ({s['licence']}): {s['attribution']}" for s in r["sources"]]
    (path / "report.md").write_text("\n".join(L) + "\n")
    return path
