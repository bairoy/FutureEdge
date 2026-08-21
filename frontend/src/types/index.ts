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

/* ─── INVESTING MODE ──────────────────────────────────────────
 *
 * Shapes returned by /api/v1/investing. Advisory only — nothing on this
 * surface places an order, and there is no field here that could.
 *
 * The split between `quality` (persisted, changes ~quarterly) and `stance`
 * (computed on read, changes every tick) mirrors the backend exactly. See
 * app/services/stance.py for why the stance is never stored.
 */

/** INVESTMENT_GRADE and WATCHLIST pass; NOT_RATED is a refusal to judge, not a bad grade. */
export type QualityGrade =
  | "INVESTMENT_GRADE"
  | "WATCHLIST"
  | "NOT_INVESTABLE"
  | "NOT_RATED";

/** Note the absence of SELL: expensive stops buying, it never forces an exit. */
export type StanceAction =
  | "BUY" | "ADD" | "HOLD" | "WATCH" | "EXIT" | "AVOID" | "NOT_RATED";

export type PriceVsBand =
  | "BELOW_MOS" | "UNDERVALUED" | "FAIRLY_VALUED" | "OVERVALUED";

export type CheckStatus = "PASS" | "FAIL" | "FLAG" | "NOT_COMPUTABLE";

export interface RedFlag {
  question: number;
  flag: string;
  citations?: string[];
}

export interface BusinessAnswer {
  n: number;
  question: string;
  kind: string;
  status: string;          // ANSWERED | NOT_FOUND | NEEDS_EXTERNAL
  answer: string;
  citations: string[];
  is_opinion: boolean;
  /** Which tier answered it. Filings are audited; the open web is not, and the
   *  reader has to be able to tell them apart at a glance. */
  source?: "DOCUMENTS" | "WEB" | "SHAREHOLDING" | "NONE";
  /** Populated only when source is WEB. */
  sources?: { title: string; url: string; domain: string }[];
}

export interface FinancialCheck {
  n: number;               // 101+ are calculation cautions, not scored checks
  name: string;
  status: CheckStatus;
  value: number | string | null;
  detail: string;
  source: string;
}

/** One thing a stage could not compute. Rendered, never swallowed —
 *  a scorecard missing four checks must not look like one that passed ten. */
export interface MissingDatum {
  stage: "BUSINESS" | "FINANCIAL" | "VALUATION";
  field: string;
  reason: string;
  period?: string | null;
}

export interface ValuationReport {
  intrinsic: number | null;
  upper_band: number | null;
  lower_band: number | null;
  mos_buy_price: number | null;
  reverse_dcf_implied_fcf: number | null;
  complete: boolean;
  assumptions: {
    base_fcf_cr?: number;
    stage1_growth_pct?: number;
    stage2_growth_pct?: number;
    terminal_growth_pct?: number;
    discount_rate_pct?: number;
    beta?: number;
    beta_note?: string;
    risk_free_rate_pct?: number;
    risk_free_reviewed?: string;
    equity_risk_premium_pct?: number;
    net_debt_cr?: number;
    shares_outstanding?: number;
    current_price?: number | null;
    price_vs_band?: PriceVsBand;
    terminal_share_of_value?: number;
    reverse_dcf_note?: string;
    warnings?: string[];
  };
  sensitivity: {
    /** { "dr_11.5": { "tg_3.0": 391.2, … } } — rows are discount rates. */
    intrinsic_by_discount_and_terminal_growth?: Record<string, Record<string, number>>;
    base_discount_rate_pct?: number;
  };
}

/** GET /api/v1/investing/{symbol}/thesis */
export interface Thesis {
  symbol: string;
  as_of: string;
  data_as_of: string | null;
  quality: {
    grade: QualityGrade;
    not_rated_reason: string | null;
    completeness: number;    // 0-1
    conviction: number;      // 0-1, deliberately distinct from completeness
    red_flags: RedFlag[];
  };
  valuation: ValuationReport;
  stance: {
    action: StanceAction;
    price_vs_band: PriceVsBand | null;
    trigger_price: number | null;
    /** The exact matrix cell that fired. Never render the verb without it. */
    rule_applied: string;
    horizon: string;
    computed_at: string;
    current_price: number | null;
  };
  owned: { quantity: number; avg_buy_price: number; buy_date: string } | null;
  business: { answers: BusinessAnswer[]; red_flags: RedFlag[]; gate: string; complete: boolean };
  financial: { checks: FinancialCheck[]; completeness: number; complete: boolean };
  missing_data: MissingDatum[];
  narrative: string;
  /** Always true. Stated on every response so the client cannot forget. */
  advisory_only: boolean;
}

export interface InvestingHolding {
  symbol: string;
  quantity: number;
  avg_buy_price: number;
  buy_date: string;
  notes: string | null;
}
