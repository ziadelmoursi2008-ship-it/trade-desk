export type AnalysisMode = 'gemini' | 'python';
export interface QuantitativeSettings {
  enabled: boolean; revision: number; manual_only: boolean; configured_weight_pct: number;
}
export interface QuantitativeData {
  ticker: string; timeframe: string; status: 'AVAILABLE' | 'PARTIAL' | 'STALE' | 'NOT_CALCULATED';
  direction: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | 'UNAVAILABLE'; direction_score: number | null;
  computed_at?: number; analysis_bar_at?: number; source_timeframe?: string; method_note: string; reason: string;
  delta?: number | null; delta_pct?: number | null; cvd?: number | null; cvd_anchor_at?: number | null;
  cvd_change_20?: number | null; cvd_pressure_pct?: number | null; divergence?: string;
  z_score_20?: number | null; efficiency_ratio_20?: number | null; market_regime?: string;
  realized_volatility_20_pct?: number | null; relative_strength_20_pct?: number | null; benchmark?: string | null;
  covered_bars?: number; window_bars?: number; volume_coverage_pct?: number | null; group_comparison?: string;
  volume_mismatch_bars?: number; coverage_method?: string;
  missing_volume_bars?: number; weight_quality_factor?: number; source_bars_per_main?: number;
  relative_strength_normalized?: number | null; relative_strength_context_score?: number | null;
  statistical_momentum_context_score?: number | null; price_context_scored_in_quant?: boolean;
  components?: Array<{key: string; name: string; score: number | null; direction: string; weight_pct: number}>;
  warnings?: string[];
}
export interface KlineItem {
  timestamp: number; open: number; high: number; low: number; close: number; volume: number;
}
export interface MarketMetadata {
  ticker: string; name: string; source_ticker: string; currency: string | null;
  exchange_timezone: string; data_source: string; is_realtime: boolean;
  market_open: boolean; is_stale: boolean; data_status: 'delayed' | 'market_closed' | 'stale';
  last_bar_at: number | null; fetched_at: number; proxy_note: string | null;
}
export interface TechnicalData {
  direction_partition?: string;
  latest_close: number;
  ema: Record<string, number | null>; sma: Record<string, number | null>;
  rsi: number; macd: { macd: number; signal: number; hist: number };
  stochastic_rsi: { k: number | null; d: number | null }; roc_12: number | null;
  cci_20: number | null; williams_r: number | null;
  bollinger: { mid: number; upper: number; lower: number };
  keltner: { mid: number | null; upper: number | null; lower: number | null };
  ichimoku: { conversion: number | null; base: number | null; span_a: number | null; span_b: number | null };
  supertrend: { line: number | null; direction: string };
  adx: { adx: number | null; plus_di: number | null; minus_di: number | null };
  vwap: number | null; atr: number;
  atr_percent: number; realized_volatility_20: number | null;
  volume: { current: number; sma20: number; ratio: number | null; obv: number | null; cmf_20: number | null; mfi_14: number | null };
  volume_profile: { poc: number | null; value_area_low: number | null; value_area_high: number | null; method: string };
  support: number | null; resistance: number | null; local_support: number | null; local_resistance: number | null;
  market_structure: { bias: string; swing_structure: string; event: string;
    fair_value_gaps: Array<{ type: string; lower: number; upper: number; filled: boolean }>;
    order_blocks: Array<{ type: string; lower: number; upper: number; invalidated: boolean; method: string }> };
  indicator_groups: { comparison: string; trend_structure: IndicatorGroup; momentum_participation: IndicatorGroup };
  risk_context: Record<string, unknown>;
  trend: string; momentum: string; breakout: string; bars: number; warmup_complete: boolean;
  warnings: string[]; error?: string;
}
export interface IndicatorGroup {
  name: string; direction: string; score: number; agreement: number; available_components: number;
  components: Array<{ name: string; signal: number; value: unknown; weight: number; detail?: string | null }>;
}
export interface NewsItem {
  ticker: string; headline: string; source: string; url: string;
  published_at: string | null; published_ts: number | null;
  sentiment: number; relevance: number; credibility: number | null; novelty: number;
  impact_score: number; event_type: string; summary: string;
  analysis_method: 'gemini' | 'keywords'; analyzed_at: number;
  importance_score?: number | null; importance_reason?: string; event_key?: string; effect_horizon?: string;
}
export interface HardDataItem {
  series_id: string; period: string; name: string; category: string;
  value: number; unit: string; previous_value: number | null; previous_period: string | null;
  change_value: number | null; change_unit: string | null; year_over_year_pct: number | null;
  source: string; source_url: string; preliminary: boolean; fetched_at: number; stale: boolean;
  importance_pct?: number; decision_weight_pct?: number;
}
export interface HardDataBundle {
  items: HardDataItem[]; snapshot_id: string; fetched_at: number | null;
  status: 'live' | 'cache' | 'stale' | 'disabled'; sources: string[];
}
export interface MacroAnalysis extends NewsItem {
  snapshot_id: string; data_kind: 'hard_data';
  weighted_direction_score?: number; assessed_weight_pct?: number;
  assessments?: Array<{ series_id: string; directional_effect: number; reason: string; importance_pct: number }>;
}
export interface DecisionPolicy {
  version: string; decision_weights_pct: { technical: number; hard_data: number; normal_news: number; quantitative?: number };
  macro_weights_pct: Record<string, number>;
  quantitative_policy?: {configured_weight_pct: number; applied_weight_pct: number; status: string; score: number | null; quality_factor?: number};
  timeframe?: string | null; weight_profile?: string; calibration_status?: string;
}
export interface NewsPipeline {
  normal_sources: string[]; hard_sources: string[]; normal_candidates: number;
  normal_feed_items: number; normal_locally_matched: number; normal_relevant: number;
  selection_limit: number; ai_batching: string; ai_search_enabled: boolean;
  context_id: string; cache_hit: boolean;
  evaluated_at?: number | null;
  normal_news_evidence_pct?: number | null;
  normal_evaluated?: number; normal_preliminary?: number; feed_status?: 'live' | 'partial' | 'unavailable' | 'empty' | 'disabled'; feed_checked_at?: number;
  source_statuses?: Array<{ source: string; status: 'live' | 'cached' | 'error' | 'empty'; error_code: string | null;
    message: string; checked_at: number; last_success_at: number | null; received_count: number; recent_count: number }>;
  ai_status?: 'evaluated' | 'unavailable' | 'disabled'; ai_error?: { kind: string; message: string } | null;
}
export interface TradeDecision {
  ticker: string; timeframe: string; signal_type: 'BUY' | 'SELL' | 'NEUTRAL';
  score: number; strength: number; confidence?: number; setup_quality: string; current_price: number;
  entry_price: number | null; entry_zone_min: number | null; entry_zone_max: number | null;
  stop_loss: number | null; take_profit_1: number | null; take_profit_2: number | null;
  risk_reward_ratio: number | null; potential_profit_pct: number | null; risk_pct: number | null;
  reasons: string[]; strategy_version: string; as_of: number;
  decision_source: string; validation_status: string; group_comparison: string | null; level_basis: string[];
  signal_label?: 'STRONG_BUY' | 'BUY' | 'WEAK_BUY' | 'NEUTRAL' | 'WEAK_SELL' | 'SELL' | 'STRONG_SELL';
  lifecycle_status?: 'VORBEREITET' | 'AKTIV' | 'BEENDET' | 'ABGELAUFEN' | 'NEUTRAL';
  direction?: 'BUY' | 'SELL' | null; setup_id?: string | null; hold_previous?: boolean; strength_status?: string;
  created_at?: number; activated_at?: number | null; ended_at?: number | null; updated_at?: number;
  last_verified_at?: number | null; verification_status?: string;
  opposing_bars?: number; low_confidence_bars?: number; elapsed_bars?: number; valid_for_bars?: number;
  entry_mode?: 'LIMIT' | 'STOP' | 'CONFIRMATION'; entry_condition?: string;
  entry_kind?: 'CURRENT' | 'PULLBACK' | 'BREAKOUT';
  invalidation_price?: number; invalidation_condition?: string;
  tp1_reached?: boolean; tp2_reached?: boolean; state_reason?: string;
  latest_ai_proposal?: { signal_type: string; confidence: number; validation_status: string; reason: string; analyzed_at: number; bar_at: number };
  decision_breakdown?: { block_direction_scores: Record<string, number>; weights_pct: Record<string, number>; weighted_direction_score: number; normal_news_events: number } | null;
  reported_direction_score?: number; score_weight_adjusted?: boolean; requires_entry_confirmation?: boolean;
  analysis_error?: { kind: string; message: string } | null;
  analysis_mode?: AnalysisMode;
  history?: SignalEvent[];
}
export interface SignalEvent { id: number; event_type: string; created_at: number; analysis_bar_at: number | null; decision: TradeDecision }
export interface TradeMarker { timestamp: number; type: 'BUY' | 'SELL'; price: number }
export interface BacktestResult {
  analysis_mode?: 'python' | 'gemini_quant';
  coverage_note?: string;
  currency?: string; quote_currency?: string;
  initial_capital: number; final_capital: number; total_return_pct: number; total_trades: number;
  winning_trades: number; losing_trades: number; win_rate: number; profit_factor: number | null;
  max_drawdown_pct: number; markers: TradeMarker[]; scope: string; costs: string;
  assumptions: string; position_sizing: string; news_coverage_bars: number; strategy_version: string;
  start_at?: number; end_at?: number; error?: string;
  recent_trades: Array<{ type: string; entry_price: number; exit_price: number; pnl: number;
    reason: string; time: number; entry_time: number; units: number; tp1_taken: boolean }>;
}
export type BacktestProfile = 'python' | 'gemini_quant';
export interface BackgroundSettings {
  enabled: boolean; symbols: string[]; timeframes: string[]; python_enabled: boolean; gemini_enabled: boolean;
  api_hour_limit: number; api_day_limit: number; report_interval_minutes: number; revision: number;
}
export interface BackgroundStatus {
  settings: BackgroundSettings; worker_alive: boolean; gemini_configured: boolean; price_bars: number;
  regular_analyses_per_hour: number; api_usage: {hour: number; day: number; day_basis: string};
  runtime: {status?: string; current?: string | null; heartbeat_at?: number; last_analysis_at?: number; last_error?: string | null; gemini_message?: string | null};
}
export interface BacktestSlot {
  status: string; message: string; result: BacktestResult | null;
  contexts?: number; activated_setups?: number;
  stream?: string;
}
export interface BacktestComparison {
  ticker: string; timeframe: string; python: BacktestSlot; gemini_quant: BacktestSlot;
}
export interface AnalysisResponse {
  lightgbm?: LightGBMData;
  ticker: string; timeframe: string; technical: TechnicalData; news: NewsItem[];
  hard_data: HardDataBundle; macro_analysis: MacroAnalysis | null; news_pipeline: NewsPipeline;
  decision: TradeDecision; backtest?: BacktestResult; market: MarketMetadata;
  ai_analysis_method: string; analyzed_at: number; analysis_bar_at: number | null;
  decision_policy?: DecisionPolicy; latest_ai_decision?: TradeDecision;
  analysis_mode?: AnalysisMode;
  quantitative?: QuantitativeData | null; quantitative_settings?: QuantitativeSettings;
  options_context?: OptionsContextData | null;
  forecast?: TechnicalForecast;
  forecast_history?: ForecastSnapshot[];
}

