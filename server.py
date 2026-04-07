"""
server.py — BiotechIntelligence Backend Server

Serves:
  GET /                      → dashboard HTML
  GET /graph                 → graph viewer (reads ?modality= param)
  GET /api/graph             → graph for current briefs.json (last run)
  GET /api/graph/<modality>  → graph for a specific modality (from history)
  GET /api/status            → run_status.json
  GET /api/history           → briefs_history.json summary
  GET /api/briefs            → raw briefs.json
  GET /api/schedule          → 7-day rotation schedule
  GET /api/counts            → per-modality article counts
"""

from flask import Flask, jsonify, send_from_directory, make_response, request
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

app = Flask(__name__)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
BRIEFS_FILE   = os.path.join(BASE_DIR, "briefs.json")
HISTORY_FILE  = os.path.join(BASE_DIR, "briefs_history.json")
STATUS_FILE   = os.path.join(BASE_DIR, "run_status.json")

MODALITY_SCHEDULE = {
    0: {"label": "Bispecific Antibodies",  "keyword": "bispecific",     "color": "#34d399", "icon": "◎"},
    1: {"label": "Monoclonal Antibodies",  "keyword": "monoclonal",     "color": "#4f9eff", "icon": "⊕"},
    2: {"label": "Molecular Glues",        "keyword": "molecular glue", "color": "#fbbf24", "icon": "◈"},
    3: {"label": "Gene Editing",           "keyword": "gene editing",   "color": "#f87171", "icon": "◇"},
    4: {"label": "Bispecific Antibodies",  "keyword": "bispecific",     "color": "#34d399", "icon": "◎"},
    5: {"label": "Monoclonal Antibodies",  "keyword": "monoclonal",     "color": "#4f9eff", "icon": "⊕"},
    6: {"label": "Molecular Glues",        "keyword": "molecular glue", "color": "#fbbf24", "icon": "◈"},
}

# ── helpers ───────────────────────────────────────────────────────────────────

def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response

def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _modality_matches(history_key: str, query: str) -> bool:
    """
    Fuzzy match: does the history key (e.g. 'Antibody-Drug Conjugates (ADCs)')
    correspond to the requested modality label (e.g. 'Monoclonal Antibodies')?
    """
    hk = history_key.lower()
    q  = query.lower().strip()
    if q in hk or hk in q:
        return True
    # keyword aliases
    aliases = {
        "bispecific antibodies":  ["bispecific"],
        "monoclonal antibodies":  ["monoclonal", "mab", "antibody-drug", "adc"],
        "molecular glues":        ["molecular glue", "molecular_glue"],
        "gene editing":           ["gene editing", "gene_editing", "crispr"],
    }
    for label, keys in aliases.items():
        if q == label or any(k in q for k in keys):
            if any(k in hk for k in keys) or label in hk:
                return True
    return False

def _get_latest_modality_items(modality_label: str) -> list:
    """
    From briefs_history.json, find the most recent items for the given modality.
    Returns a list of modality intelligence items (to wrap in modality_intelligence key).
    """
    history = load_json(HISTORY_FILE, {})
    if not history:
        return []

    # Walk dates newest-first
    for date in sorted(history.keys(), reverse=True):
        day_data = history[date]
        for history_key, items in day_data.items():
            if _modality_matches(history_key, modality_label):
                if items:
                    # Return the most recent item for this modality on this date
                    return [items[-1]]

    return []

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "dashboard.html")

@app.route("/graph")
def graph_view():
    return send_from_directory(BASE_DIR, "graph.html")

@app.route("/api/graph")
def api_graph():
    """
    Default graph — use current briefs.json.
    Also accepts ?modality=<label> to serve a specific modality from history.
    """
    modality_param = request.args.get("modality", "").strip()

    if modality_param:
        items = _get_latest_modality_items(modality_param)
        if items:
            raw = {"modality_intelligence": items}
        else:
            # fallback: try briefs.json
            raw = load_json(BRIEFS_FILE, {})
    else:
        raw = load_json(BRIEFS_FILE, {})

    if not raw or not raw.get("modality_intelligence"):
        return no_cache(make_response(jsonify({
            "nodes": [], "edges": [], "meta": {},
            "error": f"No data found for modality '{modality_param}'"
        })))

    try:
        from graph_builder import build_graph
        data = build_graph(raw)
    except Exception as e:
        return no_cache(make_response(jsonify({
            "error": str(e), "nodes": [], "edges": [], "meta": {}
        })))

    return no_cache(make_response(jsonify(data)))


