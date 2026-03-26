"""
Learning module — analyses resolved bets and adapts strategy parameters.

Runs after each batch of resolutions (when enough new data exists).
Updates LearningParams in DB and logs every change.
"""
from __future__ import annotations
import logging
from collections import defaultdict
from typing import List, Dict

import config
import database as db
from models import LearningParams

logger = logging.getLogger(__name__)

EDGE_STEP = 0.005
KELLY_STEP = 0.02
MIN_KELLY = 0.05
MAX_KELLY = 0.50
MIN_EDGE = 0.04
MAX_EDGE = 0.20


def _update_param(params: LearningParams, name: str, old_val: float, new_val: float, reason: str) -> None:
    """Apply and log a param change."""
    if abs(new_val - old_val) < 1e-6:
        return
    setattr(params, name, round(new_val, 4))
    db.log_learning_change(name, round(old_val, 4), round(new_val, 4), reason)
    logger.info(f"LEARNING | {name}: {old_val:.4f} → {new_val:.4f} | {reason}")


def _calibration_bias(resolved_bets) -> float:
    """
    Estimate systematic over/under confidence.
    Returns positive if model is overconfident (estimated too high).
    """
    if len(resolved_bets) < 5:
        return 0.0

    total_predicted = 0.0
    total_actual = 0.0
    n = 0

    for bet in resolved_bets:
        model_prob = bet["model_probability"]
        side = bet["side"]
        outcome = bet["resolution_outcome"]
        if not outcome:
            continue

        if side == "YES":
            predicted_yes = model_prob
        else:
            predicted_yes = 1.0 - model_prob

        actual_yes = 1.0 if outcome == "YES" else 0.0
        total_predicted += predicted_yes
        total_actual += actual_yes
        n += 1

    if n == 0:
        return 0.0

    return (total_predicted - total_actual) / n


def _edge_bucket_performance(resolved_bets) -> Dict[str, dict]:
    """
    Group bets by edge bucket and compute win rate + EV per bucket.
    """
    buckets: Dict[str, list] = defaultdict(list)

    for bet in resolved_bets:
        edge = abs(bet["edge"])
        outcome = bet["resolution_outcome"]
        side = bet["side"]
        pnl = bet["pnl"] or 0.0

        if edge < 0.05:
            key = "0-5%"
        elif edge < 0.10:
            key = "5-10%"
        elif edge < 0.15:
            key = "10-15%"
        elif edge < 0.20:
            key = "15-20%"
        else:
            key = "20%+"

        won = (outcome == side) if outcome else False
        buckets[key].append({"won": won, "pnl": pnl})

    result = {}
    for k, bets in buckets.items():
        n = len(bets)
        wins = sum(1 for b in bets if b["won"])
        total_pnl = sum(b["pnl"] for b in bets)
        result[k] = {
            "n": n,
            "win_rate": wins / n if n > 0 else 0.0,
            "total_pnl": total_pnl,
            "ev_per_bet": total_pnl / n if n > 0 else 0.0,
        }
    return result


def _category_performance(resolved_bets) -> Dict[str, dict]:
    cats: Dict[str, list] = defaultdict(list)
    for bet in resolved_bets:
        cat = bet["category"] or "other"
        won = (bet["resolution_outcome"] == bet["side"]) if bet["resolution_outcome"] else False
        cats[cat].append({"won": won, "pnl": bet["pnl"] or 0.0})
    result = {}
    for cat, bets in cats.items():
        n = len(bets)
        wins = sum(1 for b in bets if b["won"])
        result[cat] = {
            "n": n,
            "win_rate": wins / n if n > 0 else 0.0,
            "total_pnl": sum(b["pnl"] for b in bets),
        }
    return result


def maybe_update_params() -> bool:
    """
    Check if enough new resolutions have occurred to warrant a learning update.
    Returns True if an update was performed.
    """
    params = db.load_learning_params()
    resolved = db.get_resolved_bets()
    n_resolved = len(resolved)

    new_since_last = n_resolved - params.total_resolved
    if new_since_last < config.LEARNING_MIN_RESOLVED:
        logger.debug(
            f"Learning: {new_since_last} new resolutions, "
            f"need {config.LEARNING_MIN_RESOLVED} to trigger update"
        )
        return False

    logger.info(f"LEARNING TRIGGERED: {new_since_last} new resolutions (total={n_resolved})")
    run_learning_update(params, resolved)
    return True


