"""
FutureEdge Agent State Definitions

Central shared state passed between all LangGraph nodes.
Each node reads/writes data from this state object.
"""

# ============================================================
# IMPORTS
# ============================================================

from typing import Optional
from pydantic import BaseModel, Field
from typing_extensions import TypedDict
from typing import Annotated
import operator 

# ============================================================
# AGENT VOTE
# ============================================================

class AgentVote(BaseModel):
    """
    Represents a single agent's trading opinion.
    
    Example:
    - Signal Agent → BUY
    - Risk Agent → HOLD
    - Sentiment Agent → SELL
    """

    # Name of the agent
    agent: str

    # Trading decision made by agent
    # BUY, SELL, HOLD, VETO
    decision: str

    # Confidence score between 0 and 1
    # Example:
    # 0.95 = very confident
    confidence: float = Field(
        ge=0.0,
        le=1.0
    )

    # Why the agent made this decision
    reasoning: str

    # Extra optional data
    # Example:
    # indicators, model outputs, probabilities
    metadata: dict = Field(default_factory=dict)


# ============================================================
# TRADE PROPOSAL
# ============================================================

class TradeProposal(BaseModel):
    """
    Final trade decision produced by orchestrator.
    """

    # Trading symbol
    # Example: BTCUSDT, AAPL, MES
    symbol: str

    # LONG or SHORT
    direction: str

    # Position size
    # Example: 2 BTC or 5 contracts
    size: float

    # Expected trade entry price
    entry_price: float

    # Risk management levels
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    # Overall risk score
    # Lower = safer
    risk_score: float = Field(
        ge=0.0,
        le=1.0
    )

    # Votes collected from all agents
    agent_consensus: list[AgentVote] = Field(
        default_factory=list
    )

    # Human approval system (HITL)
    human_approved: Optional[bool] = None

    # Optional human feedback
    human_notes: Optional[str] = None


# ============================================================
# PORTFOLIO STATE
# ============================================================

class PortfolioSnapshot(BaseModel):
    """
    Current trading account condition.
    """

    # Total account value
    total_equity: float

    # Margin currently locked
    margin_used: float

    # Free capital available
    margin_available: float

    # Current unrealized profit/loss
    unrealized_pnl: float

    # Active open positions
    open_positions: list[dict] = Field(
        default_factory=list
    )

    # Daily realized PnL
    realized_pnl_today: float = 0.0

    # Current exposure level
    # Example: 0.45 = 45% capital exposed
    exposure_ratio: float = 0.0

    # Max allowed drawdown
    max_drawdown_limit: float = 0.2


# ============================================================
# MARKET CONTEXT
# ============================================================

class MarketContext(BaseModel):
    """
    Current market environment data.
    """

    # Trading asset
    symbol: str

    # Current live price
    current_price: float

    # OHLCV candles (1 minute)
    ohlcv_1m: list[dict] = Field(default_factory=list)

    # OHLCV candles (5 minute)
    ohlcv_5m: list[dict] = Field(default_factory=list)

    # Recent news articles/events
    recent_news: list[dict] = Field(default_factory=list)

    # Macro economic indicators
    macro_indicators: dict = Field(default_factory=dict)

    # Market regime
    # Example:
    # TRENDING_BULLISH
    # SIDEWAYS
    # HIGH_VOLATILITY
    regime: str = "UNKNOWN"

    # 24h volatility
    volatility_24h: float = 0.0

    # Market sentiment score
    # Example:
    # -1 = bearish
    # +1 = bullish
    sentiment_score: float = 0.0

    # Liquidity condition
    liquidity_score: float = 0.0


# ============================================================
# LANGGRAPH GLOBAL STATE
# ============================================================

class AgentState(TypedDict):
    """
    Shared state passed between all LangGraph nodes.
    
    Every node:
    - reads from this state
    - updates this state
    """

    # --------------------------------------------------------
    # INPUTS
    # --------------------------------------------------------

    # Trading symbol
    symbol: str

    # Current market information
    market_context: MarketContext

    # Portfolio/account state
    portfolio: PortfolioSnapshot


    # --------------------------------------------------------
    # AGENT OUTPUTS
    # --------------------------------------------------------

    # Technical signal agent output
    signal_vote: Optional[AgentVote]

    # News/sentiment agent output
    sentiment_vote: Optional[AgentVote]

    # Risk management agent output
    risk_vote: Optional[AgentVote]

    # Portfolio optimization agent output
    portfolio_vote: Optional[AgentVote]


    # --------------------------------------------------------
    # ORCHESTRATOR OUTPUT
    # --------------------------------------------------------

    # Final merged decision
    consensus: Optional[TradeProposal]


    # --------------------------------------------------------
    # HUMAN-IN-THE-LOOP (HITL)
    # --------------------------------------------------------

    # Whether human approval required
    hitl_required: bool

    # HITL state
    # PENDING, APPROVED, REJECTED, TIMEOUT
    hitl_status: str


    # --------------------------------------------------------
    # EXECUTION
    # --------------------------------------------------------

    # Executed trade details
    executed_trade: Optional[dict]

    # Execution failure reason
    execution_error: Optional[str]


    # --------------------------------------------------------
    # MEMORY + OBSERVABILITY
    # --------------------------------------------------------

    # Unique workflow run id
    run_id: str

    # Execution timestamp
    timestamp: str

    # Retrieved past similar situations
    episodic_memory: list[dict]

    # Logs generated during workflow
    logs: Annotated[list[str],operator.add]

    # Node execution history
    completed_nodes: Annotated[list[str],operator.add]

    