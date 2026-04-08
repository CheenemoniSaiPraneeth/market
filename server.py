"""
server.py — BiotechIntelligence Backend Server

Serves:
  GET /                          → dashboard HTML
  GET /graph                     → graph viewer
  GET /api/graph                 → graph for ALL modalities in briefs.json
  GET /api/graph/<modality>      → graph for a specific modality (latest date)
  GET /api/graph/<modality>/<date> → graph for a modality on a specific date
  GET /api/status                → run_status.json
  GET /api/history               → briefs_history.json summary
  GET /api/briefs                → raw briefs.json
  GET /api/counts                → per-modality article counts with last_scraped date
"""

from flask import Flask, jsonify, send_from_directory, make_response, request
import json
import os
from datetime import datetime
from pathlib import Path

app = Flask(__name__)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
BRIEFS_FILE   = os.path.join(BASE_DIR, "briefs.json")
HISTORY_FILE  = os.path.join(BASE_DIR, "briefs_history.json")
STATUS_FILE   = os.path.join(BASE_DIR, "run_status.json")

MODALITIES = [
    {"label": "Bispecific Antibodies",  "color": "#34d399", "icon": "◎",
     "keys":  ["bispecific"]},
    {"label": "Monoclonal Antibodies",  "color": "#4f9eff", "icon": "⊕",
     "keys":  ["monoclonal"]},
    {"label": "Molecular Glues",        "color": "#fbbf24", "icon": "◈",
     "keys":  ["molecular glue", "molecular_glue"]},
    {"label": "Gene Editing",           "color": "#f87171", "icon": "◇",
     "keys":  ["gene editing", "gene_editing", "crispr"]},
]

# ── helpers ───────────────────────────────────────────────────────────────────

def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _label_matches(label: str, query: str) -> bool:
    """Case-insensitive label match."""
    return query.lower().strip() in label.lower()


def _get_items_for_modality(modality_label: str, run_date: str = None) -> list:
    """
    Return modality_intelligence items from briefs.json that match the modality.
    If run_date is given, filter to that date only.
    If run_date is None, return the LATEST available date for that modality.
    """
    data = load_json(BRIEFS_FILE, {})
    all_items = data.get("modality_intelligence", [])

    matched = [
        it for it in all_items
        if _label_matches(it.get("modality_label", ""), modality_label)
    ]

    if not matched:
        # Fallback: match by modality_name field
        matched = [
            it for it in all_items
            if _label_matches(it.get("modality_name", ""), modality_label)
        ]

    if not matched:
        return []

    if run_date:
        matched = [it for it in matched if it.get("run_date") == run_date]
    else:
        # Get the most recent run_date for this modality
        dates = sorted({it.get("run_date", "") for it in matched if it.get("run_date")}, reverse=True)
        if dates:
            latest = dates[0]
            matched = [it for it in matched if it.get("run_date") == latest]

    return matched


def _build_graph_for_items(items: list):
    if not items:
        return {"nodes": [], "edges": [], "meta": {}}
    raw = {"modality_intelligence": items}
    try:
        from graph_builder import build_graph
        return build_graph(raw)
    except Exception as e:
        return {"nodes": [], "edges": [], "meta": {}, "error": str(e)}


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
    Default graph — accepts ?modality= and optional ?date= params.
    Without params, returns graph for ALL items in latest run.
    """
    modality_param = request.args.get("modality", "").strip()
    date_param     = request.args.get("date", "").strip() or None

    if modality_param:
        items = _get_items_for_modality(modality_param, date_param)
        if not items:
            return no_cache(make_response(jsonify({
                "nodes": [], "edges": [], "meta": {},
                "no_data": True,
                "message": f"No data for '{modality_param}'"
                           + (f" on {date_param}" if date_param else " (no data yet)"),
            })))
    else:
        data = load_json(BRIEFS_FILE, {})
        items = data.get("modality_intelligence", [])
        if not items:
            return no_cache(make_response(jsonify({
                "nodes": [], "edges": [], "meta": {},
                "no_data": True, "message": "briefs.json is empty",
            })))

    graph = _build_graph_for_items(items)
    return no_cache(make_response(jsonify(graph)))


@app.route("/api/graph/<path:modality_label>")
def api_graph_modality(modality_label: str):
    """
    GET /api/graph/Bispecific%20Antibodies
    GET /api/graph/Bispecific%20Antibodies/2026-04-07
    """
    # Allow optional trailing /YYYY-MM-DD
    parts      = modality_label.rsplit("/", 1)
    import re
    if len(parts) == 2 and re.match(r"\d{4}-\d{2}-\d{2}$", parts[1]):
        label, run_date = parts[0], parts[1]
    else:
        label, run_date = modality_label, None

    items = _get_items_for_modality(label, run_date)
    if not items:
        return no_cache(make_response(jsonify({
            "nodes": [], "edges": [], "meta": {},
            "error": f"No data for '{label}'"
                     + (f" on {run_date}" if run_date else ""),
        }), 404))

    graph = _build_graph_for_items(items)
    return no_cache(make_response(jsonify(graph)))


@app.route("/api/status")
def api_status():
    status = load_json(STATUS_FILE, {
        "stage": "idle", "modality": "—", "keyword": "—", "last_updated": "never"
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


@app.route("/api/counts")
def api_counts():
    """Per-modality counts with last_scraped date — read from briefs.json run_date tags."""
    data       = load_json(BRIEFS_FILE, {})
    all_items  = data.get("modality_intelligence", [])

    result = []
    for m in MODALITIES:
        label = m["label"]
        matched = [
            it for it in all_items
            if _label_matches(it.get("modality_label", it.get("modality_name", "")), label)
        ]
        dates = sorted({it.get("run_date", "") for it in matched if it.get("run_date")}, reverse=True)
        result.append({
            "label":        label,
            "color":        m["color"],
            "icon":         m["icon"],
            "count":        len(matched),
            "last_scraped": dates[0] if dates else "Never",
            "all_dates":    dates[:5],  # last 5 run dates for this modality
        })

    return no_cache(make_response(jsonify(result)))


@app.route("/api/dates/<path:modality_label>")
def api_dates_for_modality(modality_label: str):
    """Return all available run dates for a given modality."""
    data      = load_json(BRIEFS_FILE, {})
    all_items = data.get("modality_intelligence", [])
    matched   = [
        it for it in all_items
        if _label_matches(it.get("modality_label", it.get("modality_name", "")), modality_label)
    ]
    dates = sorted({it.get("run_date", "") for it in matched if it.get("run_date")}, reverse=True)
    return no_cache(make_response(jsonify({"modality": modality_label, "dates": dates})))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5053))
    app.run(debug=True, port=port)
