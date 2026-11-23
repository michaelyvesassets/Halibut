"""Failure taxonomy for agent calls and agent output items, and the validation-record writer.

Classes (v1). The first matching class is the primary one; all problems are kept.

Call level (one LLM reply):
  call.truncated        stop_reason == max_tokens (reply cut off; unusable)
  call.refusal          stop_reason == refusal (safeguard decline)
  call.invalid_json     a complete reply that is not a JSON object
  call.schema_top_level the reply's top-level structure violates its schema (whole reply rejected)
  call.error            the request failed (HTTP, network, budget)
  call.not_reproducible (offline reconstruction only) the original call succeeded but its reply is not in the cache
                        (its prompt embeds run-specific ids), so it cannot be replayed; reported, never invented

Item level (one proposed hypothesis / action / critique):
  format.evidence_not_id_list    evidence is not an array of id strings (e.g. a dict of values)
  format.evidence_value_appended an evidence id with a value attached ("metric:x = 8.13")
  format.subject_not_canonical   subject is a prefixed or decorated form of a valid id ("brand:byd")
  unsupported.kind_or_subject    kind or subject outside what the engine can turn into a test
  fabricated.evidence_id         a well-formed evidence id that does not exist in the bundle / as of t
  validation.unsupported_number  a narrative number that matches no evidence value
  policy.lever_or_prohibited     an unregistered lever or a prohibited tactic
  temporal.hindsight             evidence not available at as-of, or a test resolving before as-of
  schema.other / validation.other anything else (with the raw problems recorded)
"""

from __future__ import annotations

import json
import re

from ..data.store import utc_now

ID_PREFIXES = ("metric:", "obs:", "rel:", "doc:", "ent:", "k:", "c:")
ID_RE = re.compile(r"^(metric|obs|rel|doc|ent|k|c):[A-Za-z0-9_:.\-]+$")


def classify_outcome(outcome: str | None) -> str | None:
    """Map an agent_calls.outcome string (current or pre-R1 wording) to a call-level class (None = ok)."""
    o = outcome or "ok"
    if o == "ok":
        return None
    if "max_tokens" in o or o.startswith("truncated"):
        return "call.truncated"
    if "refusal" in o:
        return "call.refusal"
    if o.startswith("not_reproducible"):
        return "call.not_reproducible"
    if o.startswith("schema_invalid"):
        return "call.schema_top_level"
    if o.startswith(("unparseable", "invalid_json")):
        return "call.invalid_json"
    return "call.error"


def call_outcome(stop_reason: str | None) -> str:
    """Outcome string for a reply whose JSON could not be used."""
    if stop_reason == "max_tokens":
        return "truncated: stop_reason=max_tokens"
    if stop_reason == "refusal":
        return "refusal: stop_reason=refusal"
    if stop_reason == "not_reproducible":
        return "not_reproducible: the original reply was not cached"
    return f"invalid_json: stop_reason={stop_reason}"


def _strip_prefix(s: str) -> str:
    return s.split(":", 1)[1] if ":" in s else s


def classify_item(item, *, subjects=(), kinds=None, metric_ids=None) -> list[str]:
    """Syntactic/semantic classes for one proposed hypothesis item, before or without schema enforcement.
    ``subjects``: canonical subject ids; ``metric_ids``: evidence ids present in the bundle (optional)."""
    out = []
    if not isinstance(item, dict):
        return ["schema.other"]
    ev = item.get("evidence")
    if not isinstance(ev, list) or not all(isinstance(e, str) for e in ev):
        out.append("format.evidence_not_id_list")
    else:
        if any(not ID_RE.match(e) and e.startswith(ID_PREFIXES) for e in ev):
            out.append("format.evidence_value_appended")
        if metric_ids is not None and any(ID_RE.match(e) and e not in metric_ids for e in ev):
            out.append("fabricated.evidence_id")
    subj = item.get("subject")
    if subjects and isinstance(subj, str) and subj not in subjects:
        out.append("format.subject_not_canonical" if _strip_prefix(subj) in subjects else "unsupported.kind_or_subject")
    if kinds is not None and item.get("kind") not in kinds:
        out.append("unsupported.kind_or_subject")
    return out


def classify_problems(problems: list[str], item=None, *, subjects=()) -> str:
    """Primary class for a rejection, from validator/schema problems plus (when available) the raw item."""
    if item is not None:
        c = classify_item(item, subjects=subjects)
        if c:
            return c[0]
    text = " ".join(problems)
    if re.search(r"is not an allowed value", text):
        m = re.search(r"\.(evidence|subject|kind|tool|hypothesis_id)(\[\d+\])?: '([^']*)'", text)
        if m and m.group(1) == "evidence":
            v = m.group(3)
            return "format.evidence_value_appended" if (v.startswith(ID_PREFIXES) and not ID_RE.match(v)) else "fabricated.evidence_id"
        if m and m.group(1) == "subject":
            return "format.subject_not_canonical" if ":" in m.group(3) else "unsupported.kind_or_subject"
        return "unsupported.kind_or_subject"
    if "expected array" in text and ".evidence" in text:
        return "format.evidence_not_id_list"
    if "metric not found" in text or "does not exist" in text:
        return "fabricated.evidence_id"
    if "does not match any evidence value" in text:
        return "validation.unsupported_number"
    if "lever" in text or "prohibited" in text:
        return "policy.lever_or_prohibited"
    if "not available at" in text or "hindsight" in text or "resolve after" in text:
        return "temporal.hindsight"
    return "schema.other" if any(p.startswith("$") for p in problems) else "validation.other"


def record(store, *, run_id, produced_by: str, subject: str, stage: str, outcome: str, failure_class: str, problems: list,
           item, as_of: str | None = None, tenant: str = "public", events=None) -> int:
    """Append one validation record (and an event). Never raises for content; the caller commits."""
    cur = store.conn.execute("""INSERT INTO validation_records (run_id, tenant, produced_by, subject, stage, outcome,
        failure_class, problems_json, item_json, as_of, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                             (run_id, tenant, produced_by, subject, stage, outcome, failure_class,
                              json.dumps(list(problems)[:20], default=str),
                              json.dumps(item, default=str, sort_keys=True)[:20000], as_of, utc_now()))
    if events is None:
        from .events import EventLog  # noqa: PLC0415
        events = EventLog(store)
    events.append(f"agent_output.{outcome}", "EI", {"subject": subject, "stage": stage, "failure_class": failure_class,
                                                     "problems": list(problems)[:5]},
                  run_id=run_id, subject_id=f"validation_record:{cur.lastrowid}", provenance={"produced_by": produced_by},
                  tenant=tenant)
    return int(cur.lastrowid)


def summary(store, run_prefix: str) -> dict:
    """Counts of validation records and failed calls for runs starting with ``run_prefix``."""
    vr = store.conn.execute("""SELECT stage, outcome, failure_class, COUNT(*) n FROM validation_records WHERE run_id LIKE ?
        GROUP BY 1, 2, 3 ORDER BY 1, 3""", (run_prefix + "%",)).fetchall()
    calls = {}
    for (o,) in store.conn.execute("SELECT outcome FROM agent_calls WHERE run_id LIKE ?", (run_prefix + "%",)):
        c = classify_outcome(o)
        if c:
            calls[c] = calls.get(c, 0) + 1
    by_stage = {}
    for r in vr:
        by_stage.setdefault(f"{r['stage']}:{r['outcome']}", {})[r["failure_class"]] = r["n"]
    return {"items": by_stage, "calls": calls}
