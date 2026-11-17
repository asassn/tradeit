/**
 * The typed client for the read side.
 *
 * Hand-written for now and deliberately thin. The plan is to generate it from
 * FastAPI's own `/openapi.json` so the frontend cannot drift from the
 * backend; until the route surface settles that generation would churn more
 * than it protects, so the types here mirror the Python response shapes and a
 * test on the Python side will assert they still match.
 *
 * `BASE` is relative, so the bundle works wherever FastAPI mounts it and the
 * dev server's proxy sends the same paths to the same places.
 */

const BASE = import.meta.env.DEV ? "http://127.0.0.1:8000" : ".";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    let detail = `${path} → ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* the body was not JSON; the status line is the message */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

/* ---- health ------------------------------------------------------------ */

export interface Health {
  reachable: boolean;
  record: string;
  corpus_attached: boolean;
  has_scores: boolean;
  has_runs: boolean;
  note: string | null;
}

export const getHealth = () => get<Health>("/health");

/* ---- securities -------------------------------------------------------- */

export interface SearchHit {
  security_id: number;
  ticker: string;
  name: string;
  is_current: boolean;
  first_day: string | null;
  last_day: string | null;
}

export const searchSecurities = (q: string) =>
  get<{ query: string; results: SearchHit[] }>(
    `/securities/search?q=${encodeURIComponent(q)}`,
  );

export interface Identity {
  ticker: string;
  name: string;
  is_current: boolean;
  first_day: string | null;
  last_day: string | null;
}

export interface Multiple {
  basis: string;
  value: number;
  rank: number | null;
  rank_means: string;
  history_size: number | null;
  explanation: string;
}

export interface Valuation {
  security_id: number;
  available: boolean;
  identity: Identity | null;
  note?: string;
  as_of?: string;
  price?: { close: number; session: string };
  fcf_yield?: number | null;
  fcf_yield_rank?: number | null;
  fcf_yield_history_size?: number;
  fcf_yield_history?: { session: string; value: number }[];
  free_cash_flow?: {
    value: number;
    operating_cash: number;
    capital_expenditure: number;
    fiscal_year_end: string;
    first_filed: string;
  } | null;
  enterprise_value?: {
    value: number;
    market_cap: number;
    debt: number;
    cash: number;
    debt_metrics: string[];
    debt_may_be_understated: string | null;
  } | null;
  implied_growth?: { discount_rate: number; growth: number }[];
  quality?: { name: string; detail: string }[];
  multiples?: Multiple[];
  unresolved?: { basis: string; reason: string }[];
  explanations?: string[];
  caveats?: string[];
}

export const getValuation = (id: number | string) =>
  get<Valuation>(`/securities/${id}/valuation`);

/* ---- evidence ---------------------------------------------------------- */

export interface Run {
  id: number;
  name: string;
  status: string;
  start: string;
  end: string;
  universe: string;
  is_out_of_sample: boolean;
  run_manifest_id: number;
  trades_recorded: number;
  trade_count_column: number;
  trade_count_disagrees: string | null;
  statistically_meaningful: boolean;
  metrics: {
    total_return_pct: number | null;
    cagr: number | null;
    max_drawdown_pct: number | null;
    sharpe: number | null;
    win_rate: number | null;
    profit_factor: number | null;
    exposure_pct: number | null;
    benchmark_return_pct: number | null;
  };
}

export const getRuns = () => get<Run[]>("/runs");

/* ---- decisions --------------------------------------------------------- */

export interface SessionSummary {
  session_date: string;
  scores: number;
}

export const getSessions = () =>
  get<{ returned: number; sessions: SessionSummary[] }>("/sessions");

export interface SessionDetail {
  session_date: string;
  portfolio_id: number;
  walkable_end_to_end: boolean;
  missing: string[];
  scores: {
    instrument_id: number;
    direction: string;
    total: number;
    components: Record<string, number>;
    rank: number | null;
  }[];
  orders: {
    client_order_id: string;
    instrument_id: number;
    side: string;
    quantity: string;
    status: string;
    intent: string | null;
    submitted_at: string | null;
  }[];
  executions: {
    order_id: number;
    instrument_id: number;
    quantity: string;
    price: string;
    commission: string;
    slippage: string;
    venue: string;
  }[];
  journal: {
    entry_type: string;
    summary: string;
    rationale: string | null;
    context: unknown;
  }[];
}

export const getSession = (date: string) =>
  get<SessionDetail>(`/sessions/${date}`);

/* ---- horizons ---------------------------------------------------------- */

export interface InstrumentRow {
  instrument_id: number;
  ticker: string | null;
  ticker_note: string | null;
  scores: number;
  first_session: string;
  last_session: string;
  horizons: string[];
}

export const getInstruments = () =>
  get<{ returned: number; instruments: InstrumentRow[]; note: string }>(
    "/instruments",
  );

export interface HorizonPanel {
  instrument_id: number;
  horizons: Record<
    string,
    {
      session_date: string;
      timeframe: string;
      direction: string;
      total: number;
      components: Record<string, number>;
    }[]
  >;
  absent: Record<string, string>;
  note: string;
  unrecorded_horizon_rows: number;
  /** Set only when the record predates migration 0020. */
  horizons_not_recorded: string | null;
}

export const getPanel = (id: number | string) =>
  get<HorizonPanel>(`/instruments/${id}/panel`);
