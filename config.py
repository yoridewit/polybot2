from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Required ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ─── Search ───────────────────────────────────────────────────────────────────
SEARCH_PROVIDER: str = os.getenv("SEARCH_PROVIDER", "duckduckgo")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# ─── Scheduling ───────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))

# ─── Paper Trading ────────────────────────────────────────────────────────────
INITIAL_PAPER_BALANCE: float = float(os.getenv("INITIAL_PAPER_BALANCE", "1000"))

# ─── Market Filters ───────────────────────────────────────────────────────────
MIN_VOLUME_24H: float = float(os.getenv("MIN_VOLUME_24H", "1000"))
MIN_LIQUIDITY: float = float(os.getenv("MIN_LIQUIDITY", "500"))
MIN_DAYS_TO_RESOLVE: int = int(os.getenv("MIN_DAYS_TO_RESOLVE", "1"))
MAX_DAYS_TO_RESOLVE: int = int(os.getenv("MAX_DAYS_TO_RESOLVE", "60"))
MAX_CANDIDATES_PER_SCAN: int = int(os.getenv("MAX_CANDIDATES_PER_SCAN", "20"))

# ─── Betting Strategy ─────────────────────────────────────────────────────────
MIN_EDGE_THRESHOLD: float = float(os.getenv("MIN_EDGE_THRESHOLD", "0.08"))
KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", "0.25"))
MAX_BET_FRACTION: float = float(os.getenv("MAX_BET_FRACTION", "0.05"))
MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.50"))

# ─── Token Budget (per day) ───────────────────────────────────────────────────
DAILY_TOKEN_BUDGET: int = int(os.getenv("DAILY_TOKEN_BUDGET", "100000"))

# ─── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "8000"))

# ─── API URLs ─────────────────────────────────────────────────────────────────
GAMMA_API_BASE: str = "https://gamma-api.polymarket.com"
CLOB_API_BASE: str = "https://clob.polymarket.com"

# ─── Models ───────────────────────────────────────────────────────────────────
HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
SONNET_MODEL: str = "claude-sonnet-4-6"

# ─── Research Cache ───────────────────────────────────────────────────────────
RESEARCH_CACHE_HOURS: int = int(os.getenv("RESEARCH_CACHE_HOURS", "6"))
PRICE_CHANGE_RERESEARCH_THRESHOLD: float = float(os.getenv("PRICE_CHANGE_RERESEARCH_THRESHOLD", "0.05"))

# ─── Minimum resolved bets before learning update triggers ────────────────────
LEARNING_MIN_RESOLVED: int = int(os.getenv("LEARNING_MIN_RESOLVED", "5"))

# ─── Bootstrap (one-time historical calibration) ─────────────────────────────
BOOTSTRAP_ENABLED: bool = os.getenv("BOOTSTRAP_ENABLED", "true").lower() in ("true", "1", "yes")
BOOTSTRAP_MARKET_FETCH_LIMIT: int = int(os.getenv("BOOTSTRAP_MARKET_FETCH_LIMIT", "200"))
BOOTSTRAP_MAX_MARKETS: int = int(os.getenv("BOOTSTRAP_MAX_MARKETS", "75"))
