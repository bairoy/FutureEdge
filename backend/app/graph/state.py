"""
app/graph/state.py
===================
LangGraph shared state — Phase 2 (trading) + Phase 4 (investing).

PHASE 2 ADDITIONS:
-------------------
1. llm_rationale : str
   The LLM (Claude) explanation of WHY the orchestrator chose
   its direction. Shown to the risk manager in the HITL review
   modal so they can understand the reasoning before approving.

2. regime : str
   Market regime detected from OHLCV data.
   Values: TRENDING_UP | TRENDING_DOWN | SIDEWAYS | HIGH_VOL | UNKNOWN
   Used by signal_agent to weight indicators appropriately.
   (RSI matters more in SIDEWAYS, MACD matters more in TRENDING)

3. episodic_memory : list[dict]
   Top-K similar past trade situations retrieved from Qdrant.
   Injected into the orchestrator prompt so it can learn from history.
   Example entry:
   {
     "symbol": "RELIANCE",
     "regime": "TRENDING_UP",
     "direction": "LONG",
     "outcome": "WIN",
     "pnl_pct": 1.2,
     "similarity": 0.91
   }
"""

from typing import Optional, Annotated
import operator

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class AgentVote(BaseModel):
    agent:      str
    decision:   str            # BUY | SELL | HOLD | VETO
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning:  str
    metadata:   dict = Field(default_factory=dict)


class TradeProposal(BaseModel):
    symbol:          str
    direction:       str        # LONG | SHORT | NONE
    size:            float      # position size in Rupees
    entry_price:     float
    stop_loss:       Optional[float] = None
    take_profit:     Optional[float] = None
    risk_score:      float = Field(ge=0.0, le=1.0)
    agent_consensus: list[AgentVote] = Field(default_factory=list)
    human_approved:  Optional[bool]  = None
    human_notes:     Optional[str]   = None
    llm_rationale:   Optional[str]   = None  # Phase 2: LLM explanation
    regime:          Optional[str]   = "UNKNOWN"



class PortfolioSnapshot(BaseModel):
    total_equity:       float
    margin_used:        float
    margin_available:   float
    unrealized_pnl:     float
    open_positions:     list[dict] = Field(default_factory=list)
    realized_pnl_today: float = 0.0
    exposure_ratio:     float = 0.0
    max_drawdown_limit: float = 0.2


class MarketContext(BaseModel):
    symbol:           str
    current_price:    float
    ohlcv_1m:         list[dict] = Field(default_factory=list)
    ohlcv_5m:         list[dict] = Field(default_factory=list)
    recent_news:      list[dict] = Field(default_factory=list)
    macro_indicators: dict       = Field(default_factory=dict)
    regime:           str   = "UNKNOWN"   # Phase 2: auto-detected
    volatility_24h:   float = 0.0
    sentiment_score:  float = 0.0
    liquidity_score:  float = 0.0


# ============================================================
# INVESTING MODE (analysis_mode == "INVESTING")
#
# Advisory only — this branch never places an order. It answers
# "is this an investable business, and what is it worth", and the
# human executes at their broker themselves.
# ============================================================


class MissingDatum(BaseModel):
    """
    One thing a stage could not compute, and why.

    Stages append these instead of silently omitting a check. A scorecard that
    quietly drops 4 of 10 checks looks identical to one that passed all 10 —
    this is what makes the difference visible, and what the completeness gate
    on the final verdict is computed from.
    """
    stage:  str            # "BUSINESS" | "FINANCIAL" | "VALUATION"
    field:  str            # e.g. "gross_profit_margin"
    reason: str            # why it is missing, in plain words
    period: Optional[str] = None   # which reporting period it was wanted for


class BusinessReport(BaseModel):
    """Stage 1 — the 18 qualitative questions. Evidence, not a verdict."""
    answers:   list[dict] = Field(default_factory=list)  # {question_no, answer, source_url, source_tier, page}
    red_flags: list[dict] = Field(default_factory=list)  # same shape + name_match_confidence
    # BLOCK only on a Tier-1 regulatory hit with a confirmed identity match.
    # CLEAR means "nothing found", never "clean" — see the plan, 4C.
    gate:      str = "CLEAR"                             # BLOCK | FLAG | CLEAR
    complete:  bool = False


class FinancialReport(BaseModel):
    """Stage 2 — the 10-point checklist. Each check carries its own source."""
    checks:       list[dict] = Field(default_factory=list)  # {n, name, status, value, source}
    completeness: float = 0.0                               # fraction actually computed
    complete:     bool = False


