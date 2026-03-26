"""
3-Stage research funnel.

Stage 0: Free Python heuristics  — already done in market_scanner
Stage 1: Single Haiku batch call — ranks candidates, picks top N for deep research
Stage 2: Free web search         — DuckDuckGo or Tavily
Stage 3: Haiku/Sonnet analysis   — per-market probability estimate

Token budget is checked before every Claude call.
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from typing import List, Optional, Tuple

import requests

import config
import database as db
from models import MarketSnapshot, ResearchResult, ResearchTier

logger = logging.getLogger(__name__)

# ─── Token budget guard ───────────────────────────────────────────────────────

def _budget_ok() -> bool:
    used = db.get_daily_token_usage()
    return used < config.DAILY_TOKEN_BUDGET


def _log_tokens(tier: str, model: str, usage) -> None:
    db.log_token_usage(
        tier=tier,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


# ─── Web Search ───────────────────────────────────────────────────────────────

def _search_duckduckgo(query: str, max_results: int = 5) -> List[str]:
    """DuckDuckGo search — completely free, no API key."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        snippets = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            if body:
                snippets.append(f"{title}: {body[:300]}")
        return snippets
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed for '{query}': {e}")
        return []


def _search_tavily(query: str, max_results: int = 5) -> List[str]:
    """Tavily search — paid, higher quality."""
    if not config.TAVILY_API_KEY:
        logger.warning("Tavily API key not set, falling back to DuckDuckGo")
        return _search_duckduckgo(query, max_results)
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=config.TAVILY_API_KEY)
        response = client.search(query=query, max_results=max_results, search_depth="basic")
        snippets = []
        for r in response.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")
            if content:
                snippets.append(f"{title}: {content[:300]}")
        return snippets
    except Exception as e:
        logger.warning(f"Tavily search failed for '{query}': {e}")
        return _search_duckduckgo(query, max_results)


def _web_search(query: str, max_results: int = 5) -> List[str]:
    if config.SEARCH_PROVIDER == "tavily":
        return _search_tavily(query, max_results)
    return _search_duckduckgo(query, max_results)


def _wikipedia_summary(topic: str) -> Optional[str]:
    """Free Wikipedia API — great for factual background."""
    try:
        url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(topic)
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            extract = data.get("extract", "")
            return extract[:500] if extract else None
    except Exception:
        pass
    return None


# ─── Stage 1: Haiku batch ranking ─────────────────────────────────────────────

def rank_candidates_with_haiku(markets: List[MarketSnapshot], anthropic_client) -> List[Tuple[str, str]]:
    """
    One Haiku call to rank all candidates.
    Returns list of (condition_id, one-line reason) for the top markets worth researching.
    """
    if not markets:
        return []
    if not _budget_ok():
        logger.warning("Daily token budget exceeded — skipping Haiku ranking")
        return [(m.condition_id, "budget limit") for m in markets[:5]]

    market_list = "\n".join(
        f"{i+1}. [{m.condition_id[:8]}] {m.question} | YES={m.yes_price:.2f} | "
        f"vol24h=${m.volume_24h:.0f} | {m.days_to_resolve:.1f}d left | {m.category.value}"
        for i, m in enumerate(markets)
    )

    prompt = f"""You are a prediction market analyst. Below are {len(markets)} active markets.
Identify which markets are most likely to have a PREDICTABLE edge — where current market price
may not reflect actual probability based on known facts (vs pure sentiment or uncertainty).

Markets:
{market_list}

Respond with JSON only — a list of the top 5 market IDs worth deep research, with a one-line reason each.
Format: [{{"id": "XXXXXXXX", "reason": "..."}}]
Use the first 8 characters of the ID shown in brackets."""

    try:
        response = anthropic_client.messages.create(
            model=config.HAIKU_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        _log_tokens("stage1_ranking", config.HAIKU_MODEL, response.usage)

        text = response.content[0].text.strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array found in Haiku response")
        ranked = json.loads(text[start:end])

        id_map = {m.condition_id[:8]: m.condition_id for m in markets}
        result = []
        for item in ranked:
            short_id = item.get("id", "")
            full_id = id_map.get(short_id)
            if full_id:
                result.append((full_id, item.get("reason", "")))
        logger.info(f"Stage 1 ranked {len(result)} markets for deep research")
        return result

    except Exception as e:
        logger.error(f"Haiku batch ranking failed: {e}")
        return [(m.condition_id, "fallback") for m in markets[:5]]


# ─── Stage 2: Web search ──────────────────────────────────────────────────────

def gather_web_research(market: MarketSnapshot) -> Tuple[List[str], List[str]]:
    """
    Run 2-3 targeted searches for a market.
    Returns (search_queries, snippets).
    """
    queries = []
    snippets = []

    # Query 1: primary question (latest news)
    q1 = f"{market.question} 2025 2026"
    queries.append(q1)
    snippets.extend(_web_search(q1, max_results=4))
    time.sleep(0.5)

    # Query 2: key entity / subject matter
    words = market.question.split()
    q2 = " ".join(words[:6]) + " latest update"
    if q2 != q1:
        queries.append(q2)
        snippets.extend(_web_search(q2, max_results=3))
        time.sleep(0.5)

    # Query 3: Wikipedia background for sports/politics/science markets
    if market.category.value in ("politics", "sports", "science", "world"):
        wiki_topic = " ".join(words[:4])
        wiki = _wikipedia_summary(wiki_topic)
        if wiki:
            snippets.append(f"Wikipedia: {wiki}")

    # Deduplicate and cap
    seen = set()
    unique_snippets = []
    for s in snippets:
        key = s[:80]
        if key not in seen:
            seen.add(key)
            unique_snippets.append(s)

    return queries, unique_snippets[:12]


# ─── Stage 3: Deep analysis ───────────────────────────────────────────────────

def analyse_market(
    market: MarketSnapshot,
    snippets: List[str],
    anthropic_client,
    use_sonnet: bool = False,
) -> Tuple[str, List[str], int, str]:
    """
    Claude analysis: returns (summary, key_facts, tokens_used, model_used).
    use_sonnet=True for high-edge markets, Haiku otherwise.
    """
    model = config.SONNET_MODEL if use_sonnet else config.HAIKU_MODEL
    tier_label = "stage3_sonnet" if use_sonnet else "stage3_haiku"

    if not _budget_ok():
        logger.warning("Budget exceeded — skipping deep analysis")
        return "Budget limit reached.", [], 0, model

    snippets_text = "\n".join(f"- {s}" for s in snippets[:10]) or "No web results found."

    system = (
        "You are an expert prediction market analyst. Analyze the given market and research snippets. "
        "Return JSON only with this exact structure:\n"
        '{"summary": "2-3 sentence analysis", "key_facts": ["fact1", "fact2", "fact3"]}'
    )

    user = f"""Market: {market.question}
Category: {market.category.value}
Current YES price: {market.yes_price:.2f} (market implied probability)
Resolves: {market.end_date.strftime('%Y-%m-%d')} ({market.days_to_resolve:.1f} days)

Research snippets:
{snippets_text}

Provide a factual summary and list of key facts that affect the probability of YES resolving."""

    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        _log_tokens(tier_label, model, response.usage)
        tokens = response.usage.input_tokens + response.usage.output_tokens

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines()
                if not line.strip().startswith("```")
            ).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        data = json.loads(text[start:end])
        summary = data.get("summary", "")
        key_facts = data.get("key_facts", [])
        return summary, key_facts, tokens, model

    except Exception as e:
        logger.error(f"Deep analysis failed for {market.condition_id}: {e}")
        return f"Analysis failed: {e}", [], 0, model


