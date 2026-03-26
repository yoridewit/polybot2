"""SQLite persistence layer for the trading bot."""
from __future__ import annotations
import sqlite3
import json
import os
from contextlib import contextmanager
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from models import (
    Bet, BetStatus, BetSide, LearningParams, MarketCategory,
    ResearchTier, ResearchResult, ProbabilityEstimate, PortfolioState,
)

DB_PATH = os.getenv("DB_PATH", "polybot2.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id        TEXT PRIMARY KEY,
    question            TEXT NOT NULL,
    description         TEXT,
    category            TEXT,
    end_date            TEXT,
    is_active           INTEGER DEFAULT 1,
    is_resolved         INTEGER DEFAULT 0,
    resolution_outcome  TEXT,
    yes_price_latest    REAL,
    volume_24h_latest   REAL,
    total_volume        REAL,
    liquidity           REAL,
    research_tier       TEXT DEFAULT 'none',
    researched_at       TEXT,
    skip_until          TEXT,
    first_seen_at       TEXT DEFAULT (datetime('now')),
    last_seen_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL,
    tier            TEXT NOT NULL,
    search_queries  TEXT,
    raw_snippets    TEXT,
    summary         TEXT,
    key_facts       TEXT,
    tokens_used     INTEGER DEFAULT 0,
    model_used      TEXT,
    researched_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (condition_id) REFERENCES markets(condition_id)
);

CREATE TABLE IF NOT EXISTS probability_estimates (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id            TEXT NOT NULL,
    estimated_probability   REAL NOT NULL,
    confidence              REAL NOT NULL,
    reasoning               TEXT,
    key_factors             TEXT,
    model_version           TEXT,
    tokens_used             INTEGER DEFAULT 0,
    estimated_at            TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (condition_id) REFERENCES markets(condition_id)
);

CREATE TABLE IF NOT EXISTS bets (
    bet_id              TEXT PRIMARY KEY,
    condition_id        TEXT NOT NULL,
    question            TEXT,
    side                TEXT NOT NULL,
    amount              REAL NOT NULL,
    price_at_bet        REAL NOT NULL,
    model_probability   REAL NOT NULL,
    edge                REAL NOT NULL,
    kelly_fraction      REAL,
    research_tier       TEXT,
    category            TEXT,
    is_paper            INTEGER DEFAULT 1,
    status              TEXT DEFAULT 'open',
    placed_at           TEXT DEFAULT (datetime('now')),
    resolved_at         TEXT,
    pnl                 REAL,
    resolution_outcome  TEXT
);

CREATE TABLE IF NOT EXISTS learning_params (
    id                              INTEGER PRIMARY KEY CHECK (id = 1),
    min_edge_threshold              REAL DEFAULT 0.08,
    kelly_fraction_multiplier       REAL DEFAULT 0.25,
    max_bet_fraction                REAL DEFAULT 0.05,
    tier2_edge_threshold            REAL DEFAULT 0.06,
    min_confidence                  REAL DEFAULT 0.50,
    category_confidence_adjustments TEXT DEFAULT '{}',
    calibration_bias                REAL DEFAULT 0.0,
    total_resolved                  INTEGER DEFAULT 0,
    version                         INTEGER DEFAULT 1,
    last_updated                    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS learning_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at      TEXT DEFAULT (datetime('now')),
    param_name      TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT
);

CREATE TABLE IF NOT EXISTS token_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usage_date  TEXT NOT NULL,
    tier        TEXT NOT NULL,
    model       TEXT NOT NULL,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    logged_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cash_balance    REAL,
    total_value     REAL,
    realized_pnl    REAL,
    num_open_bets   INTEGER,
    win_rate        REAL,
    roi_pct         REAL,
    snapshot_at     TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Ensure single learning_params row exists
        conn.execute(
            "INSERT OR IGNORE INTO learning_params (id) VALUES (1)"
        )


# ─── Markets ──────────────────────────────────────────────────────────────────

