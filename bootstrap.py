"""One-time historical calibration using resolved Polymarket markets."""
from __future__ import annotations
import json
import logging
from typing import Optional

import requests

import config
import database as db
from market_scanner import parse_category

logger = logging.getLogger(__name__)

GAMMA_MARKETS_URL = f"{config.GAMMA_API_BASE}/markets"


def should_bootstrap() -> bool:
    """Check if bootstrap should run (first startup with no resolved data)."""
    if not config.BOOTSTRAP_ENABLED:
        return False
    params = db.load_learning_params()
    return params.total_resolved == 0


def fetch_resolved_markets(limit: int = 200) -> list[dict]:
    """Fetch resolved markets from Gamma API (paginated)."""
    all_markets: list[dict] = []
    page_size = 100
    pages_needed = (limit + page_size - 1) // page_size

    for page_num in range(pages_needed):
        try:
            params = {
                "closed": "true",
                "resolved": "true",
                "limit": page_size,
                "offset": page_num * page_size,
            }
            resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
            resp.raise_for_status()
            page = resp.json()
            all_markets.extend(page)
            if len(page) < page_size:
                break
        except Exception as e:
            logger.warning(f"Bootstrap: failed to fetch page {page_num}: {e}")
            break

    return all_markets


def select_clear_outcomes(raw_markets: list[dict], max_count: int = 75) -> list[dict]:
    """Filter to markets with clear YES/NO outcomes and non-trivial questions."""
    selected: list[dict] = []

    for raw in raw_markets:
        question = raw.get("question", raw.get("title", ""))
        if not question or len(question) < 15:
            continue

        outcome_prices = raw.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                continue
        if len(outcome_prices) < 2:
            continue

        try:
            yes_price = float(outcome_prices[0])
        except (ValueError, TypeError):
            continue

        if yes_price >= 0.99:
            outcome = "YES"
        elif yes_price <= 0.01:
            outcome = "NO"
        else:
            continue  # Not clearly resolved

        tags = raw.get("tags", [])
        category = parse_category(tags)

        selected.append({
            "question": question,
            "outcome": outcome,
            "category": category.value,
        })

        if len(selected) >= max_count:
            break

    return selected


def build_batch_prompt(markets: list[dict]) -> str:
    """Build a single prompt for Haiku to estimate probabilities for all markets."""
    lines = []
    for i, m in enumerate(markets, 1):
        lines.append(f'{i}. "{m["question"]}"')

    market_list = "\n".join(lines)
    return f"""For each market below, estimate the probability that it would resolve YES.
Be well-calibrated: a 70% estimate should be correct ~70% of the time.
Form your own independent view based on your knowledge.

Return ONLY a JSON array of objects, one per market, in the same order.
Each object: {{"index": N, "probability": 0.XX}}

Markets:
{market_list}

Return your estimates as a JSON array. No other text."""