# ─── Cache check ──────────────────────────────────────────────────────────────

def _needs_research(market: MarketSnapshot) -> bool:
    """Return True if we should re-research this market."""
    row = db.get_latest_research(market.condition_id)
    if not row:
        return True
    try:
        researched_at = datetime.fromisoformat(row["researched_at"])
        hours_since = (_utcnow() - researched_at).total_seconds() / 3600
        if hours_since < config.RESEARCH_CACHE_HOURS:
            mrow = db.get_market(market.condition_id)
            if mrow:
                cached_price = mrow["yes_price_latest"] or market.yes_price
                price_delta = abs(market.yes_price - cached_price)
                if price_delta < config.PRICE_CHANGE_RERESEARCH_THRESHOLD:
                    return False
    except Exception:
        pass
    return True


# ─── Public API ───────────────────────────────────────────────────────────────

def run_research_pipeline(
    markets: List[MarketSnapshot],
    anthropic_client,
    learning_params,
) -> List[ResearchResult]:
    """
    Full 3-stage funnel. Returns ResearchResult for each market that was researched.
    """
    results: List[ResearchResult] = []

    to_research = [m for m in markets if _needs_research(m)]
    if not to_research:
        logger.info("All candidates have fresh cached research")
        return results

    # Stage 1: Haiku batch ranking → top candidates for web search
    ranked = rank_candidates_with_haiku(to_research, anthropic_client)
    ranked_ids = {cid for cid, _ in ranked}

    for market in to_research:
        tier = ResearchTier.TIER1
        search_queries: List[str] = []
        raw_snippets: List[str] = []
        summary = ""
        key_facts: List[str] = []
        total_tokens = 0
        model_used = config.HAIKU_MODEL

        if market.condition_id in ranked_ids:
            tier = ResearchTier.TIER2
            search_queries, raw_snippets = gather_web_research(market)

            top3_ids = {cid for cid, _ in ranked[:3]}
            use_sonnet = market.condition_id in top3_ids

            summary, key_facts, tokens, model_used = analyse_market(
                market, raw_snippets, anthropic_client, use_sonnet=use_sonnet
            )
            total_tokens += tokens
        else:
            for cid, reason in ranked:
                if cid == market.condition_id:
                    summary = reason
                    break
            if not summary:
                summary = "Not selected for deep research this cycle."

        result = ResearchResult(
            condition_id=market.condition_id,
            tier=tier,
            search_queries=search_queries,
            raw_snippets=raw_snippets,
            summary=summary,
            key_facts=key_facts,
            tokens_used=total_tokens,
            model_used=model_used,
        )
        db.save_research(result)
        results.append(result)
        logger.info(
            f"Researched [{market.condition_id[:8]}] {market.question[:60]} "
            f"| tier={tier.value} | tokens={total_tokens}"
        )

    return results
