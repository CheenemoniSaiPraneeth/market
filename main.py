"""
main.py — Master Orchestrator for BiotechIntelligence Pipeline

Modality rotation (by day of week):
  Monday    → bispecific antibodies  (keyword: bispecific)
  Tuesday   → monoclonal antibodies  (keyword: monoclonal)
  Wednesday → molecular glues        (keyword: molecular glue)
  Thursday  → gene editing           (keyword: gene editing)
  Friday-Sun → cycles back starting Monday's modality

Pipeline per run:
  1. harvester.py  → scrape press/market URLs for candidate links
  2. today_info.py → delta filter (article-like URLs only, dedup vs master)
  3. llm.py        → fetch text from up to 30 links
  4. summary.py    → LLM analysis → briefs.json + briefs_history.json

Usage:
  python main.py              # auto-detect today's modality
  python main.py --modality bispecific
  python main.py --modality "molecular glue"
  python main.py --force      # ignore day rotation, run today's day
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── MODALITY ROTATION ─────────────────────────────────────────────────────────

MODALITY_SCHEDULE = {
    0: {"name": "bispecific antibodies",  "keyword": "bispecific",     "label": "Bispecific Antibodies"},
     1: {"name": "gene editing",           "keyword": "gene editing",   "label": "Gene Editing"},
    2: {"name": "monoclonal antibodies",  "keyword": "monoclonal",     "label": "Monoclonal Antibodies"},
    3: {"name": "molecular glues",        "keyword": "molecular glue", "label": "Molecular Glues"},
    4: {"name": "gene editing",           "keyword": "gene editing",   "label": "Gene Editing"},
    5: {"name": "bispecific antibodies",  "keyword": "bispecific",     "label": "Bispecific Antibodies"},
    6: {"name": "monoclonal antibodies",  "keyword": "monoclonal",     "label": "Monoclonal Antibodies"},
    7: {"name": "molecular glues",        "keyword": "molecular glue", "label": "Molecular Glues"},
}

# Market research sources — same base list, keyword injected per run
MARKET_SOURCES_TEMPLATE = {
    "Roots Analysis":             {"press_url": "https://www.rootsanalysis.com/reports-category/healthcare.html?search={keyword}"},
    "Precedence Research":        {"press_url": "https://www.precedenceresearch.com/search.php?q={keyword}"},
    "DelveInsight":               {"press_url": "https://www.delveinsight.com/search?q={keyword}"},
    "Towards Healthcare":         {"press_url": "https://www.towardshealthcare.com/search.php?q={keyword}"},
    "MarketsandMarkets":          {"press_url": "https://www.marketsandmarkets.com/search.asp?search={keyword}"},
    "Research and Markets":       {"press_url": "https://www.researchandmarkets.com/search.asp?q={keyword}"},
    "BIS Research":               {"press_url": "https://bisresearch.com/search/search_result?s_type=all&sort_type=1&query={keyword}"},
    "Frost & Sullivan":           {"press_url": "https://www.frost.com/?s={keyword}"},
    "IQVIA":                      {"press_url": "https://www.iqvia.com/search#q={keyword}"},
}

# ── FILE PATHS ────────────────────────────────────────────────────────────────

COMPANY_FILE    = "company_today.json"   # generated per run with keyword URLs
HARVESTED_FILE  = "companies_suii.json"
MASTER_FILE     = "empty.json"
TODAY_FILE      = "charith.json"
AI_TEXT_FILE    = "ai_text.json"
BRIEFS_FILE     = "briefs.json"
HISTORY_FILE    = "briefs_history.json"
STATUS_FILE     = "run_status.json"      # written for frontend to read

MAX_LINKS_FOR_LLM = 100  # cap passed to llm step

# ── HELPERS ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def write_status(stage: str, modality: str, keyword: str, extra: dict = None):
    data = {
        "stage":       stage,
        "modality":    modality,
        "keyword":     keyword,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        data.update(extra)
    Path(STATUS_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_today_modality() -> dict:
    dow = datetime.now().weekday()   # 0=Mon … 6=Sun
    return MODALITY_SCHEDULE[dow]


def build_company_file(keyword: str) -> None:
    """Write company_today.json with URLs interpolated for the current keyword."""
    kw_encoded = keyword.replace(" ", "+")
    sources = {}
    for name, meta in MARKET_SOURCES_TEMPLATE.items():
        url = meta["press_url"].replace("{keyword}", kw_encoded)
        sources[name] = {"press_url": url}
    Path(COMPANY_FILE).write_text(json.dumps(sources, indent=2), encoding="utf-8")
    log(f"Written {COMPANY_FILE} with {len(sources)} sources for keyword='{keyword}'")


def run_script(script: str, args: list = None) -> int:
    """Run a Python script as a subprocess, streaming output."""
    cmd = [sys.executable, script] + (args or [])
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def cap_today_links(max_links: int = MAX_LINKS_FOR_LLM) -> int:
    """Truncate charith.json to at most max_links entries."""
    p = Path(TODAY_FILE)
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    original = len(data)
    if original > max_links:
        data = data[:max_links]
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Capped {TODAY_FILE}: {original} → {max_links} links")
    return len(data)


# ── PATCH: override harvester INPUT_FILE and llm INPUT_FILE dynamically ───────

def patch_file_constant(filepath: str, old_val: str, new_val: str):
    """Simple in-place string replacement for INPUT_FILE constants."""
    content = Path(filepath).read_text(encoding="utf-8")
    patched = content.replace(f'INPUT_FILE = "{old_val}"', f'INPUT_FILE = "{new_val}"')
    Path(filepath).write_text(patched, encoding="utf-8")


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def run_pipeline(modality: dict):
    keyword = modality["keyword"]
    label   = modality["label"]

    log(f"=== PIPELINE START | {label} | keyword='{keyword}' ===")
    write_status("starting", label, keyword)

    # ── STEP 1: Build source file for this keyword ────────────────────────────
    log("STEP 1: Building company source file")
    build_company_file(keyword)

    # ── STEP 2: Harvest candidate links ───────────────────────────────────────
    log("STEP 2: Harvesting candidate links (harvester.py)")
    write_status("harvesting", label, keyword)

    # Patch harvester to use our dynamic company file
    harvester_content = Path("harvester.py").read_text(encoding="utf-8")
    patched_harvester = harvester_content.replace(
        'INPUT_FILE = "company.json"',
        f'INPUT_FILE = "{COMPANY_FILE}"'
    ).replace(
        'OUTPUT_FILE = "companies_suii.json"',
        f'OUTPUT_FILE = "{HARVESTED_FILE}"'
    )
    Path("harvester_run.py").write_text(patched_harvester, encoding="utf-8")

    rc = run_script("harvester_run.py")
    if rc != 0:
        log(f"[WARN] harvester exited with code {rc} — continuing anyway")

    # ── STEP 3: Delta filter → today's article-like URLs ─────────────────────
    log("STEP 3: Running delta filter (today_info.py)")
    write_status("filtering", label, keyword)

    # Patch today_info to use correct files
    today_content = Path("today_info.py").read_text(encoding="utf-8")
    patched_today = today_content.replace(
        'INPUT_FILE = "companies_suii.json"',
        f'INPUT_FILE = "{HARVESTED_FILE}"'
    ).replace(
        'MASTER_FILE = "empty.json"',
        f'MASTER_FILE = "{MASTER_FILE}"'
    ).replace(
        'TODAY_FILE = "charith.json"',
        f'TODAY_FILE = "{TODAY_FILE}"'
    )
    Path("today_info_run.py").write_text(patched_today, encoding="utf-8")

    rc = run_script("today_info_run.py")
    if rc != 0:
        log(f"[WARN] today_info exited with code {rc}")

    # ── STEP 4: Cap to 30 links ───────────────────────────────────────────────
    n_links = cap_today_links(MAX_LINKS_FOR_LLM)
    log(f"STEP 4: Using {n_links} article links for text extraction")
    write_status("extracting_text", label, keyword, {"links_to_process": n_links})

    if n_links == 0:
        log("[WARN] No article links found — skipping LLM steps")
        write_status("done_no_articles", label, keyword, {"links_processed": 0})
        return

    # ── STEP 5: Fetch text from links (llm.py) ────────────────────────────────
    log("STEP 5: Extracting article text (llm.py)")

    llm_content = Path("llm.py").read_text(encoding="utf-8")
    patched_llm = llm_content.replace(
        'INPUT_FILE = "charith.json"',
        f'INPUT_FILE = "{TODAY_FILE}"'
    ).replace(
        'OUTPUT_FILE = "charith_text.json"',
        f'OUTPUT_FILE = "{AI_TEXT_FILE}"'
    )
    Path("llm_run.py").write_text(patched_llm, encoding="utf-8")

    rc = run_script("llm_run.py")
    if rc != 0:
        log(f"[WARN] llm.py exited with code {rc}")

    # ── STEP 6: LLM analysis → briefs.json ───────────────────────────────────
    if not Path(AI_TEXT_FILE).exists():
        log("[WARN] ai_text.json not found — skipping summary")
        write_status("done_no_text", label, keyword)
        return

    log("STEP 6: Generating intelligence brief (summary.py)")
    write_status("analyzing", label, keyword)

    rc = run_script("summary.py", [
        "--input",  AI_TEXT_FILE,
        "--output", BRIEFS_FILE,
        "--query",  keyword,
    ])
    if rc != 0:
        log(f"[WARN] summary.py exited with code {rc}")

    # ── STEP 7: Write final status ────────────────────────────────────────────
    briefs_ok = Path(BRIEFS_FILE).exists()
    write_status("complete", label, keyword, {
        "briefs_ready":    briefs_ok,
        "links_processed": n_links,
    })
    log(f"=== PIPELINE COMPLETE | {label} | briefs_ready={briefs_ok} ===")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="BiotechIntelligence Master Orchestrator")
    p.add_argument(
        "--modality", "-m",
        help="Override modality: bispecific | monoclonal | 'molecular glue' | 'gene editing'"
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force run even if already ran today (not checked by default)"
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.modality:
        kw = args.modality.lower().strip()
        # Find matching schedule entry
        matched = None
        for entry in MODALITY_SCHEDULE.values():
            if kw in entry["keyword"] or kw in entry["name"]:
                matched = entry
                break
        if not matched:
            sys.exit(f"[ERROR] Unknown modality '{args.modality}'. "
                     f"Choose: bispecific | monoclonal | 'molecular glue' | 'gene editing'")
        modality = matched
    else:
        modality = get_today_modality()

    log(f"Today's modality: {modality['label']} (keyword: {modality['keyword']})")
    run_pipeline(modality)


if __name__ == "__main__":
    main()
