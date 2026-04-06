"""
server.py — BiotechIntelligence Backend Server

Serves:
  GET /              → dashboard HTML (PrePrint Intelligence style)
  GET /api/graph     → graph data from briefs.json (for D3 viz)
  GET /api/status    → run_status.json (pipeline progress)
  GET /api/history   → briefs_history.json summary
  GET /api/briefs    → raw briefs.json modality data
  GET /api/schedule  → today's + upcoming modality schedule

Run locally:
  python server.py

Deploy on Render:
  Build Command : pip install flask gunicorn
  Start Command : gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120
"""

from flask import Flask, jsonify, send_from_directory, make_response
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

app = Flask(__name__)

# ── File paths (same dir as server.py) ───────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
BRIEFS_FILE   = os.path.join(BASE_DIR, "briefs.json")
HISTORY_FILE  = os.path.join(BASE_DIR, "briefs_history.json")
STATUS_FILE   = os.path.join(BASE_DIR, "run_status.json")
GRAPH_HTML    = os.path.join(BASE_DIR, "graph.html")

MODALITY_SCHEDULE = {
    0: {"label": "Bispecific Antibodies",  "keyword": "bispecific",     "color": "#34d399", "icon": "◎"},
    1: {"label": "Monoclonal Antibodies",  "keyword": "monoclonal",     "color": "#4f9eff", "icon": "⊕"},
    2: {"label": "Molecular Glues",        "keyword": "molecular glue", "color": "#fbbf24", "icon": "◈"},
    3: {"label": "Gene Editing",           "keyword": "gene editing",   "color": "#f87171", "icon": "◇"},
    4: {"label": "Bispecific Antibodies",  "keyword": "bispecific",     "color": "#34d399", "icon": "◎"},
    5: {"label": "Monoclonal Antibodies",  "keyword": "monoclonal",     "color": "#4f9eff", "icon": "⊕"},
    6: {"label": "Molecular Glues",        "keyword": "molecular glue", "color": "#fbbf24", "icon": "◈"},
}

# ── no-cache helper ───────────────────────────────────────────────────────────
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

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main dashboard HTML."""
    return send_from_directory(BASE_DIR, "dashboard.html")

@app.route("/graph")
def graph_view():
    """Serve the D3 graph viz."""
    return send_from_directory(BASE_DIR, "graph.html")

@app.route("/api/graph")
def api_graph():
    """Build and return graph data from current briefs.json."""
    raw = load_json(BRIEFS_FILE, {})
    if not raw:
        return no_cache(make_response(jsonify({"nodes": [], "edges": [], "meta": {}})))

    try:
        from graph_builder import build_graph
        data = build_graph(raw)
    except Exception as e:
        return no_cache(make_response(jsonify({"error": str(e), "nodes": [], "edges": [], "meta": {}})))

    return no_cache(make_response(jsonify(data)))

@app.route("/api/status")
def api_status():
    """Return current pipeline run status."""
    status = load_json(STATUS_FILE, {
        "stage":    "idle",
        "modality": "—",
        "keyword":  "—",
        "last_updated": "never"
    })
    return no_cache(make_response(jsonify(status)))

@app.route("/api/briefs")
def api_briefs():
    """Return current briefs.json."""
    data = load_json(BRIEFS_FILE, {"modality_intelligence": [], "meta": {}})
    return no_cache(make_response(jsonify(data)))

@app.route("/api/history")
def api_history():
    """Return briefs_history.json with summary counts."""
    history = load_json(HISTORY_FILE, {})

    summary = []
    for date in sorted(history.keys(), reverse=True):
        day_data = history[date]
        for modality, items in day_data.items():
            summary.append({
                "date":     date,
                "modality": modality,
                "count":    len(items),
            })

    return no_cache(make_response(jsonify({
        "summary":      summary,
        "total_dates":  len(history),
        "total_items":  sum(len(items) for d in history.values() for items in d.values()),
    })))

@app.route("/api/schedule")
def api_schedule():
    """Return the 7-day modality rotation schedule."""
    today_dow = datetime.now().weekday()
    days_out = []
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for offset in range(7):
        dow  = (today_dow + offset) % 7
        m    = MODALITY_SCHEDULE[dow]
        date = datetime.now() + timedelta(days=offset)
        days_out.append({
            "day_name":  day_names[(today_dow + offset) % 7],
            "date":      date.strftime("%Y-%m-%d"),
            "modality":  m["label"],
            "keyword":   m["keyword"],
            "color":     m["color"],
            "icon":      m["icon"],
            "is_today":  offset == 0,
        })

    return no_cache(make_response(jsonify(days_out)))

@app.route("/api/counts")
def api_counts():
    """Return article counts per modality from history."""
    history = load_json(HISTORY_FILE, {})

    counts = {
        "Bispecific Antibodies": 0,
        "Monoclonal Antibodies": 0,
        "Molecular Glues":       0,
        "Gene Editing":          0,
    }
    last_scraped = {}

    for date, day_data in history.items():
        for modality_name, items in day_data.items():
            for label in counts:
                if label.lower() in modality_name.lower() or modality_name.lower() in label.lower():
                    counts[label] += len(items)
                    if label not in last_scraped or date > last_scraped[label]:
                        last_scraped[label] = date

    result = []
    for label, color_icon in [
        ("Bispecific Antibodies", ("#34d399", "◎", ["bispecific", "BiTE", "T-cell engager"])),
        ("Monoclonal Antibodies", ("#4f9eff", "⊕", ["monoclonal", "mAb", "therapeutic antibody"])),
        ("Molecular Glues",       ("#fbbf24", "◈", ["molecular glue", "TPD", "E3 ligase"])),
        ("Gene Editing",          ("#f87171", "◇", ["CRISPR", "Cas9", "guide RNA"])),
    ]:
        color, icon, tags = color_icon
        result.append({
            "label":        label,
            "color":        color,
            "icon":         icon,
            "tags":         tags,
            "count":        counts[label],
            "last_scraped": last_scraped.get(label, "Never"),
        })

    return no_cache(make_response(jsonify(result)))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5053))
    app.run(debug=True, port=port)
