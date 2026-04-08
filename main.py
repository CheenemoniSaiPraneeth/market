"""
main.py — Master Orchestrator for BiotechIntelligence Pipeline

Runs ALL 4 modalities on every execution.
Each modality uses expanded keyword sets (3-5 related keywords).
Results are APPENDED to briefs.json (not replaced), tagged with run_date.
briefs_history.json also appended per modality.

Usage:
  python main.py                           # run all 4 modalities
  python main.py --modality bispecific     # run a specific one only
  python main.py --modality "molecular glue"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── MODALITY DEFINITIONS (all 4, expanded keywords) ──────────────────────────

ALL_MODALITIES = [
    {
        "name":     "bispecific antibodies",
        "label":    "Bispecific Antibodies",
        "keywords": ["bispecific antibody", "bispecific", "BiTE", "T-cell engager", "bsAb"],
    },
    {
        "name":     "monoclonal antibodies",
        "label":    "Monoclonal Antibodies",
        "keywords": ["monoclonal antibody", "monoclonal", "mAb", "therapeutic antibody", "IgG therapy"],
    },
    {
        "name":     "molecular glues",
        "label":    "Molecular Glues",
        "keywords": ["molecular glue", "molecular glue degrader", "targeted protein degradation", "E3 ligase", "GSPT1"],
    },
    {
        "name":     "gene editing",
        "label":    "Gene Editing",
        "keywords": ["gene editing", "CRISPR", "Cas9", "base editing", "prime editing"],
    },
]

# Market research sources — keyword injected per run
MARKET_SOURCES_TEMPLATE = {
    "Roots Analysis":       {"press_url": "https://www.rootsanalysis.com/reports-category/healthcare.html?search={keyword}"},
    "Precedence Research":  {"press_url": "https://www.precedenceresearch.com/search.php?q={keyword}"},
    "DelveInsight":         {"press_url": "https://www.delveinsight.com/search?q={keyword}"},
    "Towards Healthcare":   {"press_url": "https://www.towardshealthcare.com/search.php?q={keyword}"},
    "MarketsandMarkets":    {"press_url": "https://www.marketsandmarkets.com/search.asp?search={keyword}"},
    "Research and Markets": {"press_url": "https://www.researchandmarkets.com/search.asp?q={keyword}"},
    "BIS Research":         {"press_url": "https://bisresearch.com/search/search_result?s_type=all&sort_type=1&query={keyword}"},
    "Frost & Sullivan":     {"press_url": "https://www.frost.com/?s={keyword}"},
    "IQVIA":                {"press_url": "https://www.iqvia.com/search#q={keyword}"},
}

# ── FILE PATHS ────────────────────────────────────────────────────────────────

COMPANY_FILE    = "company_today.json"
HARVESTED_FILE  = "companies_suii.json"
MASTER_FILE     = "empty.json"
TODAY_FILE      = "charith.json"
AI_TEXT_FILE    = "ai_text.json"
BRIEFS_FILE     = "briefs.json"
HISTORY_FILE    = "briefs_history.json"
STATUS_FILE     = "run_status.json"

MAX_LINKS_FOR_LLM = 100

# ── HELPERS ───────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def write_status(stage, modality, keyword, extra=None):
    data = {
        "stage":        stage,
        "modality":     modality,
        "keyword":      keyword,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        data.update(extra)
    Path(STATUS_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_company_file(keywords):
    """Write company_today.json with all expanded keywords — one entry per source+keyword."""
    sources = {}
    for keyword in keywords:
        kw_encoded = keyword.replace(" ", "+")
        for name, meta in MARKET_SOURCES_TEMPLATE.items():
            key = f"{name} [{keyword}]"
            url = meta["press_url"].replace("{keyword}", kw_encoded)
            sources[key] = {"press_url": url}
    Path(COMPANY_FILE).write_text(json.dumps(sources, indent=2), encoding="utf-8")
    log(f"Written {COMPANY_FILE} with {len(sources)} entries for {len(keywords)} keywords: {keywords}")


def run_script(script, args=None):
    cmd = [sys.executable, script] + (args or [])
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def cap_today_links(max_links=MAX_LINKS_FOR_LLM):
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


def append_briefs(temp_file, label):
    """
    Append modality items from temp_file into briefs.json.
    - Tags every item with run_date and modality_label.
    - Replaces today's items for this modality (re-run safe).
    - Never removes other modalities or previous dates.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    src = Path(temp_file)
    if not src.exists():
        log(f"[APPEND] {temp_file} not found — skipping")
        return

    try:
        new_data  = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[APPEND] Parse error {temp_file}: {e}")
        return

    new_items = new_data.get("modality_intelligence", [])
    if not new_items:
        log(f"[APPEND] No items in {temp_file}")
        return

    for item in new_items:
        item["run_date"]       = today
        item["modality_label"] = label

    briefs_path = Path(BRIEFS_FILE)
    if briefs_path.exists():
        try:
            existing = json.loads(briefs_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {"modality_intelligence": [], "meta": {}}
    else:
        existing = {"modality_intelligence": [], "meta": {}}

    kept = [
        it for it in existing.get("modality_intelligence", [])
        if not (it.get("run_date") == today and it.get("modality_label") == label)
    ]
    kept.extend(new_items)

    existing["modality_intelligence"] = kept
    if "meta" not in existing:
        existing["meta"] = {}
    existing["meta"]["last_updated"]     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing["meta"]["total_modalities"] = len({it.get("modality_label") for it in kept})
    existing["meta"]["run_date"]         = today

    briefs_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[APPEND] +{len(new_items)} items for '{label}' → {BRIEFS_FILE} (total: {len(kept)})")


# ── PER-MODALITY PIPELINE ─────────────────────────────────────────────────────

def run_modality(modality):
    label      = modality["label"]
    keywords   = modality["keywords"]
    primary_kw = keywords[0]

    log(f"\n{'='*60}")
    log(f"  MODALITY : {label}")
    log(f"  KEYWORDS : {', '.join(keywords)}")
    log(f"{'='*60}")
    write_status("starting", label, primary_kw)

    # STEP 1: Multi-keyword source file
    log("STEP 1: Building multi-keyword company source file")
    build_company_file(keywords)

    # STEP 2: Harvest
    log("STEP 2: Harvesting candidate links (harvester.py)")
    write_status("harvesting", label, primary_kw)
    hc = Path("harvester.py").read_text(encoding="utf-8")
    hc = hc.replace('INPUT_FILE = "company.json"', f'INPUT_FILE = "{COMPANY_FILE}"')
    hc = hc.replace('OUTPUT_FILE = "companies_suii.json"', f'OUTPUT_FILE = "{HARVESTED_FILE}"')
    Path("harvester_run.py").write_text(hc, encoding="utf-8")
    rc = run_script("harvester_run.py")
    if rc != 0:
        log(f"[WARN] harvester exited {rc}")

    # STEP 3: Delta filter
    log("STEP 3: Delta filter (today_info.py)")
    write_status("filtering", label, primary_kw)
    tc = Path("today_info.py").read_text(encoding="utf-8")
    tc = tc.replace('INPUT_FILE = "companies_suii.json"', f'INPUT_FILE = "{HARVESTED_FILE}"')
    tc = tc.replace('MASTER_FILE = "empty.json"',         f'MASTER_FILE = "{MASTER_FILE}"')
    tc = tc.replace('TODAY_FILE = "charith.json"',        f'TODAY_FILE = "{TODAY_FILE}"')
    Path("today_info_run.py").write_text(tc, encoding="utf-8")
    rc = run_script("today_info_run.py")
    if rc != 0:
        log(f"[WARN] today_info exited {rc}")

    # STEP 4: Cap links
    n_links = cap_today_links(MAX_LINKS_FOR_LLM)
    log(f"STEP 4: Using {n_links} article links")
    write_status("extracting_text", label, primary_kw, {"links_to_process": n_links})

    if n_links == 0:
        log("[WARN] No article links — skipping LLM steps for this modality")
        write_status("done_no_articles", label, primary_kw, {"links_processed": 0})
        return False

    # STEP 5: Fetch text
    log("STEP 5: Extracting article text (llm.py)")
    lc = Path("llm.py").read_text(encoding="utf-8")
    lc = lc.replace('INPUT_FILE = "charith.json"',        f'INPUT_FILE = "{TODAY_FILE}"')
    lc = lc.replace('OUTPUT_FILE = "charith_text.json"',  f'OUTPUT_FILE = "{AI_TEXT_FILE}"')
    Path("llm_run.py").write_text(lc, encoding="utf-8")
    rc = run_script("llm_run.py")
    if rc != 0:
        log(f"[WARN] llm.py exited {rc}")

    if not Path(AI_TEXT_FILE).exists():
        log("[WARN] ai_text.json missing — skipping summary")
        write_status("done_no_text", label, primary_kw)
        return False

    # STEP 6: LLM summary
    log("STEP 6: Generating intelligence brief (summary.py)")
    write_status("analyzing", label, primary_kw)
    safe_label  = label.lower().replace(" ", "_")
    temp_briefs = f"briefs_temp_{safe_label}.json"
    rc = run_script("summary.py", [
        "--input",    AI_TEXT_FILE,
        "--output",   temp_briefs,
        "--query",    primary_kw,
        "--modality", label,
    ])
    if rc != 0:
        log(f"[WARN] summary.py exited {rc}")

    # STEP 7: Append to main briefs.json
    append_briefs(temp_briefs, label)

    write_status("complete", label, primary_kw, {
        "briefs_ready":    True,
        "links_processed": n_links,
    })
    log(f"✓ {label} DONE")
    return True


# ── MASTER PIPELINE ───────────────────────────────────────────────────────────

def run_all(selected_modality=None):
    modalities = ALL_MODALITIES

    if selected_modality:
        kw = selected_modality.lower().strip()
        modalities = [
            m for m in ALL_MODALITIES
            if kw in m["name"] or any(kw in k.lower() for k in m["keywords"])
        ]
        if not modalities:
            sys.exit(f"[ERROR] Unknown modality '{selected_modality}'. "
                     "Options: bispecific | monoclonal | 'molecular glue' | 'gene editing'")

    log(f"Starting pipeline for {len(modalities)} modality/modalities")
    results = {}

    for m in modalities:
        try:
            ok = run_modality(m)
            results[m["label"]] = "OK" if ok else "No articles"
        except Exception as e:
            log(f"[ERROR] {m['label']}: {e}")
            results[m["label"]] = f"ERROR: {e}"

    log("\n" + "="*60)
    log("  ALL RUNS COMPLETE")
    log("="*60)
    for label, status in results.items():
        log(f"  {label:<32} {status}")
    log("="*60)

    write_status("all_complete", "All Modalities", "all", {
        "results": results,
        "modalities_run": len(modalities),
        "briefs_ready": True,
    })


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="BiotechIntelligence Orchestrator")
    p.add_argument("--modality", "-m", default=None,
                   help="Run a single modality instead of all 4")
    args = p.parse_args()
    run_all(args.modality)


if __name__ == "__main__":
    main()