def run_learning_update(params: LearningParams, resolved_bets=None) -> LearningParams:
    """
    Analyse all resolved bets and update strategy parameters.
    """
    if resolved_bets is None:
        resolved_bets = db.get_resolved_bets()

    n = len(resolved_bets)
    if n < 3:
        logger.info("Not enough resolved bets for learning yet")
        return params

    wins = [b for b in resolved_bets if b["status"] == "resolved_win"]
    losses = [b for b in resolved_bets if b["status"] == "resolved_loss"]
    win_rate = len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0.0

    logger.info(f"LEARNING | {n} resolved | win_rate={win_rate:.1%} | wins={len(wins)} losses={len(losses)}")

    # ── 1. Calibration bias ───────────────────────────────────────────────────
    bias = _calibration_bias(resolved_bets)
    _update_param(params, "calibration_bias", params.calibration_bias, bias,
                  f"recalibrated from {n} bets")

    # ── 2. Kelly fraction ─────────────────────────────────────────────────────
    old_kelly = params.kelly_fraction_multiplier
    if win_rate < 0.40 and len(wins) + len(losses) >= 10:
        new_kelly = max(MIN_KELLY, old_kelly - KELLY_STEP)
        _update_param(params, "kelly_fraction_multiplier", old_kelly, new_kelly,
                      f"win_rate={win_rate:.1%} < 40%, reducing Kelly")
    elif win_rate > 0.60 and len(wins) + len(losses) >= 10:
        new_kelly = min(MAX_KELLY, old_kelly + KELLY_STEP)
        _update_param(params, "kelly_fraction_multiplier", old_kelly, new_kelly,
                      f"win_rate={win_rate:.1%} > 60%, increasing Kelly")

    # ── 3. Edge threshold ─────────────────────────────────────────────────────
    bucket_perf = _edge_bucket_performance(resolved_bets)
    low_edge_bucket = bucket_perf.get("5-10%", {})
    if low_edge_bucket.get("n", 0) >= 5 and low_edge_bucket.get("ev_per_bet", 0) < 0:
        new_edge = min(MAX_EDGE, params.min_edge_threshold + EDGE_STEP)
        _update_param(params, "min_edge_threshold", params.min_edge_threshold, new_edge,
                      "5-10% edge bucket has negative EV → raising threshold")
    elif all(
        b.get("ev_per_bet", 0) > 0
        for k, b in bucket_perf.items()
        if b.get("n", 0) >= 5
    ) and len(resolved_bets) >= 20:
        new_edge = max(MIN_EDGE, params.min_edge_threshold - EDGE_STEP)
        _update_param(params, "min_edge_threshold", params.min_edge_threshold, new_edge,
                      "all edge buckets positive EV → cautiously lowering threshold")

    # ── 4. Per-category confidence adjustments ────────────────────────────────
    cat_perf = _category_performance(resolved_bets)
    adj = dict(params.category_confidence_adjustments)
    for cat, perf in cat_perf.items():
        if perf["n"] < 5:
            continue
        cat_wr = perf["win_rate"]
        if cat_wr < 0.40:
            old = adj.get(cat, 0.0)
            adj[cat] = round(old + 0.02, 3)
            db.log_learning_change(
                f"category_adj[{cat}]", old, adj[cat],
                f"win_rate={cat_wr:.1%} < 40% in {cat}"
            )
        elif cat_wr > 0.65:
            old = adj.get(cat, 0.0)
            adj[cat] = round(max(0.0, old - 0.01), 3)
            db.log_learning_change(
                f"category_adj[{cat}]", old, adj[cat],
                f"win_rate={cat_wr:.1%} > 65% in {cat}"
            )

    params.category_confidence_adjustments = adj
    params.total_resolved = len(resolved_bets)
    params.version += 1

    db.save_learning_params(params)
    logger.info(f"LEARNING COMPLETE | params v{params.version} saved")

    logger.info("Edge bucket performance:")
    for k, v in sorted(bucket_perf.items()):
        if v["n"] > 0:
            logger.info(f"  {k:8s}: n={v['n']:3d} wr={v['win_rate']:.1%} ev/bet=${v['ev_per_bet']:+.2f}")

    return params
