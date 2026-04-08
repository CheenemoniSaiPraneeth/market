"""
graph_builder.py — fully dynamic, works for ANY modality_intelligence JSON.
No hardcoded company names, drug names, or field values.
Just swap in a new data.json every week and re-run.

key_players.major and key_players.emerging can be either:
  - list of strings  (legacy format)
  - list of objects  {"company_name": "...", "reasoning": "..."}  (new format)

Both are handled transparently.
"""

import json


def _extract_player_name(entry) -> str:
    """Return the company name whether entry is a str or a dict."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return (entry.get("company_name") or "").strip()
    return ""


def _extract_player_reasoning(entry) -> str:
    """Return reasoning text (empty string for legacy string entries)."""
    if isinstance(entry, dict):
        return (entry.get("reasoning") or "").strip()
    return ""


def build_graph(data: dict) -> dict:
    nodes, edges = [], []
    idx = 0
    label_to_id = {}   # deduplicate nodes by label

    SIZE_MAP = {
        "insight":          28,
        "company_major":    24,
        "company_emerging": 18,
        "cdmo":             19,
        "trend":            20,
        "risk":             16,
        "deal":             22,
        "bottleneck":       15,
        "opportunity":      17,
        "stage":            18,
    }

    def add(label: str, ntype: str, desc: str = "", tags: list = None, extra: dict = None) -> int:
        nonlocal idx
        label = label.strip()
        key   = label.lower()
        if key in label_to_id:
            return label_to_id[key]
        node = {
            "id":    idx,
            "label": label,
            "type":  ntype,
            "desc":  (desc or "")[:400],
            "tags":  (tags or [])[:8],
            "size":  SIZE_MAP.get(ntype, 15),
        }
        if extra:
            node.update(extra)
        nodes.append(node)
        label_to_id[key] = idx
        idx += 1
        return idx - 1

    def link(a: int, b: int):
        if a != b and [a, b] not in edges and [b, a] not in edges:
            edges.append([a, b])

    def get_id(label: str):
        return label_to_id.get(label.strip().lower())

    # ── pull the modality block ───────────────────────────────────────────────
    if isinstance(data, dict) and "modality_intelligence" in data:
        modalities = data["modality_intelligence"]
    else:
        modalities = data.get("modality_intelligence", [])

    if not modalities:
        return {"nodes": [], "edges": [], "meta": {}}

    m     = modalities[0]
    evol  = m.get("evolution_and_direction", {})
    grow  = m.get("growth_trajectory", {})
    comm  = m.get("commercial_value", {})
    collab= m.get("collaborations_and_deals", {})
    kp    = m.get("key_players", {})
    bn    = m.get("bottlenecks", {})
    risks = m.get("risks", {})
    opps  = m.get("collaboration_opportunities_for_startup", {})
    impl  = m.get("implementation_stage", {})

    modality_name = m.get("modality_name", "Market")

    # Build meta summary to pass back to frontend
    meta = {
        "modality_name":    modality_name,
        "current_state":    evol.get("current_state", ""),
        "next_direction":   evol.get("next_direction", ""),
        "trend":            grow.get("trend", ""),
        "stage":            impl.get("stage", ""),
        "market_character": comm.get("market_character", ""),
        "total_deals":      collab.get("total_number_of_deals", len(collab.get("deals", []))),
        "value_signals":    comm.get("value_signals", [])[:3],
        "growth_signals":   grow.get("signals", [])[:3],
        "collab_types":     opps.get("collaboration_types", [])[:4],
        "impl_shifts":      impl.get("shifts", [])[:3],
        "roles":            kp.get("roles", [])[:4],
        "bn_business":      bn.get("business", [])[:3],
        "evidence_evol":    evol.get("evidence", [])[:2],
        "evidence_grow":    grow.get("evidence", [])[:2],
        "evidence_risks":   risks.get("evidence", [])[:2],
        "evidence_deals":   collab.get("evidence", [])[:2],
    }

    # lookup: company name (lowercase) -> opportunity detail dict
    opp_lookup = {}
    for opp in opps.get("target_companies", []):
        name = (opp.get("company_name") or "").strip().lower()
        if name:
            opp_lookup[name] = opp

    # lookup: company name (lowercase) -> reasoning string
    reasoning_lookup = {}
    for entry in kp.get("major", []) + kp.get("emerging", []):
        name      = _extract_player_name(entry).lower()
        reasoning = _extract_player_reasoning(entry)
        if name and reasoning:
            reasoning_lookup[name] = reasoning

    def opp_detail(name: str):
        o = opp_lookup.get(name.strip().lower(), {})
        relevance = o.get("relevance", "")
        rationale = o.get("deal_rationale", "")
        sci_fit   = o.get("scientific_fit", "")
        biz_fit   = o.get("business_fit", "")
        why       = o.get("why_this_company", "")

        desc_parts = []
        if relevance:
            desc_parts.append(relevance)
        if why:
            desc_parts.append(f"Why: {why[:120]}")
        desc = " | ".join(desc_parts)

        tags = []
        if sci_fit:
            tags.append(f"Sci: {sci_fit[:50]}")
        if biz_fit:
            tags.append(f"Biz: {biz_fit[:50]}")
        if rationale:
            tags.append(rationale[:80])

        return desc, tags

    # ── 1. ROOT NODE ─────────────────────────────────────────────────────────
    current   = evol.get("current_state", grow.get("trend", ""))
    root_tags = grow.get("signals", evol.get("evidence", []))[:4]
    root = add(
        modality_name, "insight",
        current[:400],
        root_tags,
        {"extra_type": "root", "next_direction": evol.get("next_direction", "")}
    )

    # ── 2. GROWTH TRAJECTORY ─────────────────────────────────────────────────
    if grow.get("trend"):
        gid = add(
            "Growth Trajectory", "insight",
            grow["trend"][:400],
            grow.get("signals", [])[:4],
            {"extra_type": "growth", "evidence": grow.get("evidence", [])}
        )
        link(root, gid)

    # ── 3. KEY TRANSITIONS ───────────────────────────────────────────────────
    trend_ids = []
    for t in evol.get("key_transitions", []):
        name = t.get("transition", "").strip()
        if not name:
            continue
        detail = t.get("detail", "")
        just   = t.get("justification", "")
        tags   = [just[:80]] if just else []
        tid = add(
            name, "trend", detail,
            tags,
            {"justification": just, "extra_type": "transition"}
        )
        trend_ids.append(tid)
        link(root, tid)

    # ── 4. COMMERCIAL VALUE ──────────────────────────────────────────────────
    val_signals = comm.get("value_signals", [])
    if val_signals:
        comm_desc = f"Market: {comm.get('market_character', 'premium')}."
        if comm.get("evidence"):
            comm_desc += " " + comm["evidence"][0]
        vid = add(
            "Commercial Value", "insight",
            comm_desc[:400],
            val_signals[:4],
            {"extra_type": "commercial", "evidence": comm.get("evidence", [])}
        )
        link(root, vid)

    # ── 5. IMPLEMENTATION STAGE ──────────────────────────────────────────────
    if impl.get("stage"):
        stage_label = f"Stage: {impl['stage'].title()}"
        iid = add(
            stage_label, "stage",
            " | ".join(impl.get("shifts", [])[:3]),
            impl.get("shifts", [])[:3],
            {"extra_type": "stage", "evidence": impl.get("evidence", [])}
        )
        link(root, iid)

    # ── 6. MAJOR PLAYERS ─────────────────────────────────────────────────────
    major_ids = []
    for entry in kp.get("major", []):
        name = _extract_player_name(entry)
        if not name:
            continue
        reasoning = _extract_player_reasoning(entry) or reasoning_lookup.get(name.lower(), "")
        desc, tags = opp_detail(name)
        # Prepend reasoning as the primary description if present
        if reasoning:
            desc = reasoning[:400]
        extra = {
            "extra_type":   "major_player",
            "company_type": "Major Pharma",
            "reasoning":    reasoning,
            "opp_data":     opp_lookup.get(name.lower(), {}),
        }
        mid = add(name, "company_major", desc or f"Major player: {name}", tags, extra)
        major_ids.append(mid)
        link(root, mid)

    # ── 7. EMERGING PLAYERS ──────────────────────────────────────────────────
    emerging_ids = []
    for entry in kp.get("emerging", []):
        name = _extract_player_name(entry)
        if not name:
            continue
        reasoning = _extract_player_reasoning(entry) or reasoning_lookup.get(name.lower(), "")
        desc, tags = opp_detail(name)
        if reasoning:
            desc = reasoning[:400]
        extra = {
            "extra_type":   "emerging_player",
            "company_type": "Emerging Biotech",
            "reasoning":    reasoning,
            "opp_data":     opp_lookup.get(name.lower(), {}),
        }
        eid = add(name, "company_emerging", desc or f"Emerging innovator: {name}", tags, extra)
        emerging_ids.append(eid)

    # ── 8. DEALS ─────────────────────────────────────────────────────────────
    deals_list = collab.get("deals", [])
    for deal in deals_list:
        c1    = (deal.get("company_1") or "").strip()
        c2    = (deal.get("company_2") or "").strip()
        dtype = (deal.get("deal_type") or "Deal").strip()
        ddesc = (deal.get("deal_description") or "").strip()
        dval  = (deal.get("deal_value_or_size") or "Undisclosed").strip()
        dyear = str(deal.get("year") or "").strip()

        if not c1 and not c2:
            continue

        if c1 and c2:
            label = f"{c1[:22]} × {c2[:22]}"
        else:
            label = (c1 or c2)[:42]

        parts = []
        if dtype:
            parts.append(f"Type: {dtype}")
        if dval and dval != "Undisclosed":
            parts.append(f"Value: {dval}")
        if dyear:
            parts.append(f"Year: {dyear}")
        if ddesc:
            parts.append(ddesc[:200])
        desc_text = " | ".join(parts)

        tags = []
        if dtype:
            tags.append(dtype)
        if dval and dval != "Undisclosed":
            tags.append(dval)
        if dyear:
            tags.append(dyear)

        extra = {
            "deal_company_1":   c1,
            "deal_company_2":   c2,
            "deal_type":        dtype,
            "deal_description": ddesc,
            "deal_value":       dval,
            "deal_year":        dyear,
            "extra_type":       "deal",
        }

        did = add(label, "deal", desc_text, tags, extra)
        link(root, did)

        for party in [c1, c2]:
            if not party:
                continue
            party_id = get_id(party)
            if party_id is not None and party_id != did:
                link(did, party_id)

    # ── 9. COLLABORATION OPPORTUNITIES ───────────────────────────────────────
    for opp in opps.get("target_companies", []):
        cname = (opp.get("company_name") or "").strip()
        if not cname:
            continue
        cid = get_id(cname)
        if cid is None:
            desc  = opp.get("relevance", "")
            why   = opp.get("why_this_company", "")
            tags  = []
            sci   = opp.get("scientific_fit", "")
            biz   = opp.get("business_fit", "")
            rat   = opp.get("deal_rationale", "")
            if sci:
                tags.append(f"Sci: {sci[:50]}")
            if biz:
                tags.append(f"Biz: {biz[:50]}")
            if rat:
                tags.append(rat[:80])

            full_desc = desc
            if why:
                full_desc += f" | {why[:120]}"

            # also check reasoning_lookup for this company
            reasoning = reasoning_lookup.get(cname.lower(), "")
            if reasoning:
                full_desc = reasoning[:400]

            raw_type = (opp.get("company_type") or "").lower()
            if "cdmo" in raw_type or "platform" in raw_type or "data" in raw_type:
                ntype = "cdmo"
            elif "big pharma" in raw_type or "major" in raw_type:
                ntype = "company_major"
            else:
                ntype = "company_emerging"

            extra = {
                "extra_type":   "opportunity",
                "company_type": opp.get("company_type", ""),
                "reasoning":    reasoning,
                "opp_data":     opp,
            }
            cid = add(cname, ntype, full_desc[:400], tags, extra)
            link(root, cid)

        combined = (opp.get("scientific_fit", "") + " " + opp.get("business_fit", "")).lower()
        for tid in trend_ids:
            tnode  = nodes[tid]
            twords = [w for w in tnode["label"].lower().split() if len(w) > 4]
            if any(w in combined for w in twords):
                link(cid, tid)

    # ── 10. RISKS ────────────────────────────────────────────────────────────
    risk_ids = []
    for r in risks.get("key_risks", []):
        if not isinstance(r, str) or not r.strip():
            continue
        rid = add(
            r[:52], "risk", r,
            risks.get("evidence", [])[:2],
            {"extra_type": "risk"}
        )
        risk_ids.append(rid)
        link(root, rid)

    # ── 11. TECHNICAL BOTTLENECKS ─────────────────────────────────────────────
    for b in bn.get("technical", [])[:6]:
        if not isinstance(b, str) or not b.strip():
            continue
        bid = add(
            b[:52], "bottleneck", b,
            ["Technical bottleneck"] + bn.get("evidence", [])[:1],
            {"extra_type": "bottleneck", "bn_type": "technical"}
        )
        link(root, bid)
        for rid in risk_ids[:3]:
            link(bid, rid)

    # ── 12. BUSINESS BOTTLENECKS ──────────────────────────────────────────────
    for b in bn.get("business", [])[:4]:
        if not isinstance(b, str) or not b.strip():
            continue
        bid = add(
            b[:52], "bottleneck", b,
            ["Business bottleneck"],
            {"extra_type": "bottleneck", "bn_type": "business"}
        )
        link(root, bid)

    # ── 13. COLLABORATION PATTERNS ────────────────────────────────────────────
    for pattern in opps.get("patterns", [])[:6]:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        pid = add(
            pattern[:52], "trend", pattern, [],
            {"extra_type": "pattern"}
        )
        link(root, pid)
        for tid in trend_ids:
            twords = [w for w in nodes[tid]["label"].lower().split() if len(w) > 4]
            if any(w in pattern.lower() for w in twords):
                link(pid, tid)

    # ── DEDUP EDGES ───────────────────────────────────────────────────────────
    seen, clean_edges = set(), []
    for e in edges:
        key = (min(e[0], e[1]), max(e[0], e[1]))
        if key not in seen:
            seen.add(key)
            clean_edges.append(e)

    return {"nodes": nodes, "edges": clean_edges, "meta": meta}


if __name__ == "__main__":
    import sys
    from collections import Counter
    path = sys.argv[1] if len(sys.argv) > 1 else "briefs.json"
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    result = build_graph(d)
    print(f"Nodes: {len(result['nodes'])}  Edges: {len(result['edges'])}")
    counts = Counter(n["type"] for n in result["nodes"])
    for t, c in counts.most_common():
        print(f"  {t:25s} {c}")
    print("\nMeta:", json.dumps(result.get("meta", {}), indent=2))
