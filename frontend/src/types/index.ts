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
  quantity: number;
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

/**
 * Two different payloads arrive on the "hitl_pending" channel, discriminated
 * by `status`:
 *   - a proposal awaiting approval  (orchestration_agent._publish_results)
 *   - a resolution notice           (human_agent, published on workflow resume)
 * Modelling only the first is what broke the build — the resolution branch in
 * useWebSocket reads fields the old single-shape type didn't have.
 */
export interface HITLProposalPayload {
  status?: undefined;
  symbol: string;
  reasons: string[];
  proposal: {
    run_id: string;
    symbol: string;
    direction: string;
    size: number;
    entry_price: number;
    risk_score: number;
    stop_loss: number | null;
    take_profit: number | null;
    llm_rationale: string | null;
    hitl_required: boolean;
    hitl_reasons: string[];
    votes: AgentVote[];
  };
}

export interface HITLResolvedPayload {
  status: "RESOLVED";
  run_id: string;
  decision: string;
  hitl_status: "APPROVED" | "REJECTED";
}

export interface HITLPendingMessage {
  type: "hitl_pending";
  data: HITLProposalPayload | HITLResolvedPayload;
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

/**
 * GET /api/v1/system/status
 *
 * Feed and broker are deliberately separate objects: where prices come from
 * and where orders go are independent choices. The usual setup is a live
 * Zerodha feed with the mock broker — real prices, simulated money.
 */
export interface SystemStatus {
  market_open: boolean;
  feed: {
    mode: "zerodha" | "mock";
    /** What is ACTUALLY serving prices right now, which may not equal `mode`. */
    source: "zerodha_ticker" | "yfinance";
    label: string;
    /** Human-readable reason, including WHY a feed is degraded. */
    detail: string;
    is_live: boolean;
    is_delayed: boolean;
    streaming: boolean;
    tick_age_seconds: number | null;
  };
  broker: {
    mode: "zerodha" | "mock";
    label: string;
    detail: string;
    /** True means orders hit a REAL Zerodha account. */
    is_live: boolean;
    is_paper: boolean;
    connected: boolean;
  };
  zerodha: {
    configured: boolean;
    connected: boolean;
  };
}