def call_haiku_batch(anthropic_client, prompt: str, n_markets: int) -> list[dict]:
    """Send batch prompt to Haiku and parse the JSON array response."""
    response = anthropic_client.messages.create(
        model=config.HAIKU_MODEL,
        max_tokens=4096,
        system="You are an expert prediction market probability estimator. Return only valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    usage = response.usage
    db.log_token_usage(
        tier="bootstrap",
        model=config.HAIKU_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    logger.info(
        f"Bootstrap Haiku call: {usage.input_tokens} in + {usage.output_tokens} out "
        f"= {usage.input_tokens + usage.output_tokens} tokens"
    )

    text = response.content[0].text
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        logger.error("Bootstrap: could not find JSON array in Haiku response")
        return []

    try:
        estimates = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        logger.error(f"Bootstrap: failed to parse JSON: {e}")
        return []

    valid = []
    for entry in estimates:
        if isinstance(entry, dict) and "index" in entry and "probability" in entry:
            try:
                prob = float(entry["probability"])
                prob = max(0.01, min(0.99, prob))
                valid.append({"index": int(entry["index"]), "probability": prob})
            except (ValueError, TypeError):
                continue

    return valid


def compute_bootstrap_params(markets: list[dict], estimates: list[dict]) -> dict:
    """Compare estimates to actual outcomes and compute calibration parameters."""
    estimate_map = {e["index"]: e["probability"] for e in estimates}

    pairs = []
    category_pairs: dict[str, list] = {}

    for i, market in enumerate(markets, 1):
        if i not in estimate_map:
            continue
        predicted_yes = estimate_map[i]
        actual_yes = 1.0 if market["outcome"] == "YES" else 0.0
        cat = market["category"]

        pairs.append((predicted_yes, actual_yes))

        if cat not in category_pairs:
            category_pairs[cat] = []
        category_pairs[cat].append((predicted_yes, actual_yes))

    if not pairs:
        return {
            "calibration_bias": 0.0,
            "category_confidence_adjustments": {},
            "total_resolved": 0,
            "metrics": {"brier_score": 1.0, "accuracy": 0.0, "n_markets": 0, "n_yes": 0, "n_no": 0},
        }

    total_predicted = sum(p for p, _ in pairs)
    total_actual = sum(a for _, a in pairs)
    n = len(pairs)
    calibration_bias = (total_predicted - total_actual) / n

    brier = sum((p - a) ** 2 for p, a in pairs) / n

    correct = sum(1 for p, a in pairs if (p > 0.5) == (a > 0.5))
    accuracy = correct / n

    category_adjustments: dict[str, float] = {}
    for cat, cat_pairs in category_pairs.items():
        if len(cat_pairs) < 5:
            continue
        cat_n = len(cat_pairs)
        cat_bias = (sum(p for p, _ in cat_pairs) - sum(a for _, a in cat_pairs)) / cat_n
        if abs(cat_bias) > 0.03:
            category_adjustments[cat] = round(cat_bias, 4)

    n_yes = sum(1 for _, a in pairs if a > 0.5)
    n_no = n - n_yes

    return {
        "calibration_bias": round(calibration_bias, 4),
        "category_confidence_adjustments": category_adjustments,
        "total_resolved": n,
        "metrics": {
            "brier_score": round(brier, 4),
            "accuracy": round(accuracy, 4),
            "n_markets": n,
            "n_yes": n_yes,
            "n_no": n_no,
        },
    }


def apply_bootstrap_params(params_dict: dict) -> None:
    """Write bootstrap results to the learning_params table and log changes."""
    params = db.load_learning_params()

    old_bias = params.calibration_bias
    new_bias = params_dict["calibration_bias"]
    if old_bias != new_bias:
        params.calibration_bias = new_bias
        db.log_learning_change(
            "calibration_bias", old_bias, new_bias,
            f"bootstrap: computed from {params_dict['total_resolved']} historical resolved markets"
        )

    old_adj = params.category_confidence_adjustments
    new_adj = params_dict["category_confidence_adjustments"]
    if new_adj:
        params.category_confidence_adjustments = new_adj
        for cat, adj_val in new_adj.items():
            old_val = old_adj.get(cat, 0.0)
            db.log_learning_change(
                f"category_adj[{cat}]", old_val, adj_val,
                f"bootstrap: bias={adj_val:+.4f} across historical {cat} markets"
            )

    old_resolved = params.total_resolved
    params.total_resolved = params_dict["total_resolved"]
    params.version += 1

    db.save_learning_params(params)

    db.log_learning_change(
        "total_resolved", old_resolved, params.total_resolved,
        f"bootstrap: initialized from {params.total_resolved} historical markets"
    )


def run_bootstrap(anthropic_client) -> bool:
    """Run one-time historical calibration. Returns True if bootstrap ran."""
    if not should_bootstrap():
        return False

    logger.info("=" * 50)
    logger.info("BOOTSTRAP: Starting historical calibration...")
    logger.info("=" * 50)

    raw_markets = fetch_resolved_markets(limit=config.BOOTSTRAP_MARKET_FETCH_LIMIT)
    logger.info(f"BOOTSTRAP: Fetched {len(raw_markets)} resolved markets from Gamma API")

    if not raw_markets:
        logger.warning("BOOTSTRAP: No resolved markets found, skipping")
        return False

    selected = select_clear_outcomes(raw_markets, max_count=config.BOOTSTRAP_MAX_MARKETS)
    if len(selected) < 10:
        logger.warning(f"BOOTSTRAP: Only {len(selected)} clear-outcome markets (need >= 10), skipping")
        return False
    logger.info(f"BOOTSTRAP: Selected {len(selected)} markets with clear YES/NO outcomes")

    prompt = build_batch_prompt(selected)
    estimates = call_haiku_batch(anthropic_client, prompt, len(selected))

    if len(estimates) < len(selected) * 0.5:
        logger.warning(
            f"BOOTSTRAP: Only got {len(estimates)} estimates for {len(selected)} markets, skipping"
        )
        return False

    logger.info(f"BOOTSTRAP: Got {len(estimates)} probability estimates from Haiku")

    params_dict = compute_bootstrap_params(selected, estimates)

    apply_bootstrap_params(params_dict)

    m = params_dict["metrics"]
    logger.info(
        f"BOOTSTRAP COMPLETE: {m['n_markets']} markets | "
        f"bias={params_dict['calibration_bias']:+.4f} | "
        f"brier={m['brier_score']:.4f} | accuracy={m['accuracy']:.1%}"
    )
    if params_dict["category_confidence_adjustments"]:
        for cat, adj in params_dict["category_confidence_adjustments"].items():
            logger.info(f"  category[{cat}]: bias={adj:+.4f}")

    return True
