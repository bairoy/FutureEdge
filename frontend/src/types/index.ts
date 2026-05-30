/**
 * frontend/src/types/index.ts
 *
 * All TypeScript types for the application.
 * Every type here matches an exact backend response shape.
 * One place to update when the backend API changes.
 */

// ─── AUTH ───────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: "viewer" | "trader" | "risk_manager" | "admin";
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

// ─── WORKFLOW ────────────────────────────────────────────────

export interface AgentVote {
  agent: string;
  decision: "BUY" | "SELL" | "HOLD" | "VETO";
  confidence: number;
  reasoning: string;
  metadata?: Record<string, unknown>;
}

export interface WorkflowRunResponse {
  thread_id: string;
  hitl_status: "NOT_REQUIRED" | "PENDING" | "APPROVED" | "REJECTED";
  direction: "LONG" | "SHORT" | "NONE";
  risk_score: number;
  proposal?: any;
  votes?: AgentVote[];
  reasons?: string[];
  execution_error: string | null;
  completed_nodes: string[];
  logs: string[];
  execution_summary?: {
    market_open: boolean;
    symbol_mapped: string;
    shares_requested: number | null;
    position_rupees: number;
    method: "kelly_based" | "user_override";
    kelly_fraction: number;
    blocking_reason: string | null;
  };
}

export interface HITLResumeResponse {
  thread_id: string;
  decision: "APPROVE" | "REJECT";
  hitl_status: string;
  executed_trade: Record<string, unknown> | null;
  execution_error: string | null;
  logs: string[];
}

// ─── TRADES ──────────────────────────────────────────────────

export interface Trade {
  id: string;
  user_id: string;
  run_id: string;
  symbol: string;
  direction: "LONG" | "SHORT" | "NONE";
  size: number;
  entry_price: number;
  risk_score: number;
  status: "OPEN" | "CLOSED" | "REJECTED" | "FAILED" | "NONE";
  hitl_required: boolean;
  human_approved: boolean | null;
  human_notes: string | null;
  agent_consensus: AgentVote[] | null;
  broker: string | null;
  broker_order_id: string | null;
  actual_fill_price: number | null;
  slippage: number | null;
  exit_price: number | null;
  realized_pnl: number | null;
  pnl_pct: number | null;
  opened_at: string;
  closed_at: string | null;
}

// ─── KILL SWITCH ──────────────────────────────────────────────

export interface KillSwitchStatus {
  halted: boolean;
  status: "HALTED" | "ACTIVE";
}

// ─── EPISODIC MEMORY (Phase 2) ────────────────────────────────

export interface EpisodicMemory {
  run_id: string;
  symbol: string;
  direction: string;
  outcome: "WIN" | "LOSS" | "UNKNOWN";
  pnl_pct: number;
  regime: string;
  similarity: number;
}

// ─── WEBSOCKET MESSAGES ───────────────────────────────────────
// The market WebSocket sends JSON with a "type" discriminator field.

export interface TickMessage {
  type: "tick";
  data: {
    ltp: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    timestamp: string;
  };
}

export interface AgentResultMessage {
  type: "agent_result";
  data: {
    symbol: string;
    direction: string;
    size: number;
    entry_price: number;
    risk_score: number;
    hitl_required: boolean;
    hitl_reasons: string[];
    llm_rationale: string | null;
    memories: EpisodicMemory[];
    votes: AgentVote[];
  };
}

export interface HITLPendingMessage {
  type: "hitl_pending";
  data: {
    symbol: string;
    reasons: string[];
    llm_rationale: string | null;
    memories: EpisodicMemory[];
    proposal: {
      run_id?: string;
      direction: string;
      size: number;
      entry_price: number;
      risk_score: number;
      votes: AgentVote[];
    };
  };
}

export interface TradeMessage {
  type: "trade";
  data: {
    run_id: string;
    user_id: string;
    symbol: string;
    direction: string;
    shares: number;
    price: number;
    status: string;
    success: boolean;
  };
}

export interface KillSwitchMessage {
  type: "kill_switch";
  data: {
    halted: boolean;
    reason?: string;
    halted_by?: string;
  };
}

export interface ClearTicksMessage {
  type: "clear_ticks";
}

export type WebSocketMessage =
  | TickMessage
  | AgentResultMessage
  | HITLPendingMessage
  | TradeMessage
  | KillSwitchMessage
  | ClearTicksMessage;