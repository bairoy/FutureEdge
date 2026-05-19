"""
Execution Node

Responsibilities:
-----------------
1. Final trade execution layer
2. Validate safety conditions
3. Check emergency kill switch
4. Verify HITL approval
5. Simulate trade execution
6. Create executed trade record

IMPORTANT:
-----------
This node is the ACTION layer
of the entire trading system.

Everything before this node:
    analysis + reasoning

This node:
    execution
"""

# ============================================================
# IMPORTS
# ============================================================

# Generate unique trade ids
import uuid

# Trade timestamps
from datetime import datetime

# Structured logging
from loguru import logger

# Shared Redis client
from app.db.redis import redis_client

# Shared state + schemas
from app.graph.state import (
    AgentState,
    TradeProposal
)


# ============================================================
# EXECUTION NODE
# ============================================================

async def execution_node(
    state: AgentState
) -> dict:
    """
    Main execution node.

    Workflow:
    ----------
    Trade Proposal
        ↓
    Kill Switch Check
        ↓
    HITL Validation
        ↓
    Trade Execution
        ↓
    Execution Record
    """

    try:

        # ====================================================
        # EXTRACT TRADE PROPOSAL
        # ====================================================

        """
        Final orchestrator output.

        Contains:
        - direction
        - position size
        - entry price
        - risk score
        """

        proposal: TradeProposal = (
            state["consensus"]
        )


        # ====================================================
        # KILL SWITCH CHECK
        # ====================================================

        """
        Emergency global shutdown system.

        Used to instantly stop:
        - trading bugs
        - runaway execution
        - market emergencies
        - infrastructure failures

        Redis acts as centralized
        ultra-fast control state.
        """

        halt = await redis_client.get(
            "TRADING_HALT"
        )


        # ----------------------------------------------------
        # EXECUTION BLOCKED
        # ----------------------------------------------------

        if halt == "1":

            logger.critical(
                "🛑 KILL SWITCH ACTIVE "
                "- Execution blocked"
            )

            return {

                "executed_trade": None,

                "execution_error": (
                    "KILL_SWITCH_ACTIVE"
                ),


                "logs": [
                    *state.get("logs", []),
                    "Execution blocked by kill switch"
                ]
            }


        # ====================================================
        # NO-TRADE CHECK
        # ====================================================

        """
        HOLD decisions produce:
        no executable trade.
        """

        if (

            proposal.direction == "NONE"

            or proposal.size <= 0
        ):

            logger.info(
                "⏭️ No trade to execute"
            )

            return {

                "executed_trade": None,


                "completed_nodes": [
                   
                    "execution"
                ],

                "logs": [
                    
                    "No trade executed"
                ]
            }


        # ====================================================
        # HITL VALIDATION
        # ====================================================

        """
        Double-check human approval.

        IMPORTANT:
        -----------
        Execution systems should NEVER
        trust upstream systems blindly.

        Multiple safety layers are critical
        in financial infrastructure.
        """

        if (

            state.get("hitl_required")

            and not proposal.human_approved
        ):

            logger.error(
                "❌ Trade rejected "
                "- HITL not approved"
            )

            return {

                "executed_trade": None,

                "execution_error": (
                    "HITL_NOT_APPROVED"
                ),

                "logs": [
                    
                    "Execution rejected due to missing HITL approval"
                ]
            }


        # ====================================================
        # SIMULATED TRADE EXECUTION
        # ====================================================

        """
        Current system:
        paper trading / simulated fills.

        Production version later becomes:
        ---------------------------------
        broker.place_order(...)
        """

        trade = {

            # Unique trade identifier
            "trade_id": str(
                uuid.uuid4()
            ),

            # Trade symbol
            "symbol": proposal.symbol,

            # LONG or SHORT
            "direction": proposal.direction,

            # Position size
            "size": proposal.size,

            # Entry price
            "entry_price": proposal.entry_price,

            # Trade lifecycle status
            "status": "OPEN",

            # Execution timestamp
            "opened_at": (
                datetime.utcnow().isoformat()
            ),

            # Workflow tracking
            "run_id": state["run_id"],

            # Agent reasoning history
            "agent_consensus": [

                vote.model_dump()

                for vote in proposal.agent_consensus
            ],

            # Human oversight state
            "human_approved": (
                proposal.human_approved
            ),

            # Risk metadata
            "risk_score": proposal.risk_score
        }


        # ====================================================
        # EXECUTION LOGGING
        # ====================================================

        logger.info(
            f"✅ EXECUTED | "
            f"{trade['direction']} "
            f"{trade['size']} "
            f"{trade['symbol']} "
            f"@ {trade['entry_price']}"
        )


        # ====================================================
        # RETURN STATE UPDATE
        # ====================================================

        """
        Executed trade becomes:
        part of workflow state.

        Later systems may use this for:
        - PnL tracking
        - portfolio updates
        - analytics
        - RL training
        - episodic memory
        """

        return {

            # Executed position
            "executed_trade": trade,

            # Workflow observability
            
            "completed_nodes": [
                
                "execution"
            ],

            "logs": [
               
                (
                    f"Executed "
                    f"{proposal.direction} "
                    f"{proposal.symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Execution failures are critical.

        Failed execution can create:
        - inconsistent state
        - orphaned positions
        - financial losses

        Therefore:
        log aggressively and fail safely.
        """

        logger.exception(
            f"Execution failure: {str(e)}"
        )

        return {

            "executed_trade": None,

            "execution_error": str(e),

               
            "logs": [
                
                f"Execution failed: {str(e)}"
            ]
        }