"""
Orchestrator Node

Responsibilities:
-----------------
1. Collect all agent votes
2. Apply weighted consensus logic
3. Detect disagreements between agents
4. Handle veto authority
5. Generate final trade proposal
6. Trigger HITL (Human-In-The-Loop) if needed

IMPORTANT:
-----------
This is the brain of the multi-agent system.

Agents produce opinions.
Orchestrator produces final decision.
"""

# ============================================================
# IMPORTS
# ============================================================

# Structured logging
from loguru import logger

# Shared state + schemas
from app.graph.state import (
    AgentState,
    AgentVote,
    TradeProposal,
    PortfolioSnapshot,
    MarketContext
)


# ============================================================
# AGENT WEIGHTS
# ============================================================

"""
Each agent contributes differently.

Higher weight:
    more influence on final decision

These weights should eventually become:
- performance-adaptive
- dynamically optimized
- reinforcement learned
"""

AGENT_WEIGHTS = {

    "SignalAgent": 0.30,

    "SentimentAgent": 0.20,

    "RiskAgent": 0.25,

    "PortfolioAgent": 0.25
}


# ============================================================
# AGENT DISAGREEMENT METRIC
# ============================================================

def calculate_agent_disagreement(
    votes: list[AgentVote]
) -> float:
    """
    Measure disagreement between agents.

    Purpose:
    --------
    Detect uncertainty in system consensus.

    Interpretation:
    ----------------
    0.0:
        full agreement

    1.0:
        extreme disagreement

    Higher disagreement means:
    - less reliable consensus
    - more uncertainty
    - greater need for HITL
    """

    # --------------------------------------------------------
    # EMPTY VOTE CHECK
    # --------------------------------------------------------

    if not votes:
        return 0.0


    # --------------------------------------------------------
    # IGNORE HOLD DECISIONS
    # --------------------------------------------------------

    """
    HOLD is neutral.

    Only compare active opinions:
    BUY vs SELL
    """

    decisions = [

        vote.decision

        for vote in votes

        if vote.decision != "HOLD"
    ]


    # --------------------------------------------------------
    # TOO FEW ACTIVE DECISIONS
    # --------------------------------------------------------

    if len(decisions) <= 1:
        return 0.0


    # --------------------------------------------------------
    # COUNT BUY VS SELL
    # --------------------------------------------------------

    buy_count = sum(
        1
        for decision in decisions
        if decision == "BUY"
    )

    sell_count = sum(
        1
        for decision in decisions
        if decision == "SELL"
    )


    # --------------------------------------------------------
    # TOTAL ACTIVE DECISIONS
    # --------------------------------------------------------

    total_active = (
        buy_count + sell_count
    )


    if total_active == 0:
        return 0.0


    # --------------------------------------------------------
    # DISAGREEMENT FORMULA
    # --------------------------------------------------------

    """
    Minority faction strength.

    Example:
    --------
    BUY BUY SELL

    minority = 1
    total = 3

    disagreement = 0.33
    """

    disagreement = (
        min(buy_count, sell_count)
        / total_active
    )

    return round(disagreement, 3)


# ============================================================
# ORCHESTRATOR NODE
# ============================================================

