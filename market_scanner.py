"""
Fetches active Polymarket markets from the Gamma API,
applies filters, and returns MarketSnapshot candidates.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict

import requests

import config
import database as db
from models import MarketSnapshot, MarketCategory

logger = logging.getLogger(__name__)

GAMMA_MARKETS_URL = f"{config.GAMMA_API_BASE}/markets"


def parse_category(tags: list) -> MarketCategory:
    tag_names = {t.get("label", "").lower() for t in (tags or [])}
    if any(k in tag_names for k in ("politics", "election", "government", "congress", "president")):
        return MarketCategory.POLITICS
    if any(k in tag_names for k in ("crypto", "bitcoin", "ethereum", "defi", "blockchain")):
        return MarketCategory.CRYPTO
    if any(k in tag_names for k in ("sports", "nba", "nfl", "mlb", "soccer", "tennis", "football")):
        return MarketCategory.SPORTS
    if any(k in tag_names for k in ("science", "technology", "ai", "space", "climate")):
        return MarketCategory.SCIENCE
    if any(k in tag_names for k in ("entertainment", "oscars", "music", "movies", "celebrity")):
        return MarketCategory.ENTERTAINMENT
    if any(k in tag_names for k in ("finance", "stocks", "economy", "fed", "interest rate")):
        return MarketCategory.FINANCE
    if any(k in tag_names for k in ("world", "international", "geopolitics", "war")):
        return MarketCategory.WORLD
    return MarketCategory.OTHER


def _parse_end_date(raw_date: Optional[str]) -> datetime:
    """Parse end date from various Polymarket date formats."""
    if not raw_date:
        return datetime.utcnow() + timedelta(days=30)
    try:
        if "T" in raw_date:
            return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.strptime(raw_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return datetime.utcnow() + timedelta(days=30)


def fetch_active_markets(limit: int = 100) -> list[dict]:
    """Fetch active, unresolved markets from Gamma API."""
    try:
        params = {
            "active": "true",
            "closed": "false",
            "resolved": "false",
            "limit": limit,
        }
        resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return []


def get_resolved_market_outcomes() -> Dict[str, str]:
    """Fetch recently resolved markets and return {condition_id: outcome}."""
    outcomes: Dict[str, str] = {}
    open_bets = db.get_open_bets()
    if not open_bets:
        return {}

    open_ids = {bet["condition_id"] for bet in open_bets}

    try:
        params = {
            "resolved": "true",
            "limit": 200,
        }
        resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        resolved_markets = resp.json()

        for raw in resolved_markets:
            cid = raw.get("conditionId") or raw.get("condition_id") or raw.get("id")
            if cid not in open_ids:
                continue

            outcome_prices = raw.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except (ValueError, TypeError):
                    continue

            if len(outcome_prices) >= 2:
                try:
                    yes_price = float(outcome_prices[0])
                    if yes_price >= 0.99:
                        outcomes[cid] = "YES"
                    elif yes_price <= 0.01:
                        outcomes[cid] = "NO"
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning(f"Failed to fetch resolved outcomes: {e}")

    return outcomes


def _parse_market(raw: dict) -> Optional[MarketSnapshot]:
    """Parse a raw Gamma API market dict into a MarketSnapshot."""
    try:
        condition_id = raw.get("conditionId") or raw.get("condition_id") or raw.get("id")
        if not condition_id:
            return None

        question = raw.get("question") or raw.get("title") or ""
        if not question:
            return None

        description = raw.get("description", "")

        outcome_prices = raw.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (ValueError, TypeError):
                return None
        if len(outcome_prices) < 2:
            return None

        try:
            yes_price = float(outcome_prices[0])
            no_price = float(outcome_prices[1])
        except (ValueError, TypeError):
            return None

        # Normalize prices
        total = yes_price + no_price
        if total > 0:
            yes_price = yes_price / total
            no_price = no_price / total

        volume_24h = float(raw.get("volume24hr", 0) or raw.get("volume_24h", 0) or 0)
        total_volume = float(raw.get("volume", 0) or 0)
        liquidity = float(raw.get("liquidity", 0) or 0)
        end_date = _parse_end_date(raw.get("endDate") or raw.get("end_date"))
        tags = raw.get("tags", [])
        category = parse_category(tags)
        tag_labels = [t.get("label", "") for t in (tags or [])]

        is_resolved = bool(raw.get("resolved", False))
        resolution_outcome = raw.get("resolutionOutcome") or raw.get("resolution_outcome")

        return MarketSnapshot(
            condition_id=condition_id,
            question=question,
            description=description,
            category=category,
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=volume_24h,
            total_volume=total_volume,
            liquidity=liquidity,
            end_date=end_date,
            is_active=not is_resolved,
            is_resolved=is_resolved,
            resolution_outcome=resolution_outcome,
            tags=tag_labels,
        )
    except Exception as e:
        logger.debug(f"Failed to parse market: {e}")
        return None


def scan_markets() -> List[MarketSnapshot]:
    """
    Fetch and filter markets, returning the top candidates for research.
    """
    raw_markets = fetch_active_markets(limit=100)
    logger.info(f"Scanner: fetched {len(raw_markets)} raw markets")

    candidates: List[MarketSnapshot] = []

    for raw in raw_markets:
        market = _parse_market(raw)
        if not market:
            continue

        # Apply filters
        if market.volume_24h < config.MIN_VOLUME_24H:
            continue
        if market.liquidity < config.MIN_LIQUIDITY:
            continue
        if market.days_to_resolve < config.MIN_DAYS_TO_RESOLVE:
            continue
        if market.days_to_resolve > config.MAX_DAYS_TO_RESOLVE:
            continue

        # Skip fully one-sided markets
        if market.yes_price > 0.97 or market.yes_price < 0.03:
            continue

        # Check if snoozed
        mrow = db.get_market(market.condition_id)
        if mrow and mrow["skip_until"]:
            try:
                skip_until = datetime.fromisoformat(mrow["skip_until"])
                if datetime.utcnow() < skip_until:
                    continue
            except (ValueError, TypeError):
                pass

        # Upsert to DB
        db.upsert_market({
            "condition_id": market.condition_id,
            "question": market.question,
            "description": market.description,
            "category": market.category.value,
            "end_date": market.end_date.isoformat(),
            "is_active": 1 if market.is_active else 0,
            "is_resolved": 1 if market.is_resolved else 0,
            "resolution_outcome": market.resolution_outcome,
            "yes_price_latest": market.yes_price,
            "volume_24h_latest": market.volume_24h,
            "total_volume": market.total_volume,
            "liquidity": market.liquidity,
        })

        candidates.append(market)

    # Sort by score (liquidity × distance from 50%) and cap
    candidates.sort(key=lambda m: m.score, reverse=True)
    candidates = candidates[:config.MAX_CANDIDATES_PER_SCAN]

    logger.info(
        f"Scanner: {len(candidates)} candidates passed filters → top {len(candidates)} by score"
    )
    return candidates