export interface LightGBMData {
  status: string; direction: string; usable: boolean; reason: string; model_id: string | null;
  filter_action?: string; probabilities?: { up: number; flat: number; down: number };
}
export interface LightGBMModel {
  id: string; created_at: number; active: boolean; qualified: boolean; target: string;
  training_samples: number; validation_samples: number; training_from: number;
  test_from: number; validated_through: number;
  metrics: { samples: number; accuracy: number; baseline_accuracy: number; log_loss: number; baseline_log_loss: number };
  calibration?: { method: string; temperature: number };
  calibration_samples?: number;
  nonoverlapping_metrics?: { samples: number; accuracy: number; confident_directional_samples: number; confident_directional_accuracy: number | null };
  release_failures?: string[];
}
export interface LightGBMStatus {
  ticker: string; timeframe: string; supported: boolean; installed: boolean; enabled: boolean;
  active_model: LightGBMModel | null; latest_model: LightGBMModel | null;
  archive: { candles: number; from: number | null; through: number | null };
  job: { id: string; status: string; message?: string } | null;
}

export interface ModelInventoryRow {
  ticker:string; name:string; timeframe:string; candles:number;
  first_bar_at:number|null; last_bar_at:number|null; last_checked_at:number|null; last_updated_at:number|null;
  trained_at:number|null; model_status:string; active_model:LightGBMModel|null; latest_model:LightGBMModel|null;
  job:{id:string;status:string;message:string}|null;
}
export interface ModelInventory {
  enabled:boolean; installed:boolean; rows:ModelInventoryRow[];
  queue:{id:string;ticker:string;timeframe:string;status:string}[];
  batch:{id:string|null;total:number;completed:number}|null; generated_at:number;
}
export interface OptionsContract {
  symbol: string; kind: 'call' | 'put'; expiration: string; strike: number;
  iv: number; bid: number; ask: number; quote_at: number | null; last_trade_at: number | null;
  open_interest: number | null; fresh_quote: boolean;
  greeks: {delta: number; gamma: number; theta: number; vega: number; rho: number; model_price: number};
}
export interface OptionsContextData {
  ticker: string; status: 'AVAILABLE' | 'REFERENCE' | 'STALE' | 'NOT_CALCULATED' | 'UNAVAILABLE' | 'UNSUPPORTED';
  reason: string; usable_for_ai: boolean; direction_weight_pct: number;
  provider?: string; feed?: string; computed_at?: number; cache_hit?: boolean;
  spot?: number; spot_at?: number | null; spot_time_basis?: string;
  model?: string; warnings: string[]; contracts: OptionsContract[];
  assumptions?: {rate: number; rate_source: string; dividend_yield: number; dividend_source: string; expiry_time: string};
}
export interface TechnicalForecast {
  ticker: string; timeframe: string; strategy_version: string; analysis_kind: 'CURRENT_PROJECTION';
  status: 'AVAILABLE' | 'UNAVAILABLE' | 'STALE' | 'UNCLEAR' | 'NO_TARGET';
  direction: 'UP' | 'DOWN' | 'UNCLEAR'; reference_price: number | null;
  target_price: number | null; secondary_target_price: number | null;
  target_zone_min: number | null; target_zone_max: number | null; target_basis: string | null;
  invalidation_price: number | null; invalidation_basis: string | null;
  direction_score: number; indicator_strength: number; distance_pct: number | null;
  risk_reward_ratio: number | null; trade_eligible: boolean; min_reward_risk: number;
  min_distance_pct?: number; nearest_zone_price?: number | null; nearest_zone_basis?: string | null;
  poi_check?: PoiCheck;
  summary: string; trade_reason: string; reasons: string[];
  analysis_bar_at: number; observed_at: number; generated_at: number;
}
export interface PoiCheck {
  status: 'NOT_CHECKED' | 'CLEAR' | 'BLOCKED' | 'INCOMPLETE';
  required_timeframes: string[]; checked_timeframes: string[]; unavailable_timeframes: string[];
  unavailable_reasons: Record<string, string>; analysis_bars: Record<string, number | null>;
  obstacles: Array<{ timeframe: string; kind: string; price: number; lower: number; upper: number;
    distance_pct: number; blocking: boolean; reason: string }>;
}
export interface ForecastSnapshot { id: string; forecast: TechnicalForecast }
export interface KlinesResponse { ticker: string; timeframe: string; klines: KlineItem[]; market: MarketMetadata }
export interface ScanResponse {
  quantitative_enabled?: boolean;
  timeframe: string; min_strength: number; universe: string[]; scanned: number; analyzed: number;
  eligible: number; matches: AnalysisResponse[]; best: AnalysisResponse | null; ranked_at: number; cached: boolean;
  ranking: string; message: string | null; errors: Array<{ ticker: string; reason: string }>;
}
