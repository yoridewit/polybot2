#!/usr/bin/env python3
"""
Polybot2 — Autonomous Polymarket Paper Trading Bot.

Main loop: scan → research → estimate → size → bet → sleep
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

import anthropic

import config
import database as db
import market_scanner
import research
import analyst
import edge_calculator
import paper_trader
import learning as learning_module
import bootstrap

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("polybot2")


def run_cycle(anthropic_client) -> None:
    """Execute one full scan-research-bet cycle."""
    logger.info("=" * 60)
    logger.info(f"CYCLE START | {_utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    learning_params = db.load_learning_params()
    logger.info(
        f"Strategy | edge≥{learning_params.min_edge_threshold:.3f} "
        f"kelly×{learning_params.kelly_fraction_multiplier:.2f} "
        f"conf≥{learning_params.min_confidence:.2f}"
    )

    used_today = db.get_daily_token_usage()
    logger.info(f"Token budget: {used_today:,} / {config.DAILY_TOKEN_BUDGET:,} used today")

    # 1. Scan markets
    candidates = market_scanner.scan_markets()
    if not candidates:
        logger.info("No candidates found this cycle")
        paper_trader.print_portfolio_summary()
        return

    # 2. Research pipeline
    research_results = research.run_research_pipeline(candidates, anthropic_client, learning_params)

    # Build lookup by condition_id
    research_map = {r.condition_id: r for r in research_results}

    # 3. Estimate and bet
    bets_placed = 0
    for market in candidates:
        if db.has_open_bet_for(market.condition_id):
            continue

        research_result = research_map.get(market.condition_id)

        estimate = analyst.estimate_probability(
            market, research_result, anthropic_client, learning_params
        )
        if estimate is None:
            continue

        cash = paper_trader.get_cash()
        edge = edge_calculator.calculate_edge(market, estimate, cash, learning_params)

        if not edge.meets_threshold:
            continue

        from models import ResearchTier
        tier = research_result.tier if research_result else ResearchTier.NONE
        bet = paper_trader.place_bet(market, edge, tier)
        if bet:
            bets_placed += 1

    logger.info(f"Bets placed this cycle: {bets_placed}")

    # 4. Check and resolve open bets
    resolutions = paper_trader.check_and_resolve_bets()
    if resolutions:
        logger.info(f"Resolved {len(resolutions)} bets this cycle")

    # 5. Learning update
    learning_module.maybe_update_params()

    # 6. Portfolio summary
    paper_trader.print_portfolio_summary()


def main() -> None:
    """Main entry point."""
    db.init_db()

    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # ── Bootstrap: one-time historical calibration ────────────────────────
    try:
        if bootstrap.run_bootstrap(anthropic_client):
            logger.info("Bootstrap calibration applied — starting with pre-calibrated params")
    except Exception as e:
        logger.warning(f"Bootstrap failed (non-fatal, using defaults): {e}")

    logger.info("=" * 60)
    logger.info("Polybot2 — Autonomous Polymarket Paper Trading Bot")
    logger.info(f"Starting balance: ${config.INITIAL_PAPER_BALANCE:.2f}")
    logger.info(f"Scan interval: {config.SCAN_INTERVAL_MINUTES} minutes")
    logger.info("=" * 60)

    while True:
        try:
            run_cycle(anthropic_client)
        except KeyboardInterrupt:
            logger.info("Interrupted by user, shutting down...")
            break
        except Exception as e:
            logger.error(f"Cycle failed with error: {e}", exc_info=True)

        sleep_seconds = config.SCAN_INTERVAL_MINUTES * 60
        logger.info(f"Sleeping {config.SCAN_INTERVAL_MINUTES} minutes until next cycle...")
        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            logger.info("Interrupted by user, shutting down...")
            break


if __name__ == "__main__":
    main()
