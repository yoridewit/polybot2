"""
Edge detection and Kelly criterion position sizing.
"""
from __future__ import annotations
import logging
from typing import Optional

import config
from models import (
    MarketSnapshot, ProbabilityEstimate, EdgeCalculation,
    BetSide, LearningParams,
)

logger = logging.getLogger(__name__)


def _kelly_fraction(win_prob: float, win_payout_ratio: float) -> float:
    """
    Kelly criterion: f* = (b*p - q) / b
    where b = payout per unit staked if win, p = win probability, q = 1-p.
    """
    b = win_payout_ratio
    p = win_prob
    q = 1.0 - p
    f = (b * p - q) / b
    return max(0.0, f)


def calculate_edge(
    market: MarketSnapshot,
    estimate: ProbabilityEstimate,
    portfolio_cash: float,
    learning_params: LearningParams,
) -> EdgeCalculation:
    """
    Compare model probability to market price, compute Kelly bet size.
    """
    model_prob = estimate.estimated_probability
    market_prob = market.yes_price  # market's implied YES probability
    edge = model_prob - market_prob  # positive → YES underpriced, negative → NO underpriced

    if edge > 0:
        bet_side = BetSide.YES
        entry_price = market.yes_price
        win_prob = model_prob
    else:
        bet_side = BetSide.NO
        entry_price = market.no_price      # 1 - yes_price
        win_prob = 1.0 - model_prob        # probability NO resolves

    # payout_ratio b = 1/p - 1
    payout_ratio = (1.0 / entry_price) - 1.0 if entry_price > 0 else 0.0

    raw_kelly = _kelly_fraction(win_prob, payout_ratio) if payout_ratio > 0 else 0.0

    recommended_fraction = raw_kelly * learning_params.kelly_fraction_multiplier
    recommended_fraction = min(recommended_fraction, learning_params.max_bet_fraction)

    recommended_bet_size = portfolio_cash * recommended_fraction

    abs_edge = abs(edge)
    meets = (
        abs_edge >= learning_params.min_edge_threshold
        and estimate.confidence >= learning_params.min_confidence
        and recommended_bet_size >= 1.0
    )

    ec = EdgeCalculation(
        condition_id=market.condition_id,
        model_probability=model_prob,
        market_probability=market_prob,
        edge=edge,
        bet_side=bet_side if meets else None,
        kelly_fraction=raw_kelly,
        recommended_fraction=recommended_fraction,
        recommended_bet_size=round(recommended_bet_size, 2),
        meets_threshold=meets,
    )

    if meets:
        logger.info(
            f"EDGE FOUND [{market.condition_id[:8]}] "
            f"side={bet_side.value} edge={edge:+.3f} "
            f"kelly={raw_kelly:.3f} → bet=${recommended_bet_size:.2f}"
        )
    else:
        logger.debug(
            f"No edge [{market.condition_id[:8]}] "
            f"edge={edge:+.3f} (need ≥{learning_params.min_edge_threshold:.3f})"
        )

    return ec
