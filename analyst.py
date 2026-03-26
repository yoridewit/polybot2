"""
Claude-powered probability estimator.

Uses Haiku for Tier-1 quick estimates and Sonnet for Tier-2 deep analysis.
Every call logs token usage and respects the daily budget.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional

import config
import database as db
from models import MarketSnapshot, ProbabilityEstimate, ResearchResult, ResearchTier, LearningParams

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an expert prediction market analyst. Your job is to estimate "
    "the probability that a binary event resolves YES. "
    "Be well-calibrated: a 70% estimate should be correct ~70% of the time. "
    "Avoid anchoring on the current market price — form your own independent view. "
    "Return JSON only."
)

OUTPUT_SCHEMA = """{
  "estimated_probability": 0.XX,
  "confidence": 0.XX,
  "reasoning": "2-3 sentence explanation",
  "key_factors": ["factor1", "factor2", "factor3"]
}"""


def _build_prompt(
    market: MarketSnapshot,
    research: Optional[ResearchResult],
    learning_params: LearningParams,
) -> str:
    research_block = ""
    if research and research.summary:
        research_block = f"\nResearch Summary:\n{research.summary}\n"
        if research.key_facts:
            facts = "\n".join(f"- {f}" for f in research.key_facts)
            research_block += f"\nKey Facts:\n{facts}\n"

    cat_adj = learning_params.category_confidence_adjustments.get(market.category.value, 0.0)
    adj_note = ""
    if abs(cat_adj) > 0.02:
        direction = "overestimate" if cat_adj > 0 else "underestimate"
        adj_note = (
            f"\nHistorical note: In the '{market.category.value}' category, "
            f"past estimates have tended to {direction} probability by ~{abs(cat_adj):.0%}. "
            f"Please account for this bias.\n"
        )

    return f"""Market Question: {market.question}
Category: {market.category.value}
Current Market Price (YES): {market.yes_price:.3f}  ← market's implied probability, for reference only
Resolution Date: {market.end_date.strftime('%Y-%m-%d')} ({market.days_to_resolve:.1f} days from now)
{research_block}{adj_note}
Output your estimate as JSON with this exact structure:
{OUTPUT_SCHEMA}

Probability must be between 0.01 and 0.99.
Confidence 0.0 = pure guess, 1.0 = very high certainty."""


def estimate_probability(
    market: MarketSnapshot,
    research: Optional[ResearchResult],
    anthropic_client,
    learning_params: LearningParams,
    force_sonnet: bool = False,
) -> Optional[ProbabilityEstimate]:
    """
    Produce a probability estimate for a market.
    Returns None if budget is exhausted or API call fails.
    """
    used = db.get_daily_token_usage()
    if used >= config.DAILY_TOKEN_BUDGET:
        logger.warning(f"Daily token budget exhausted ({used} tokens used). Skipping estimate.")
        return None

    use_sonnet = force_sonnet or (research is not None and research.tier == ResearchTier.TIER2)
    model = config.SONNET_MODEL if use_sonnet else config.HAIKU_MODEL
    tier_label = "estimate_sonnet" if use_sonnet else "estimate_haiku"

    prompt = _build_prompt(market, research, learning_params)

    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        db.log_token_usage(
            tier=tier_label,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        total_tokens = response.usage.input_tokens + response.usage.output_tokens

        text = response.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object in response")
        data = json.loads(text[start:end])

        prob = float(data["estimated_probability"])
        conf = float(data["confidence"])
        reasoning = str(data.get("reasoning", ""))
        key_factors = list(data.get("key_factors", []))

        prob = max(0.01, min(0.99, prob))
        conf = max(0.0, min(1.0, conf))

        if abs(learning_params.calibration_bias) > 0.01:
            prob = max(0.01, min(0.99, prob - learning_params.calibration_bias))

        estimate = ProbabilityEstimate(
            condition_id=market.condition_id,
            estimated_probability=prob,
            confidence=conf,
            reasoning=reasoning,
            key_factors=key_factors,
            model_version=model,
            tokens_used=total_tokens,
        )
        db.save_estimate(estimate)

        logger.info(
            f"Estimate [{market.condition_id[:8]}] "
            f"model={prob:.3f} market={market.yes_price:.3f} "
            f"edge={prob - market.yes_price:+.3f} conf={conf:.2f} ({model.split('-')[1]})"
        )
        return estimate

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for {market.condition_id}: {e} | raw: {text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Estimate failed for {market.condition_id}: {e}")
        return None
