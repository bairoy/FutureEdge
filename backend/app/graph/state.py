"""
app/graph/state.py
===================
LangGraph shared state — updated for Phase 2.

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
    # OBSERVABILITY + MEMORY
    # --------------------------------------------------------
    run_id:          str
    timestamp:       str
    episodic_memory: list[dict]   # Phase 2: filled from Qdrant
    market_vector:   Optional[list[float]]  # Phase 2: computed market embedding vector
    logs:            Annotated[list[str], operator.add]
    completed_nodes: Annotated[list[str], operator.add]