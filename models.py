from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
from datetime import datetime
import uuid


class MarketCategory(str, Enum):
    POLITICS = "politics"
    CRYPTO = "crypto"
    SPORTS = "sports"
    SCIENCE = "science"
    ENTERTAINMENT = "entertainment"
    FINANCE = "finance"
    WORLD = "world"
    OTHER = "other"


class BetSide(str, Enum):
    YES = "YES"
    NO = "NO"


class BetStatus(str, Enum):
    OPEN = "open"
    RESOLVED_WIN = "resolved_win"
    RESOLVED_LOSS = "resolved_loss"
    RESOLVED_PUSH = "resolved_push"
    CANCELLED = "cancelled"


class ResearchTier(str, Enum):
    NONE = "none"
    TIER1 = "tier1"  # Haiku batch ranking
    TIER2 = "tier2"  # Web search + Haiku/Sonnet deep analysis


@dataclass
class MarketSnapshot:
    """A Polymarket market at a point in time."""
    condition_id: str
    question: str
    description: str
    category: MarketCategory
    yes_price: float          # 0.0-1.0 (= implied YES probability)
    no_price: float
    volume_24h: float
    total_volume: float
    liquidity: float
    end_date: datetime
    is_active: bool
    is_resolved: bool
    resolution_outcome: Optional[str]  # "YES", "NO", or None
    tags: List[str]
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def days_to_resolve(self) -> float:
        delta = self.end_date - datetime.utcnow()
        return max(0.0, delta.total_seconds() / 86400)

    @property
    def distance_from_even(self) -> float:
        """How far the market is from 50/50 — larger = more one-sided."""
        return abs(self.yes_price - 0.5)

    @property
    def score(self) -> float:
        """Prioritisation score for research queue."""
        return self.liquidity * self.distance_from_even


@dataclass
class ResearchResult:
    """Aggregated research for a market."""
    condition_id: str
    tier: ResearchTier
    search_queries: List[str]
    raw_snippets: List[str]
    summary: str
    key_facts: List[str]
    tokens_used: int
    model_used: str
    researched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProbabilityEstimate:
    """Claude's probability assessment for a market."""
    condition_id: str
    estimated_probability: float   # 0.0-1.0 for YES outcome
    confidence: float              # 0.0-1.0
    reasoning: str
    key_factors: List[str]
    model_version: str
    tokens_used: int
    estimated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EdgeCalculation:
    """Edge analysis comparing model estimate to market price."""
    condition_id: str
    model_probability: float
    market_probability: float
    edge: float                   # model_probability - market_probability
    bet_side: Optional[BetSide]
    kelly_fraction: float
    recommended_fraction: float   # fractional Kelly
    recommended_bet_size: float   # in USD
    meets_threshold: bool


@dataclass
class Bet:
    """A paper bet placed on a market."""
    condition_id: str
    question: str
    side: BetSide
    amount: float
    price_at_bet: float
    model_probability: float
    edge: float
    kelly_fraction: float
    research_tier: ResearchTier
    category: MarketCategory
    bet_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_paper: bool = True
    status: BetStatus = BetStatus.OPEN
    placed_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    pnl: Optional[float] = None
    resolution_outcome: Optional[str] = None

    @property
    def potential_profit(self) -> float:
        """Max profit if bet wins."""
        if self.side == BetSide.YES:
            return self.amount * (1.0 / self.price_at_bet - 1.0)
        else:
            return self.amount * (1.0 / (1.0 - self.price_at_bet) - 1.0)

    @property
    def potential_loss(self) -> float:
        return self.amount


@dataclass
class LearningParams:
    """Adaptive strategy parameters updated by the learning module."""
    min_edge_threshold: float = 0.08
    kelly_fraction_multiplier: float = 0.25
    max_bet_fraction: float = 0.05
    tier2_edge_threshold: float = 0.06    # edge needed to run Stage 2 research
    min_confidence: float = 0.50
    category_confidence_adjustments: Dict[str, float] = field(default_factory=dict)
    calibration_bias: float = 0.0         # systematic over/under confidence
    total_resolved: int = 0
    version: int = 1
    last_updated: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PortfolioState:
    """Current paper trading portfolio snapshot."""
    cash_balance: float
    open_bets_value: float          # sum of amounts in open bets
    total_value: float              # cash + open_bets_value
    realized_pnl: float
    num_open_bets: int
    num_resolved_bets: int
    num_wins: int
    num_losses: int
    win_rate: float
    roi_pct: float
    snapshot_at: datetime = field(default_factory=datetime.utcnow)
