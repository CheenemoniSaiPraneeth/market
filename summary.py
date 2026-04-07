"""
summary.py — Pharma Intelligence Brief Generator

Reads extracted article text (text.json), calls the LLM with guaranteed
JSON output, validates the schema, and writes structured modality intelligence
to a clean JSON file.

Pipeline:
1. Chunk articles (15 each) → per-chunk modality intelligence
2. Merge all chunk results into one unified report
3. Validate schema and write output

After each run, results are also appended to briefs_history.json:

  {
    "2026-04-02": {
      "bispecific antibodies": [ { modality_name, trend, deals, ... }, ... ],
      "CAR-T":                 [ ... ]
    },
    "2026-04-01": { ... }
  }

Usage:
  python summary.py --query "PROTAC"
  python summary.py --query "CAR-T" --input text.json --output briefs.json
"""

import argparse
import json
import re
import sys
import time
import requests
from datetime import datetime
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
INVOKE_URL      = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY         = "Bearer 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"
MODEL           = "qwen/qwen3.5-122b-a10b"
MAX_RETRIES     = 3
BACKOFF_BASE    = 1      # seconds — attempt 1: no wait, 2: 1s, 3: 2s, 4: 4s
REQUEST_TIMEOUT = 180    # seconds per attempt
CHUNK_SIZE      = 5     # articles per chunk

BRIEFS_HISTORY_FILE = "briefs_history.json"