@app.route("/api/graph/<path:modality_label>")
def api_graph_modality(modality_label: str):
    """
    Dedicated per-modality graph endpoint.
    e.g. GET /api/graph/Bispecific%20Antibodies
    """
    items = _get_latest_modality_items(modality_label)

    if not items:
        return no_cache(make_response(jsonify({
            "nodes": [], "edges": [], "meta": {},
            "error": f"No data found for modality '{modality_label}'"
        }), 404))

    raw = {"modality_intelligence": items}

    try:
        from graph_builder import build_graph
        data = build_graph(raw)
    except Exception as e:
        return no_cache(make_response(jsonify({
            "error": str(e), "nodes": [], "edges": [], "meta": {}
        }), 500))

    return no_cache(make_response(jsonify(data)))


@app.route("/api/status")
def api_status():
    status = load_json(STATUS_FILE, {
        "stage": "idle", "modality": "—",
        "keyword": "—", "last_updated": "never"
    })
    return no_cache(make_response(jsonify(status)))


@app.route("/api/briefs")
def api_briefs():
    data = load_json(BRIEFS_FILE, {"modality_intelligence": [], "meta": {}})
    return no_cache(make_response(jsonify(data)))


@app.route("/api/history")
def api_history():
    history = load_json(HISTORY_FILE, {})
    summary = []
    for date in sorted(history.keys(), reverse=True):
        for modality, items in history[date].items():
            summary.append({"date": date, "modality": modality, "count": len(items)})
    return no_cache(make_response(jsonify({
        "summary":     summary,
        "total_dates": len(history),
        "total_items": sum(len(i) for d in history.values() for i in d.values()),
    })))


@app.route("/api/schedule")
def api_schedule():
    today_dow = datetime.now().weekday()
    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    days_out  = []
    for offset in range(7):
        dow  = (today_dow + offset) % 7
        m    = MODALITY_SCHEDULE[dow]
        date = datetime.now() + timedelta(days=offset)
        days_out.append({
            "day_name": day_names[(today_dow + offset) % 7],
            "date":     date.strftime("%Y-%m-%d"),
            "modality": m["label"],
            "keyword":  m["keyword"],
            "color":    m["color"],
            "icon":     m["icon"],
            "is_today": offset == 0,
        })
    return no_cache(make_response(jsonify(days_out)))


@app.route("/api/counts")
def api_counts():
    history = load_json(HISTORY_FILE, {})

    MODALITIES = [
        {
            "label": "Bispecific Antibodies",
            "color": "#34d399", "icon": "◎",
            "tags":  ["bispecific", "BiTE", "T-cell engager"],
            "keys":  ["bispecific"],
        },
        {
            "label": "Monoclonal Antibodies",
            "color": "#4f9eff", "icon": "⊕",
            "tags":  ["monoclonal", "mAb", "therapeutic antibody"],
            "keys":  ["monoclonal", "mab", "antibody-drug", "adc"],
        },
        {
            "label": "Molecular Glues",
            "color": "#fbbf24", "icon": "◈",
            "tags":  ["molecular glue", "TPD", "E3 ligase"],
            "keys":  ["molecular glue", "molecular_glue"],
        },
        {
            "label": "Gene Editing",
            "color": "#f87171", "icon": "◇",
            "tags":  ["CRISPR", "Cas9", "guide RNA"],
            "keys":  ["gene editing", "gene_editing", "crispr"],
        },
    ]

    counts      = {m["label"]: 0        for m in MODALITIES}
    last_scraped = {m["label"]: "Never" for m in MODALITIES}

    for date, day_data in history.items():
        for history_key, items in day_data.items():
            hk = history_key.lower()
            for m in MODALITIES:
                if any(k in hk for k in m["keys"]):
                    counts[m["label"]] += len(items)
                    if last_scraped[m["label"]] == "Never" or date > last_scraped[m["label"]]:
                        last_scraped[m["label"]] = date

    result = []
    for m in MODALITIES:
        result.append({
            "label":        m["label"],
            "color":        m["color"],
            "icon":         m["icon"],
            "tags":         m["tags"],
            "count":        counts[m["label"]],
            "last_scraped": last_scraped[m["label"]],
        })

    return no_cache(make_response(jsonify(result)))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5053))
    app.run(debug=True, port=port)
