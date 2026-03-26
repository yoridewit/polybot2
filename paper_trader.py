"""
Paper (simulated) trading portfolio.

Manages a virtual $1,000 starting balance, places and resolves paper bets,
and exposes portfolio state for reporting and learning.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import List, Optional

import config
import database as db
from models import (
    Bet, BetSide, BetStatus, EdgeCalculation, MarketSnapshot,
    PortfolioState, ResearchTier, MarketCategory,
)

logger = logging.getLogger(__name__)

# In-memory cash balance — persisted implicitly via bets table
_cash: Optional[float] = None


def _load_cash() -> float:
    """Derive current cash from initial balance minus open bet amounts plus resolved PNL."""
    resolved = db.get_resolved_bets()
    open_bets = db.get_open_bets()

    realized_pnl = sum(r["pnl"] or 0.0 for r in resolved)
    open_investment = sum(o["amount"] for o in open_bets)

    return config.INITIAL_PAPER_BALANCE + realized_pnl - open_investment


def get_cash() -> float:
    global _cash
    if _cash is None:
        _cash = _load_cash()
    return _cash


def _update_cash(delta: float) -> None:
    global _cash
    _cash = get_cash() + delta


def place_bet(
    market: MarketSnapshot,
    edge: EdgeCalculation,
    research_tier: ResearchTier,
) -> Optional[Bet]:
    """
    Place a paper bet. Returns the Bet if successful, None if insufficient funds.
    """
    if edge.bet_side is None or not edge.meets_threshold:
        return None

    amount = edge.recommended_bet_size
    cash = get_cash()

    if amount > cash:
        logger.warning(
            f"Insufficient funds for bet on [{market.condition_id[:8]}]: "
            f"need ${amount:.2f}, have ${cash:.2f}"
        )
        amount = cash * config.MAX_BET_FRACTION
        if amount < 1.0:
            logger.warning("Cash too low to place minimum bet, skipping")
            return None

    price = market.yes_price if edge.bet_side == BetSide.YES else market.no_price

    bet = Bet(
        condition_id=market.condition_id,
        question=market.question,
        side=edge.bet_side,
        amount=round(amount, 2),
        price_at_bet=price,
        model_probability=edge.model_probability,
        edge=edge.edge,
        kelly_fraction=edge.kelly_fraction,
        research_tier=research_tier,
        category=market.category,
    )

    db.save_bet(bet)
    _update_cash(-bet.amount)

    logger.info(
        f"PAPER BET PLACED | {bet.bet_id[:8]} | {market.question[:60]} | "
        f"side={bet.side.value} amount=${bet.amount:.2f} price={price:.3f} "
        f"edge={edge.edge:+.3f}"
    )
    return bet


def check_and_resolve_bets() -> List[dict]:
    """
    Fetch resolved markets from Gamma API and settle any open bets.
    Returns list of resolution dicts for the learning module.
    """
    from market_scanner import get_resolved_market_outcomes

    open_bets = db.get_open_bets()
    if not open_bets:
        return []

    resolved_outcomes = get_resolved_market_outcomes()
    resolutions = []

    for bet_row in open_bets:
        condition_id = bet_row["condition_id"]
        outcome = resolved_outcomes.get(condition_id)
        if outcome is None:
            continue

        bet_id = bet_row["bet_id"]
        side = bet_row["side"]
        amount = bet_row["amount"]
        price_at_bet = bet_row["price_at_bet"]

        if outcome == side:
            pnl = round(amount * (1.0 / price_at_bet - 1.0), 4)
            status = BetStatus.RESOLVED_WIN
        else:
            pnl = -amount
            status = BetStatus.RESOLVED_LOSS

        db.resolve_bet(bet_id, outcome, pnl)
        db.mark_market_resolved(condition_id, outcome)
        if status == BetStatus.RESOLVED_WIN:
            _update_cash(pnl + amount)  # Return stake + profit

        resolutions.append({
            "bet_id": bet_id,
            "condition_id": condition_id,
            "side": side,
            "outcome": outcome,
            "pnl": pnl,
            "status": status.value,
            "model_probability": bet_row["model_probability"],
            "price_at_bet": price_at_bet,
            "category": bet_row["category"],
        })

        logger.info(
            f"BET RESOLVED | {bet_id[:8]} | outcome={outcome} "
            f"side={side} pnl=${pnl:+.2f} | {status.value}"
        )

    return resolutions


def get_portfolio_state() -> PortfolioState:
    """Compute current portfolio metrics."""
    cash = get_cash()
    open_bets = db.get_open_bets()
    resolved = db.get_resolved_bets()

    open_investment = sum(o["amount"] for o in open_bets)
    realized_pnl = sum(r["pnl"] or 0.0 for r in resolved)

    wins = [r for r in resolved if r["status"] == BetStatus.RESOLVED_WIN.value]
    losses = [r for r in resolved if r["status"] == BetStatus.RESOLVED_LOSS.value]

    n_resolved = len(wins) + len(losses)
    win_rate = len(wins) / n_resolved if n_resolved > 0 else 0.0

    total_value = cash + open_investment
    roi_pct = (total_value - config.INITIAL_PAPER_BALANCE) / config.INITIAL_PAPER_BALANCE * 100

    return PortfolioState(
        cash_balance=round(cash, 2),
        open_bets_value=round(open_investment, 2),
        total_value=round(total_value, 2),
        realized_pnl=round(realized_pnl, 2),
        num_open_bets=len(open_bets),
        num_resolved_bets=n_resolved,
        num_wins=len(wins),
        num_losses=len(losses),
        win_rate=round(win_rate, 4),
        roi_pct=round(roi_pct, 2),
    )


def print_portfolio_summary() -> None:
    """Log a human-readable portfolio summary."""
    p = get_portfolio_state()
    db.save_portfolio_snapshot(p)
    logger.info("=" * 60)
    logger.info(f"PORTFOLIO SUMMARY @ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"  Cash:        ${p.cash_balance:>10.2f}")
    logger.info(f"  Open bets:   ${p.open_bets_value:>10.2f}  ({p.num_open_bets} bets)")
    logger.info(f"  Total value: ${p.total_value:>10.2f}")
    logger.info(f"  Realized PNL:${p.realized_pnl:>+10.2f}")
    logger.info(f"  ROI:          {p.roi_pct:>+9.2f}%")
    logger.info(f"  Win rate:     {p.win_rate:>9.1%}  ({p.num_wins}W / {p.num_losses}L)")
    logger.info("=" * 60)
