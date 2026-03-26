#!/usr/bin/env python3
"""
FastAPI dashboard server — read-only view into the trading bot's SQLite DB.

Run: python dashboard/server.py
Access: http://localhost:8000
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent dir to path so we can import bot modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config
import database as db

app = FastAPI(title="Polybot2 Dashboard", version="1.0")

STATIC_DIR = Path(__file__).parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ─── Portfolio ─────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
def get_portfolio():
    import paper_trader
    p = paper_trader.get_portfolio_state()
    return {
        "cash_balance": p.cash_balance,
        "open_bets_value": p.open_bets_value,
        "total_value": p.total_value,
        "realized_pnl": p.realized_pnl,
        "num_open_bets": p.num_open_bets,
        "num_resolved_bets": p.num_resolved_bets,
        "num_wins": p.num_wins,
        "num_losses": p.num_losses,
        "win_rate": p.win_rate,
        "roi_pct": p.roi_pct,
        "initial_balance": config.INITIAL_PAPER_BALANCE,
    }


@app.get("/api/portfolio/history")
def get_portfolio_history():
    rows = db.get_portfolio_history(limit=1000)
    return [dict(r) for r in rows]


# ─── Bets ─────────────────────────────────────────────────────────────────────

@app.get("/api/bets")
def get_bets(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    rows = db.get_all_bets(limit=limit, offset=offset, status=status, category=category)
    return [dict(r) for r in rows]


@app.get("/api/bets/{bet_id}")
def get_bet_detail(bet_id: str):
    with db.get_conn() as conn:
        bet = conn.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
        if not bet:
            return {"error": "not found"}

        research = db.get_latest_research(bet["condition_id"])
        estimate = db.get_latest_estimate(bet["condition_id"])

        return {
            "bet": dict(bet),
            "research": {
                "summary": research["summary"] if research else None,
                "key_facts": json.loads(research["key_facts"] or "[]") if research else [],
                "search_queries": json.loads(research["search_queries"] or "[]") if research else [],
                "tier": research["tier"] if research else None,
                "tokens_used": research["tokens_used"] if research else 0,
            },
            "estimate": {
                "estimated_probability": estimate["estimated_probability"] if estimate else None,
                "confidence": estimate["confidence"] if estimate else None,
                "reasoning": estimate["reasoning"] if estimate else None,
                "key_factors": json.loads(estimate["key_factors"] or "[]") if estimate else [],
                "model_version": estimate["model_version"] if estimate else None,
            },
        }


# ─── Markets ──────────────────────────────────────────────────────────────────

@app.get("/api/markets")
def get_markets(limit: int = Query(50, le=200)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM markets ORDER BY last_seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/markets/{condition_id}/research")
def get_market_research(condition_id: str):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research WHERE condition_id=? ORDER BY researched_at DESC LIMIT 10",
            (condition_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Learning ─────────────────────────────────────────────────────────────────

@app.get("/api/learning")
def get_learning():
    params = db.load_learning_params()
    history = db.get_learning_history(limit=100)
    return {
        "params": {
            "min_edge_threshold": params.min_edge_threshold,
            "kelly_fraction_multiplier": params.kelly_fraction_multiplier,
            "max_bet_fraction": params.max_bet_fraction,
            "tier2_edge_threshold": params.tier2_edge_threshold,
            "min_confidence": params.min_confidence,
            "category_confidence_adjustments": params.category_confidence_adjustments,
            "calibration_bias": params.calibration_bias,
            "total_resolved": params.total_resolved,
            "version": params.version,
            "last_updated": params.last_updated.isoformat(),
        },
        "history": [dict(h) for h in history],
    }


@app.get("/api/calibration")
def get_calibration():
    """Probability bucket analysis: predicted vs actual win rate."""
    resolved = db.get_resolved_bets()
    buckets: dict = {}

    for bet in resolved:
        if not bet["resolution_outcome"]:
            continue
        model_prob = bet["model_probability"]
        side = bet["side"]
        outcome = bet["resolution_outcome"]

        if side == "YES":
            p = model_prob
        else:
            p = 1.0 - model_prob

        bucket_key = f"{int(p * 10) * 10}-{int(p * 10) * 10 + 10}%"
        if bucket_key not in buckets:
            buckets[bucket_key] = {"total": 0, "wins": 0, "mid": int(p * 10) * 10 + 5}

        won = (outcome == side)
        buckets[bucket_key]["total"] += 1
        if won:
            buckets[bucket_key]["wins"] += 1

    result = []
    for k, v in sorted(buckets.items(), key=lambda x: x[1]["mid"]):
        n = v["total"]
        result.append({
            "bucket": k,
            "mid": v["mid"],
            "total": n,
            "wins": v["wins"],
            "actual_win_rate": v["wins"] / n if n > 0 else 0.0,
            "predicted_win_rate": v["mid"] / 100.0,
        })

    return result


@app.get("/api/category-performance")
def get_category_performance():
    resolved = db.get_resolved_bets()
    cats: dict = {}

    for bet in resolved:
        cat = bet["category"] or "other"
        if cat not in cats:
            cats[cat] = {"total": 0, "wins": 0, "total_pnl": 0.0}
        cats[cat]["total"] += 1
        if bet["status"] == "resolved_win":
            cats[cat]["wins"] += 1
        cats[cat]["total_pnl"] += bet["pnl"] or 0.0

    return [
        {
            "category": cat,
            "total": v["total"],
            "wins": v["wins"],
            "win_rate": v["wins"] / v["total"] if v["total"] > 0 else 0.0,
            "total_pnl": round(v["total_pnl"], 2),
        }
        for cat, v in sorted(cats.items())
    ]


# ─── Token Usage ──────────────────────────────────────────────────────────────

@app.get("/api/token-usage")
def get_token_usage():
    rows = db.get_token_usage_log(days=30)
    today_used = db.get_daily_token_usage()
    return {
        "today_used": today_used,
        "daily_budget": config.DAILY_TOKEN_BUDGET,
        "budget_pct": round(today_used / config.DAILY_TOKEN_BUDGET * 100, 1),
        "history": [dict(r) for r in rows],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.DASHBOARD_PORT, log_level="info")
