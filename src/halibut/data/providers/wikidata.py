"""Wikidata entity resolution: names -> items -> companies, parents, countries.

Resolution is deterministic and auditable. For each name: an explicit QID
override (an analyst assertion) wins; otherwise wbsearchentities candidates are
ranked by (a) instance-of (P31) overlap with type hints, then (b) description
keywords, then search rank. All candidates and the method are recorded. If
nothing qualifies, the name stays UNRESOLVED (never guessed).

Brand identity, company and parent company are separate entities:
  brand:<market-local id>  --operated_by-->  company wd:Q…  --parent_organization-->  wd:Q…
  company --country--> place wd:Q…   company --industry--> industry wd:Q…
"""

from __future__ import annotations

import json

from .base import add_identifier, add_relationship, snapshot, upsert_entity

SEARCH = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
DATASET = "wikidata_entities"
PRODUCED_BY = "provider:wikidata"


def search(client, store, raw_dir, name: str, limit: int = 7) -> tuple[list[dict], int]:
    resp = client.get(SEARCH, params={"action": "wbsearchentities", "search": name, "language": "en",
                                      "type": "item", "limit": limit, "format": "json"})
    sid = snapshot(store, DATASET, resp.body, {"search": name}, raw_dir)
    return json.loads(resp.body).get("search", []), sid


def sparql(client, store, raw_dir, query: str, tag: str) -> tuple[list[dict], int]:
    resp = client.get(SPARQL, params={"query": query, "format": "json"},
                      headers={"Accept": "application/sparql-results+json"})
    sid = snapshot(store, DATASET, resp.body, {"sparql": tag, "query": query}, raw_dir)
    return json.loads(resp.body)["results"]["bindings"], sid


def claims(client, store, raw_dir, qids: list[str]) -> tuple[dict, int]:
    """P31 instance of, P749 parent organisation, P17 country, P452 industry (+labels)."""
    values = " ".join(f"wd:{q}" for q in qids)
    q = f"""SELECT ?item ?prop ?value ?valueLabel WHERE {{
      VALUES ?item {{ {values} }}
      VALUES ?prop {{ wdt:P31 wdt:P749 wdt:P17 wdt:P452 }}
      ?item ?prop ?value .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
    rows, sid = sparql(client, store, raw_dir, q, f"claims:{','.join(qids)}")
    out: dict = {}
    for r in rows:
        item = r["item"]["value"].rsplit("/", 1)[-1]
        prop = r["prop"]["value"].rsplit("/", 1)[-1]
        val = r["value"]["value"].rsplit("/", 1)[-1]
        out.setdefault(item, {}).setdefault(prop, []).append((val, r.get("valueLabel", {}).get("value", val)))
    return out, sid


def choose(candidates: list[dict], candidate_claims: dict, type_hints: list[str], keywords: list[str]) -> tuple[dict | None, str]:
    for c in candidates:
        types = {v for v, _ in candidate_claims.get(c["id"], {}).get("P31", [])}
        if types & set(type_hints):
            return c, "type_match"
    for c in candidates:
        desc = (c.get("description") or "").lower()
        if any(k.lower() in desc for k in keywords):
            return c, "keyword_match"
    return None, "unresolved"


def resolve_competitor(client, store, raw_dir, comp, type_hints, keywords, available_at) -> dict:
    """Create brand entity, resolve its company in Wikidata, link parent/country/industry."""
    brand_id = f"brand:{comp.id}"
    upsert_entity(store, brand_id, "brand", comp.name, "asserted", "market_spec",
                  {"aliases": list(comp.aliases), "source": "market specification"})
    for a in comp.aliases:
        add_identifier(store, brand_id, "alias", a.lower(), "market_spec")
    if comp.dft_make:
        add_identifier(store, brand_id, "dft_make", comp.dft_make, "market_spec")
    result = {"brand": brand_id, "name": comp.name}
    if comp.wikidata:
        cands, sid = [{"id": comp.wikidata, "label": comp.name, "description": "(analyst override)"}], None
        cl, sid = claims(client, store, raw_dir, [comp.wikidata])
        chosen, method = cands[0], "override"
    else:
        cands, sid0 = search(client, store, raw_dir, comp.name)
        cl, sid = claims(client, store, raw_dir, [c["id"] for c in cands]) if cands else ({}, sid0)
        chosen, method = choose(cands, cl, type_hints, keywords)
    result.update(method=method, candidates=[{"id": c["id"], "label": c.get("label"), "description": c.get("description")}
                                             for c in cands])
    if chosen is None:
        return result
    qid = chosen["id"]
    company = f"wd:{qid}"
    upsert_entity(store, company, "company", chosen.get("label") or qid, "observed", PRODUCED_BY,
                  {"description": chosen.get("description"), "instance_of": cl.get(qid, {}).get("P31", [])})
    add_identifier(store, company, "wikidata", qid, PRODUCED_BY)
    result.update(company=company, qid=qid, label=chosen.get("label"))
    status = "asserted" if method == "override" else "derived"
    add_relationship(store, brand_id, "operated_by", company, status, f"resolver:{method}", available_at, sid,
                     {"method": method})
    for prop, pred, kind in (("P749", "parent_organization", "company"), ("P17", "country", "place"),
                             ("P452", "industry", "industry")):
        for val, label in cl.get(qid, {}).get(prop, []):
            ent = f"wd:{val}"
            upsert_entity(store, ent, kind, label, "observed", PRODUCED_BY, {})
            add_identifier(store, ent, "wikidata", val, PRODUCED_BY)
            add_relationship(store, company, pred, ent, "observed", PRODUCED_BY, available_at, sid, {"wikidata_property": prop})
            result.setdefault(pred, []).append({"id": ent, "label": label})
    return result