def upsert_market(m: Dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO markets
                (condition_id, question, description, category, end_date,
                 is_active, is_resolved, resolution_outcome,
                 yes_price_latest, volume_24h_latest, total_volume, liquidity,
                 last_seen_at)
            VALUES
                (:condition_id, :question, :description, :category, :end_date,
                 :is_active, :is_resolved, :resolution_outcome,
                 :yes_price_latest, :volume_24h_latest, :total_volume, :liquidity,
                 datetime('now'))
            ON CONFLICT(condition_id) DO UPDATE SET
                question           = excluded.question,
                is_active          = excluded.is_active,
                is_resolved        = excluded.is_resolved,
                resolution_outcome = excluded.resolution_outcome,
                yes_price_latest   = excluded.yes_price_latest,
                volume_24h_latest  = excluded.volume_24h_latest,
                total_volume       = excluded.total_volume,
                liquidity          = excluded.liquidity,
                last_seen_at       = datetime('now')
        """, m)


def get_market(condition_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM markets WHERE condition_id = ?", (condition_id,)
        ).fetchone()


def mark_market_researched(condition_id: str, tier: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE markets SET research_tier=?, researched_at=datetime('now') WHERE condition_id=?",
            (tier, condition_id)
        )


def snooze_market(condition_id: str, until_iso: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE markets SET skip_until=? WHERE condition_id=?",
            (until_iso, condition_id)
        )


def mark_market_resolved(condition_id: str, outcome: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE markets SET is_resolved=1, resolution_outcome=? WHERE condition_id=?",
            (outcome, condition_id)
        )


# ─── Research ─────────────────────────────────────────────────────────────────

def save_research(r: ResearchResult) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO research
                (condition_id, tier, search_queries, raw_snippets, summary,
                 key_facts, tokens_used, model_used, researched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.condition_id, r.tier.value,
            json.dumps(r.search_queries), json.dumps(r.raw_snippets),
            r.summary, json.dumps(r.key_facts),
            r.tokens_used, r.model_used,
            r.researched_at.isoformat()
        ))
    mark_market_researched(r.condition_id, r.tier.value)


def get_latest_research(condition_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM research WHERE condition_id=? ORDER BY researched_at DESC LIMIT 1",
            (condition_id,)
        ).fetchone()


# ─── Probability Estimates ────────────────────────────────────────────────────

def save_estimate(e: ProbabilityEstimate) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO probability_estimates
                (condition_id, estimated_probability, confidence, reasoning,
                 key_factors, model_version, tokens_used, estimated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            e.condition_id, e.estimated_probability, e.confidence,
            e.reasoning, json.dumps(e.key_factors),
            e.model_version, e.tokens_used, e.estimated_at.isoformat()
        ))


def get_latest_estimate(condition_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM probability_estimates WHERE condition_id=? ORDER BY estimated_at DESC LIMIT 1",
            (condition_id,)
        ).fetchone()


# ─── Bets ─────────────────────────────────────────────────────────────────────

def save_bet(b: Bet) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO bets
                (bet_id, condition_id, question, side, amount, price_at_bet,
                 model_probability, edge, kelly_fraction, research_tier,
                 category, is_paper, status, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            b.bet_id, b.condition_id, b.question, b.side.value,
            b.amount, b.price_at_bet, b.model_probability, b.edge,
            b.kelly_fraction, b.research_tier.value, b.category.value,
            1 if b.is_paper else 0, b.status.value,
            b.placed_at.isoformat()
        ))


def resolve_bet(bet_id: str, outcome: str, pnl: float) -> None:
    """outcome: 'YES' or 'NO'"""
    with get_conn() as conn:
        row = conn.execute("SELECT side FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
        if not row:
            return
        side = row["side"]
        if outcome == side:
            status = BetStatus.RESOLVED_WIN.value
        else:
            status = BetStatus.RESOLVED_LOSS.value
        conn.execute("""
            UPDATE bets SET status=?, resolved_at=datetime('now'), pnl=?, resolution_outcome=?
            WHERE bet_id=?
        """, (status, pnl, outcome, bet_id))


def get_open_bets() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bets WHERE status='open'"
        ).fetchall()


def get_all_bets(limit: int = 200, offset: int = 0, status: Optional[str] = None,
                 category: Optional[str] = None) -> List[sqlite3.Row]:
    with get_conn() as conn:
        where = []
        params: List[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if category:
            where.append("category=?")
            params.append(category)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params += [limit, offset]
        return conn.execute(
            f"SELECT * FROM bets {clause} ORDER BY placed_at DESC LIMIT ? OFFSET ?",
            params
        ).fetchall()


def get_resolved_bets() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bets WHERE status IN ('resolved_win','resolved_loss','resolved_push')"
        ).fetchall()


def has_open_bet_for(condition_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM bets WHERE condition_id=? AND status='open'", (condition_id,)
        ).fetchone()
        return row is not None


# ─── Portfolio ────────────────────────────────────────────────────────────────

def save_portfolio_snapshot(p: PortfolioState) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO portfolio_history
                (cash_balance, total_value, realized_pnl, num_open_bets, win_rate, roi_pct, snapshot_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            p.cash_balance, p.total_value, p.realized_pnl,
            p.num_open_bets, p.win_rate, p.roi_pct,
            p.snapshot_at.isoformat()
        ))


def get_portfolio_history(limit: int = 500) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM portfolio_history ORDER BY snapshot_at ASC LIMIT ?", (limit,)
        ).fetchall()


# ─── Token Usage ──────────────────────────────────────────────────────────────

def log_token_usage(tier: str, model: str, input_tokens: int, output_tokens: int) -> None:
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO token_usage (usage_date, tier, model, input_tokens, output_tokens, total_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (today, tier, model, input_tokens, output_tokens, input_tokens + output_tokens))


def get_daily_token_usage(usage_date: Optional[str] = None) -> int:
    today = usage_date or date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) as total FROM token_usage WHERE usage_date=?",
            (today,)
        ).fetchone()
        return row["total"] if row else 0


def get_token_usage_log(days: int = 30) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT usage_date, tier, model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(total_tokens) as total_tokens
            FROM token_usage
            WHERE usage_date >= date('now', ?)
            GROUP BY usage_date, tier, model
            ORDER BY usage_date DESC
        """, (f"-{days} days",)).fetchall()