def orchestrator_node(
    state: AgentState
) -> dict:
    """
    Main orchestration node.

    Workflow:
    ----------
    Agent Votes
        ↓
    Weighted Consensus
        ↓
    Risk Filtering
        ↓
    Trade Proposal
        ↓
    HITL Evaluation
    """

    try:

        # ====================================================
        # EXTRACT SHARED STATE
        # ====================================================

        market_ctx: MarketContext = (
            state["market_context"]
        )

        portfolio: PortfolioSnapshot = (
            state["portfolio"]
        )

        symbol = market_ctx.symbol

        price = market_ctx.current_price


        # ====================================================
        # COLLECT AGENT VOTES
        # ====================================================

        """
        Retrieve all agent outputs
        from LangGraph shared state.
        """

        votes = []


        for vote_key in [

            "signal_vote",

            "sentiment_vote",

            "risk_vote",

            "portfolio_vote"
        ]:

            vote = state.get(vote_key)

            if vote:

                """
                Votes already stored
                as AgentVote objects.
                """

                votes.append(vote)


        # ====================================================
        # NO VOTES SAFETY CHECK
        # ====================================================

        if not votes:

            logger.warning(
                "No agent votes received"
            )

            empty_proposal = TradeProposal(

                symbol=symbol,

                direction="NONE",

                size=0,

                entry_price=price,

                risk_score=1.0,

                agent_consensus=[]
            )

            return {

                "consensus": empty_proposal,

                "hitl_required": False,

                "hitl_status": "NOT_REQUIRED"
            }


        # ====================================================
        # VETO CHECK
        # ====================================================

        """
        Risk agents may override
        all other agents.

        IMPORTANT:
        -----------
        In institutional systems,
        risk management always has
        final authority.
        """

        vetoes = [

            vote

            for vote in votes

            if vote.decision == "VETO"
        ]


        if vetoes:

            veto = vetoes[0]

            logger.warning(
                f"🚫 Trade VETOED by "
                f"{veto.agent}: "
                f"{veto.reasoning}"
            )

            veto_proposal = TradeProposal(

                symbol=symbol,

                direction="NONE",

                size=0,

                entry_price=price,

                risk_score=1.0,

                agent_consensus=votes,

                human_approved=False
            )

            return {

                "consensus": veto_proposal,

                "hitl_required": False,

                "hitl_status": "NOT_REQUIRED",


                "completed_nodes": [
                    *state.get("completed_nodes", []),
                    "orchestrator"
                ],

                "logs": [
                    *state.get("logs", []),
                    (
                        f"Trade vetoed by "
                        f"{veto.agent}"
                    )
                ]
            }


        # ====================================================
        # WEIGHTED SCORING
        # ====================================================

        """
        Weighted voting system.

        Stronger agents contribute more.

        Formula:
        --------
        weight × confidence
        """

        buy_score = 0.0

        sell_score = 0.0

        total_weight = 0.0


        # ----------------------------------------------------
        # PROCESS EACH AGENT VOTE
        # ----------------------------------------------------

        for vote in votes:

            weight = AGENT_WEIGHTS.get(
                vote.agent,
                0.2
            )

            total_weight += weight


            # ------------------------------------------------
            # BUY CONTRIBUTION
            # ------------------------------------------------

            if vote.decision == "BUY":

                buy_score += (
                    weight
                    * vote.confidence
                )


            # ------------------------------------------------
            # SELL CONTRIBUTION
            # ------------------------------------------------

            elif vote.decision == "SELL":

                sell_score += (
                    weight
                    * vote.confidence
                )


        # ====================================================
        # NORMALIZATION
        # ====================================================

        """
        Normalize scores
        into comparable scale.
        """

        if total_weight > 0:

            buy_score /= total_weight

            sell_score /= total_weight


        # ====================================================
        # DISAGREEMENT ANALYSIS
        # ====================================================

        disagreement = (
            calculate_agent_disagreement(
                votes
            )
        )


        # ====================================================
        # FINAL CONSENSUS DECISION
        # ====================================================

        """
        Thresholds prevent weak trades.

        Small uncertain signals:
            HOLD

        Strong aligned signals:
            LONG / SHORT
        """

        if (

            buy_score > 0.55

            and buy_score > sell_score
        ):

            direction = "LONG"

            confidence = buy_score

            decision = "BUY"


        elif (

            sell_score > 0.55

            and sell_score > buy_score
        ):

            direction = "SHORT"

            confidence = sell_score

            decision = "SELL"


        else:

            direction = "NONE"

            confidence = max(
                buy_score,
                sell_score
            )

            decision = "HOLD"


        # ====================================================
        # RISK SCORE
        # ====================================================

        """
        Higher confidence:
            lower risk

        Higher disagreement:
            higher risk

        Risk score range:
            0 → safe
            1 → dangerous
        """

        risk_score = (

            1.0 - confidence

            + (disagreement * 0.3)
        )


        # ----------------------------------------------------
        # CLAMP RISK SCORE
        # ----------------------------------------------------

        risk_score = min(
            1.0,
            max(0.0, risk_score)
        )


        # ====================================================
        # POSITION SIZING
        # ====================================================

        """
        Conservative position sizing.

        Current rule:
        --------------
        2% of total equity per trade.

        Production systems may use:
        - Kelly criterion
        - volatility targeting
        - dynamic sizing
        """

        if decision != "HOLD":

            position_size = (
                portfolio.total_equity
                * 0.02
            )

        else:

            position_size = 0


        # ====================================================
        # CREATE TRADE PROPOSAL
        # ====================================================

        proposal = TradeProposal(

            symbol=symbol,

            direction=direction,

            size=round(position_size, 2),

            entry_price=price,

            risk_score=round(
                risk_score,
                3
            ),

            agent_consensus=votes,

            human_approved=None
        )


        # ====================================================
        # HUMAN-IN-THE-LOOP (HITL)
        # ====================================================

        """
        Some trades require
        human approval.

        Common institutional practice.
        """

        hitl_required = False

        hitl_reasons = []


        # ----------------------------------------------------
        # HIGH RISK CHECK
        # ----------------------------------------------------

        if risk_score > 0.7:

            hitl_required = True

            hitl_reasons.append(
                f"High risk score: "
                f"{risk_score:.2f}"
            )


        # ----------------------------------------------------
        # HIGH DISAGREEMENT CHECK
        # ----------------------------------------------------

        if disagreement > 0.4:

            hitl_required = True

            hitl_reasons.append(
                f"High disagreement: "
                f"{disagreement:.2f}"
            )


        # ----------------------------------------------------
        # LARGE POSITION CHECK
        # ----------------------------------------------------

        if position_size > (
            portfolio.total_equity * 0.05
        ):

            hitl_required = True

            hitl_reasons.append(
                f"Large position: "
                f"${position_size:.2f}"
            )


        # ====================================================
        # LOG FINAL DECISION
        # ====================================================

        logger.info(
            f"🎯 Orchestrator | "
            f"{decision} {symbol} | "
            f"Conf={confidence:.2f} | "
            f"Risk={risk_score:.2f} | "
            f"Disagreement={disagreement:.2f} | "
            f"HITL={hitl_required}"
        )


        # ====================================================
        # RETURN LANGGRAPH STATE UPDATE
        # ====================================================

        return {

            # Final trade proposal
            "consensus": proposal,

            # HITL controls
            "hitl_required": hitl_required,

            "hitl_status": (
                "PENDING"
                if hitl_required
                else "NOT_REQUIRED"
            ),

            # Workflow observability
            "completed_nodes": [
            
                "orchestrator"
            ],

            "logs": [
               
                (
                    f"Orchestrator generated "
                    f"{decision} for {symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Orchestrator failure is critical.

        Safest action:
        no trade.
        """

        logger.exception(
            f"Orchestrator failure: {str(e)}"
        )

        fallback_proposal = TradeProposal(

            symbol="UNKNOWN",

            direction="NONE",

            size=0,

            entry_price=0,

            risk_score=1.0
        )

        return {

            "consensus": fallback_proposal,

            "hitl_required": True,

            "hitl_status": "PENDING",

            "execution_error": str(e),

            "logs": [
               
                f"Orchestrator failed: {str(e)}"
            ]
        }
        