class ValuationReport(BaseModel):
    """Stage 3 — DCF. A band and a sensitivity grid, never a single number."""
    intrinsic:     Optional[float] = None
    upper_band:    Optional[float] = None   # intrinsic x 1.10
    lower_band:    Optional[float] = None   # intrinsic x 0.90
    mos_buy_price: Optional[float] = None   # lower_band x 0.70
    assumptions:   dict = Field(default_factory=dict)   # discount rate, growth stages, terminal, beta source
    sensitivity:   dict = Field(default_factory=dict)
    reverse_dcf_implied_fcf: Optional[float] = None
    complete:      bool = False


class InvestmentThesis(BaseModel):
    """
    What the run concluded. Quality and valuation are stored; the stance
    (BUY/HOLD/WATCH/...) is deliberately NOT — it depends on the live price and
    on whether the position is held, so it is computed on read and would be
    stale the moment the market moved.
    """
    symbol:            str
    quality_grade:     str = "NOT_RATED"   # INVESTMENT_GRADE | WATCHLIST | NOT_INVESTABLE | NOT_RATED
    not_rated_reason:  Optional[str] = None
    completeness:      float = 0.0
    conviction:        float = 0.0         # distinct from completeness, on purpose
    red_flags:         list[dict] = Field(default_factory=list)
    narrative:         str = ""            # non-prescriptive prose; the verb lives in Stance
    data_as_of:        Optional[str] = None


class AgentState(TypedDict):
    """
    Shared state passed between all LangGraph nodes.

    PHASE 2 ADDITIONS:
    - llm_rationale  : Claude's explanation of the consensus decision
    - episodic_memory: similar past trades from Qdrant (populated before orchestrator)
    """

    # --------------------------------------------------------
    # USER CONTEXT
    # --------------------------------------------------------
    user_id: str

    # --------------------------------------------------------
    # USER OVERRIDES (Quantity, Rupees, Kelly, Paper Trade)
    # --------------------------------------------------------
    user_override_quantity: Optional[int]
    user_override_rupees:   Optional[float]
    override_kelly:         bool
    paper_trade:            bool

    # --------------------------------------------------------
    # MARKET + PORTFOLIO INPUTS
    # --------------------------------------------------------
    symbol:         str
    market_context: MarketContext
    portfolio:      PortfolioSnapshot

    # --------------------------------------------------------
    # AGENT OUTPUTS
    # --------------------------------------------------------
    signal_vote:    Optional[AgentVote]
    sentiment_vote: Optional[AgentVote]
    risk_vote:      Optional[AgentVote]
    portfolio_vote: Optional[AgentVote]
    macro_vote:     Optional[AgentVote]

    # --------------------------------------------------------
    # ORCHESTRATOR
    # --------------------------------------------------------
    consensus:     Optional[TradeProposal]
    llm_rationale: Optional[str]   # Phase 2: Claude's reasoning

    # --------------------------------------------------------
    # HITL
    # --------------------------------------------------------
    hitl_required: bool
    hitl_status:   str

    # --------------------------------------------------------
    # EXECUTION
    # --------------------------------------------------------
    executed_trade:  Optional[dict]
    execution_error: Optional[str]

    # --------------------------------------------------------
    # INVESTING MODE
    #
    # Each parallel node writes its OWN top-level key. Three nodes writing
    # into one shared dict would raise InvalidUpdateError on concurrent
    # writes — the same reason the five trading votes above are separate
    # keys rather than one `votes` dict.
    #
    # missing_data is the single exception, and only because operator.add
    # merges concurrent appends: all three stages may report into it.
    # --------------------------------------------------------
    analysis_mode:     str                          # "TRADING" | "INVESTING"

    fundamentals_raw:  Optional[dict]               # data_fetch — single writer
    derived_metrics:   Optional[dict]               # data_fetch — single writer

    business_report:   Optional[BusinessReport]     # Stage 1
    financial_report:  Optional[FinancialReport]    # Stage 2
    valuation_report:  Optional[ValuationReport]    # Stage 3

    investment_thesis: Optional[InvestmentThesis]   # thesis_agent
    missing_data:      Annotated[list[MissingDatum], operator.add]

    # --------------------------------------------------------
    # OBSERVABILITY + MEMORY
    # --------------------------------------------------------
    run_id:          str
    timestamp:       str
    episodic_memory: list[dict]   # Phase 2: filled from Qdrant
    market_vector:   Optional[list[float]]  # Phase 2: computed market embedding vector
    logs:            Annotated[list[str], operator.add]
    completed_nodes: Annotated[list[str], operator.add]