# ─── Learning Params ──────────────────────────────────────────────────────────

def load_learning_params() -> LearningParams:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM learning_params WHERE id=1").fetchone()
        if not row:
            return LearningParams()
        adj = json.loads(row["category_confidence_adjustments"] or "{}")
        return LearningParams(
            min_edge_threshold=row["min_edge_threshold"],
            kelly_fraction_multiplier=row["kelly_fraction_multiplier"],
            max_bet_fraction=row["max_bet_fraction"],
            tier2_edge_threshold=row["tier2_edge_threshold"],
            min_confidence=row["min_confidence"],
            category_confidence_adjustments=adj,
            calibration_bias=row["calibration_bias"],
            total_resolved=row["total_resolved"],
            version=row["version"],
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )


def save_learning_params(p: LearningParams) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE learning_params SET
                min_edge_threshold=?, kelly_fraction_multiplier=?, max_bet_fraction=?,
                tier2_edge_threshold=?, min_confidence=?,
                category_confidence_adjustments=?, calibration_bias=?,
                total_resolved=?, version=?, last_updated=datetime('now')
            WHERE id=1
        """, (
            p.min_edge_threshold, p.kelly_fraction_multiplier, p.max_bet_fraction,
            p.tier2_edge_threshold, p.min_confidence,
            json.dumps(p.category_confidence_adjustments), p.calibration_bias,
            p.total_resolved, p.version
        ))


def log_learning_change(param_name: str, old_value: Any, new_value: Any, reason: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO learning_history (param_name, old_value, new_value, reason)
            VALUES (?, ?, ?, ?)
        """, (param_name, str(old_value), str(new_value), reason))


def get_learning_history(limit: int = 100) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM learning_history ORDER BY changed_at DESC LIMIT ?", (limit,)
        ).fetchall()
