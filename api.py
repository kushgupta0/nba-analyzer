"""
FastAPI server — feeds the CourtValue dashboard.

Start: uvicorn api:app --reload --port 8000
Docs:  http://localhost:8000/docs
"""

import glob
import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fetcher import fetch_player_data
from analyzer import run_pipeline

app = FastAPI(title="NBA Contract Value API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR

# ── helpers ──────────────────────────────────────────────────────────────────

def _latest_json() -> Optional[List[Dict[str, Any]]]:
    files = sorted(glob.glob(str(DATA_DIR / "nba_contracts_*.json")), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def _refresh_data(season: int):
    df = fetch_player_data(season=season)
    if df is not None:
        run_pipeline(df)


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/api/players")
def get_players(
    verdict: Optional[str] = None,
    hg: Optional[str] = None,
    tier: Optional[str] = None,
):
    data = _latest_json()
    if data is None:
        raise HTTPException(503, "No data yet — call POST /api/refresh first")

    if verdict:
        data = [p for p in data if p.get("verdict") == verdict.upper()]
    if hg:
        data = [p for p in data if p.get("hg") == hg]
    if tier:
        data = [p for p in data if p.get("tier") == tier]

    return data


@app.get("/api/players/{name}")
def get_player(name: str):
    data = _latest_json()
    if data is None:
        raise HTTPException(503, "No data yet")

    name_lower = name.lower()
    matches = [p for p in data if p.get("name", "").lower() == name_lower]
    if not matches:
        raise HTTPException(404, f"Player '{name}' not found")
    return matches[0]


@app.get("/api/stats/summary")
def get_summary():
    data = _latest_json()
    if data is None:
        raise HTTPException(503, "No data yet")

    from collections import Counter
    verdicts = Counter(p.get("verdict") for p in data)

    sorted_data = sorted(data, key=lambda p: p.get("vpd", 0) or 0, reverse=True)

    return {
        "total_players":   len(data),
        "underpaid_count": verdicts.get("UNDERPAID", 0),
        "fair_count":      verdicts.get("FAIR", 0),
        "overpaid_count":  verdicts.get("OVERPAID", 0),
        "top_values":      sorted_data[:10],
        "worst_values":    sorted_data[-10:],
    }


@app.post("/api/refresh")
def refresh(background_tasks: BackgroundTasks, season: int = 2024):
    """Trigger a fresh data pull + analysis in the background."""
    background_tasks.add_task(_refresh_data, season)
    return {"status": "refresh started", "season": season}


# ── serve frontend ────────────────────────────────────────────────────────────

frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