HEADERS = {
    "Authorization": API_KEY,
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert biotech strategy analyst specializing in deeptech modalities, pharmaceutical R&D, and market intelligence. Your task is to transform raw scientific, clinical, and market information into a highly structured, insight-rich JSON output.

STRICT INSTRUCTIONS:
- Return ONLY JSON.
- Do NOT include explanations or markdown.
- Do NOT hallucinate.
- Be strictly scientific and evidence-based.
- Do NOT think creatively beyond given data.
- No generic statements.
- Every explanation should be minimum 5-6 lines very very detailed for sure.
-Fill up every slot and give result if you are usure also give them it is fine but justify your reasoning detailly
- Every company must have clear reasoning.
- Each company must be mapped into the correct ecosystem bucket.
- Prioritize actionable insights over summaries.
- Even if input data is minimal or incomplete, you MUST still return a fully structured JSON with best possible inference. Do NOT return empty output.
- For deals: extract company_1, company_2, deal_type, deal_description, deal_value_or_size, year from the articles.
- For key_players: populate major and emerging as lists of objects where each object contains:
  {
    "company_name": "string",
    "reasoning": "string"
  }
  The reasoning field MUST explain in 5-6 detailed lines WHY this company is a key player — covering their scientific capabilities, pipeline strength, platform differentiation, deal activity, and market position specific to this modality. Do NOT use generic statements.
- For risks: key_risks must be a list of plain strings.
- For bottlenecks: technical and business must be lists of plain strings.
- For collaboration_opportunities_for_startup.target_companies: each entry needs company_name, company_type, relevance, scientific_fit, business_fit, why_this_company, deal_rationale.
Think step-by-step internally but output only final JSON.

OUTPUT FORMAT (strict JSON, nothing else):
{
  "modality_intelligence": [
    {
      "modality_name": "string",
      "evolution_and_direction": {
        "current_state": "string",
        "next_direction": "string",
        "key_transitions": [
          {
            "transition": "string",
            "detail": "string",
            "justification": "string"
          }
        ],
        "evidence": ["string"]
      },
      "growth_trajectory": {
        "trend": "string",
        "signals": ["string"],
        "evidence": ["string"]
      },
      "commercial_value": {
        "market_character": "string",
        "value_signals": ["string"],
        "evidence": ["string"]
      },
      "collaborations_and_deals": {
        "deals": [
          {
            "company_1": "string",
            "company_2": "string",
            "deal_type": "string",
            "deal_description": "string",
            "deal_value_or_size": "string",
            "year": "string"
          }
        ],
        "total_number_of_deals": 0,
        "evidence": ["string"]
      },
      "key_players": {
        "major": [
          {
            "company_name": "string",
            "reasoning": "string"
          }
        ],
        "emerging": [
          {
            "company_name": "string",
            "reasoning": "string"
          }
        ],
        "roles": ["string"],
        "evidence": ["string"]
      },
      "bottlenecks": {
        "technical": ["string"],
        "business": ["string"],
        "evidence": ["string"]
      },
      "implementation_stage": {
        "stage": "string",
        "shifts": ["string"],
        "evidence": ["string"]
      },
      "risks": {
        "key_risks": ["string"],
        "evidence": ["string"]
      },
      "collaboration_opportunities_for_startup": {
        "target_companies": [
          {
            "company_name": "string",
            "company_type": "string",
            "relevance": "string",
            "scientific_fit": "string",
            "business_fit": "string",
            "why_this_company": "string",
            "deal_rationale": "string"
          }
        ],
        "collaboration_types": ["string"],
        "patterns": ["string"],
        "evidence": ["string"]
      }
    }
  ]
}
"""

# ── MERGE SYSTEM PROMPT ───────────────────────────────────────────────────────

MERGE_SYSTEM_PROMPT = """You are merging multiple structured modality intelligence JSON outputs into one unified report.

STRICT MERGE RULES:
- Return ONLY JSON in the exact same schema as the input chunks.
- ZERO signal loss — preserve ALL insights from ALL chunks.
- If the same modality appears in multiple chunks, merge into ONE entry and APPEND all unique insights.
- For list fields (signals, evidence, deals, key_players, target_companies, etc): combine all entries, remove only exact duplicates.
- For string fields (current_state, trend, market_character, stage): concatenate with a separator " | " if different content exists.
- Do NOT summarize, compress, or drop any information.
- Do NOT hallucinate new content.
- Output must be information-dense with every field preserved.

OUTPUT FORMAT (strict JSON, nothing else):
{
  "modality_intelligence": [ ... ]
}
"""

# ── CHUNKING ──────────────────────────────────────────────────────────────────

def chunk_articles(articles, chunk_size=CHUNK_SIZE):
    for i in range(0, len(articles), chunk_size):
        yield articles[i:i + chunk_size]

# ── PROMPT BUILDERS ───────────────────────────────────────────────────────────

def build_chunk_prompt(articles: list, query: str) -> str | None:
    sections = []

    for i, art in enumerate(articles, 1):
        title  = art.get("title", "Untitled")
        source = art.get("url", "Unknown")
        date   = art.get("date") or art.get("period", "")
        body   = (art.get("body") or "").strip()

        if not body:
            continue

        sections.append(
            f"--- ARTICLE {i} ---\n"
            f"Source : {source}\n"
            f"Date   : {date}\n"
            f"Title  : {title}\n\n"
            f"{body}"
        )

    if not sections:
        return None

    return (
        f"Query focus: {query}\n\n"
        f"Below are {len(sections)} pharmaceutical news articles.\n"
        f"Extract all relevant modality intelligence matching the query focus.\n\n"
        + "\n\n".join(sections)
    )


def build_merge_prompt(chunk_results: list, query: str) -> str:
    """Build a prompt to merge multiple chunk JSON outputs into one."""
    all_chunks = "\n\n".join(chunk_results)
    return (
        f"Query focus: {query}\n\n"
        f"Below are {len(chunk_results)} structured modality intelligence outputs "
        f"from different article chunks.\n"
        f"Merge them into ONE unified intelligence report with ZERO signal loss.\n\n"
        f"{all_chunks}"
    )

# ── LLM CALL ─────────────────────────────────────────────────────────────────

def call_llm(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model":   MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens":      16384,
        "temperature":     0.3,
        "top_p":           0.95,
        "stream":          False,
        "response_format": {"type": "json_object"},
    }

    last_error: str = "unknown"

    for attempt in range(1, MAX_RETRIES + 1):

        if attempt > 1:
            wait = BACKOFF_BASE * (2 ** (attempt - 2))
            print(f"[LLM] Retry {attempt}/{MAX_RETRIES} — backing off {wait}s...")
            time.sleep(wait)

        print(f"[LLM] Attempt {attempt}/{MAX_RETRIES} — sending request...", flush=True)

        try:
            resp = requests.post(
                INVOKE_URL,
                headers=HEADERS,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

        except requests.exceptions.Timeout:
            last_error = f"timed out after {REQUEST_TIMEOUT}s"
            print(f"[LLM] ✗ Attempt {attempt} failed: {last_error}")
            continue

        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            last_error = f"HTTP {code}"
            if 400 <= code < 500:
                raise RuntimeError(
                    f"HTTP {code} — aborting retries (client error): {exc}"
                ) from exc
            print(f"[LLM] ✗ Attempt {attempt} failed: {last_error}")
            continue

        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            print(f"[LLM] ✗ Attempt {attempt} network error: {last_error}")
            continue

        try:
            envelope = resp.json()
        except json.JSONDecodeError as exc:
            last_error = f"response body not valid JSON: {exc}"
            print(f"[LLM] ✗ Attempt {attempt} failed: {last_error}")
            continue

        content = (
            envelope
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

        if not content:
            last_error = "empty content in API response"
            print(f"[LLM] ✗ Attempt {attempt} failed: {last_error}")
            continue

        print(f"[LLM] ✓ Response received ({len(content):,} chars)")
        return content

    raise RuntimeError(
        f"LLM call failed after {MAX_RETRIES} attempts. Last error: {last_error}"
    )

# ── CHUNK PROCESSING ──────────────────────────────────────────────────────────

def generate_chunk_results(articles: list, query: str) -> list[str]:
    """Process articles in chunks of CHUNK_SIZE, return list of raw JSON strings."""
    chunks = list(chunk_articles(articles, CHUNK_SIZE))
    print(f"[INFO] Total article chunks : {len(chunks)}")

    results = []
    for idx, chunk in enumerate(chunks, 1):
        print(f"\n[INFO] Processing chunk {idx}/{len(chunks)} ({len(chunk)} articles)...")
        print("─" * 60)

        prompt = build_chunk_prompt(chunk, query)
        if not prompt:
            print(f"[WARN] Chunk {idx}: no valid article bodies — skipping")
            continue

        try:
            raw = call_llm(SYSTEM_PROMPT, prompt)
            if raw:
                results.append(raw)
        except RuntimeError as e:
            print(f"[ERROR] Chunk {idx} failed: {e} — skipping")

    return results


def merge_chunk_results(chunk_results: list[str], query: str) -> str:
    """
    If only one chunk result exists, return it directly.
    Otherwise send all chunk results to the LLM for merging.
    """
    if len(chunk_results) == 1:
        print("\n[INFO] Single chunk — skipping merge step.")
        return chunk_results[0]

    print(f"\n[INFO] Merging {len(chunk_results)} chunk results...\n")
    print("─" * 60)

    merge_prompt = build_merge_prompt(chunk_results, query)

    try:
        merged = call_llm(MERGE_SYSTEM_PROMPT, merge_prompt)
        return merged
    except RuntimeError as e:
        print(f"[ERROR] Merge failed: {e} — falling back to first chunk result")
        return chunk_results[0]

# ── JSON PARSER ───────────────────────────────────────────────────────────────

def parse_llm_response(raw: str) -> list:
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj.get("modality_intelligence", [])
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            return obj.get("modality_intelligence", [])
        except json.JSONDecodeError:
            pass

    print("[WARN] parse_llm_response: could not extract JSON — returning []")
    return []

# ── SCHEMA VALIDATOR ──────────────────────────────────────────────────────────

REQUIRED_MODALITY_KEYS = {
    "modality_name",
    "evolution_and_direction",
    "growth_trajectory",
    "commercial_value",
    "collaborations_and_deals",
    "key_players",
    "bottlenecks",
    "implementation_stage",
    "risks",
    "collaboration_opportunities_for_startup",
}

def _ensure_str_list(obj, key):
    val = obj.get(key, [])
    if not isinstance(val, list):
        obj[key] = []
        return
    result = []
    for item in val:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            for v in item.values():
                if isinstance(v, str) and v.strip():
                    result.append(v.strip())
                    break
    obj[key] = result


def _normalize_player_list(raw_list: list) -> list:
    result = []
    for entry in raw_list:
        if isinstance(entry, str) and entry.strip():
            result.append({"company_name": entry.strip(), "reasoning": ""})
        elif isinstance(entry, dict):
            name      = (entry.get("company_name") or "").strip()
            reasoning = (entry.get("reasoning") or "").strip()
            if name:
                result.append({"company_name": name, "reasoning": reasoning})
    return result


def validate_items(raw_items: list) -> tuple[list, int]:
    valid   = []
    dropped = 0

    for idx, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            print(f"[VALIDATE] Item {idx}: not a dict — dropped")
            dropped += 1
            continue

        missing = REQUIRED_MODALITY_KEYS - item.keys()
        if missing:
            print(f"[VALIDATE] Item {idx}: missing top-level keys {missing} — dropped")
            dropped += 1
            continue

        modality_name = item.get("modality_name", "")
        if not isinstance(modality_name, str) or not modality_name.strip():
            print(f"[VALIDATE] Item {idx}: empty modality_name — dropped")
            dropped += 1
            continue

        # collaborations_and_deals
        collab = item.get("collaborations_and_deals", {})
        if not isinstance(collab, dict):
            print(f"[VALIDATE] Item {idx} ({modality_name}): collaborations_and_deals not a dict — dropped")
            dropped += 1
            continue
        if not isinstance(collab.get("deals", []), list):
            item["collaborations_and_deals"]["deals"] = []

        total = collab.get("total_number_of_deals", 0)
        if not isinstance(total, int):
            try:
                item["collaborations_and_deals"]["total_number_of_deals"] = int(total)
            except (TypeError, ValueError):
                item["collaborations_and_deals"]["total_number_of_deals"] = len(collab.get("deals", []))

        # key_players
        kp = item.get("key_players", {})
        if isinstance(kp, dict):
            kp["major"]    = _normalize_player_list(kp.get("major", []))
            kp["emerging"] = _normalize_player_list(kp.get("emerging", []))
            _ensure_str_list(kp, "roles")

        # risks
        risks = item.get("risks", {})
        if isinstance(risks, dict):
            _ensure_str_list(risks, "key_risks")

        # bottlenecks
        bn = item.get("bottlenecks", {})
        if isinstance(bn, dict):
            _ensure_str_list(bn, "technical")
            _ensure_str_list(bn, "business")

        # collaboration_opportunities_for_startup
        collab_opps = item.get("collaboration_opportunities_for_startup", {})
        if isinstance(collab_opps, dict):
            if not isinstance(collab_opps.get("target_companies", []), list):
                item["collaboration_opportunities_for_startup"]["target_companies"] = []

        valid.append(item)

    return valid, dropped

# ── OUTPUT BUILDER ────────────────────────────────────────────────────────────

def build_output(modality_items: list, query: str, article_count: int, dropped: int) -> dict:
    return {
        "meta": {
            "query":                query,
            "articles_used":        article_count,
            "modalities_extracted": len(modality_items),
            "items_dropped":        dropped,
            "generated_at":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "modality_intelligence": modality_items,
    }

# ── DATEWISE HISTORY ──────────────────────────────────────────────────────────

def append_to_history(
    modality_items: list,
    query: str,
    history_file: str = BRIEFS_HISTORY_FILE,
) -> None:
    today = datetime.now().strftime("%Y-%m-%d")

    p = Path(history_file)
    if p.exists():
        try:
            history = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            history = {}
    else:
        history = {}

    if today not in history:
        history[today] = {}

    added = 0
    for item in modality_items:
        modality_name = item.get("modality_name", "unknown").strip()

        if modality_name not in history[today]:
            history[today][modality_name] = []

        trend_sig = (
            item.get("growth_trajectory", {}).get("trend", "")
            if isinstance(item.get("growth_trajectory"), dict)
            else ""
        )
        existing_sigs = {
            (r.get("modality_name"), r.get("_trend_sig", ""))
            for r in history[today][modality_name]
        }
        if (modality_name, trend_sig) in existing_sigs:
            continue

        record = dict(item)
        record["query"]      = query
        record["_trend_sig"] = trend_sig
        history[today][modality_name].append(record)
        added += 1

    history = dict(sorted(history.items(), reverse=True))

    p.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[HISTORY] Appended {added} items → {history_file}")
    print(f"[HISTORY] Dates stored: {list(history.keys())[:5]} ...")


def print_history_summary(history_file: str = BRIEFS_HISTORY_FILE) -> None:
    p = Path(history_file)
    if not p.exists():
        return

    try:
        history = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return

    print("\n" + "═" * 64)
    print("  BRIEFS HISTORY SUMMARY")
    print("═" * 64)
    print(f"  {'Date':<14}  {'Modality':<28}  {'Items':>5}")
    print("  " + "─" * 52)

    for date in sorted(history.keys(), reverse=True):
        modalities = history[date]
        for modality, items in modalities.items():
            print(f"  {date:<14}  {modality:<28}  {len(items):>5}")

    total = sum(
        len(items)
        for day in history.values()
        for items in day.values()
    )
    print("═" * 64)
    print(f"  Total items stored : {total}")
    print(f"  Total dates        : {len(history)}")
    print("═" * 64 + "\n")

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Pharma Intelligence Brief Generator")
    p.add_argument("--input",  "-i", default="text.json",   help="Extracted articles JSON")
    p.add_argument("--output", "-o", default="briefs.json", help="Output JSON file")
    p.add_argument("--query",  "-q", required=True,         help="Therapeutic focus e.g. 'PROTAC'")
    p.add_argument(
        "--no-history", action="store_true",
        help="Skip writing to briefs_history.json"
    )
    return p.parse_args()

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"[ERROR] Input file not found: {src}")

    try:
        with open(src, encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        sys.exit(f"[ERROR] Failed to load JSON: {e}")

    if isinstance(raw_data, dict) and "articles" in raw_data:
        articles = raw_data["articles"]
    elif isinstance(raw_data, list):
        articles = raw_data
    else:
        sys.exit("[ERROR] Unexpected JSON format in input file.")

    valid_articles = [a for a in articles if (a.get("body") or "").strip()]
    skipped        = len(articles) - len(valid_articles)

    print(f"[INFO] Loaded   : {len(articles)} articles")
    print(f"[INFO] Valid    : {len(valid_articles)}")
    if skipped:
        print(f"[INFO] Skipped  : {skipped} (no body text)")
    print(f"[INFO] Query    : {args.query}")
    print(f"[INFO] Chunk sz : {CHUNK_SIZE} articles per chunk\n")

    if not valid_articles:
        sys.exit("[WARN] No valid articles found.")

    # ── STEP 1: Process articles in chunks of 15 ─────────────────────────────
    print("[INFO] Generating per-chunk modality intelligence...\n")
    print("─" * 60)

    chunk_results = generate_chunk_results(valid_articles, args.query)

    if not chunk_results:
        sys.exit("[WARN] No chunk results generated.")

    # ── STEP 2: Merge all chunk results into one ──────────────────────────────
    raw_response = merge_chunk_results(chunk_results, args.query)

    if not raw_response:
        sys.exit("[WARN] Empty response from merge step.")

    # ── STEP 3: Parse + validate ──────────────────────────────────────────────
    raw_items               = parse_llm_response(raw_response)
    modality_items, dropped = validate_items(raw_items)

    print(f"\n[INFO] Chunk results         : {len(chunk_results)}")
    print(f"[INFO] Raw items parsed      : {len(raw_items)}")
    print(f"[INFO] Passed validation     : {len(modality_items)}")
    if dropped:
        print(f"[WARN] Dropped bad items     : {dropped}")

    # ── STEP 4: Write output ──────────────────────────────────────────────────
    output_data = build_output(modality_items, args.query, len(valid_articles), dropped)
    out_path    = Path(args.output)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"[INFO] Saved → {out_path}")

    if not args.no_history and modality_items:
        append_to_history(modality_items, args.query)
        print_history_summary()

    if modality_items:
        print("\n── PREVIEW (first 3 modality blocks) ───────────────────────")
        for item in modality_items[:3]:
            name  = item.get("modality_name", "?")
            trend = item.get("growth_trajectory", {}).get("trend", "N/A")
            stage = item.get("implementation_stage", {}).get("stage", "N/A")
            deals = len(item.get("collaborations_and_deals", {}).get("deals", []))
            major_players = item.get("key_players", {}).get("major", [])
            major_names   = [
                p.get("company_name", p) if isinstance(p, dict) else p
                for p in major_players
            ][:3]
            print(f"  [{name}]")
            print(f"    Trend         : {trend[:100]}")
            print(f"    Stage         : {stage[:100]}")
            print(f"    Deals         : {deals} extracted")
            print(f"    Major players : {', '.join(major_names)}")
        print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
