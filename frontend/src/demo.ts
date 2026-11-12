import rawData from './demo-data.json';
import type { AnalysisMode, AnalysisResponse, BackgroundStatus, KlineItem, MarketMetadata, TechnicalData, TechnicalForecast, TradeDecision } from './types';

// All prices and scenarios are synthetic fixtures. No requests leave the browser.
const data = rawData as unknown as Record<string, {instrument: {symbol:string; name:string; exchange:string; type:string}; klines:KlineItem[]; technical:TechnicalData}>;
const end = Date.parse('2026-11-11T18:00:00Z');
const intervals: Record<string,number> = {'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1h':3600000,'4h':14400000,'1D':86400000};
let mode: AnalysisMode = 'python';

function fixture(ticker: string) {
  if (!data[ticker]) throw new Error('Dieses Symbol ist in der Demo nicht enthalten. Wähle ein Instrument aus der Schnellauswahl.');
  return data[ticker];
}

function bars(ticker: string, timeframe: string): KlineItem[] {
  const source = fixture(ticker).klines;
  const step = intervals[timeframe] ?? intervals['15m'];
  return source.map((bar,index)=>({...bar,timestamp:end-(source.length-1-index)*step}));
}

function market(ticker: string): MarketMetadata {
  return {ticker,name:fixture(ticker).instrument.name,source_ticker:ticker,currency:'USD',exchange_timezone:'UTC',
    data_source:'Synthetische Beispieldaten',is_realtime:false,market_open:false,is_stale:false,
    data_status:'market_closed',last_bar_at:end,fetched_at:end,proxy_note:null};
}

function analysis(ticker: string, timeframe: string, selected: AnalysisMode): AnalysisResponse {
  const source = fixture(ticker);
  const quote = source.klines.at(-1)!.close;
  const score = source.technical.indicator_groups.trend_structure.score;
  const up = score >= 0;
  const sign = up ? 1 : -1;
  const target = quote * (1 + sign * .018);
  const stop = quote * (1 - sign * .008);
  const strength = Math.min(94, Math.round(76 + Math.abs(score)*.16));
  const directionScore = Math.round((Math.max(-100, Math.min(100, score)) + 100) / 2);
  const reasons = ['Illustratives Szenario auf synthetischen Beispieldaten.', 'Die Indikatoren wurden mit der Python-Engine des Projekts berechnet.'];
  const decision: TradeDecision = {ticker,timeframe,signal_type:up?'BUY':'SELL',score:directionScore,strength,
    setup_quality:'B',current_price:quote,entry_price:quote,entry_zone_min:quote*.999,entry_zone_max:quote*1.001,
    stop_loss:stop,take_profit_1:target,take_profit_2:null,risk_reward_ratio:2.25,potential_profit_pct:1.8,risk_pct:.8,
    reasons,strategy_version:'portfolio-demo',as_of:end,decision_source:selected==='python'?'python':'demo',
    validation_status:'demo',verification_status:'demo',group_comparison:source.technical.indicator_groups.comparison,
    level_basis:['Illustrative Demo-Kurszonen'],lifecycle_status:'VORBEREITET',created_at:end,updated_at:end};
  const forecast: TechnicalForecast = {ticker,timeframe,strategy_version:'portfolio-demo',analysis_kind:'CURRENT_PROJECTION',
    status:'AVAILABLE',direction:up?'UP':'DOWN',reference_price:quote,target_price:target,secondary_target_price:null,
    target_zone_min:target*.999,target_zone_max:target*1.001,target_basis:'Illustrative Demo-Kurszone',
    invalidation_price:stop,invalidation_basis:'Illustrative Demo-Risikogrenze',direction_score:score,
    indicator_strength:strength,distance_pct:1.8,risk_reward_ratio:2.25,trade_eligible:false,min_reward_risk:1,
    summary:'Beispielszenario zur Vorführung der Oberfläche. Kein aktueller Marktausblick.',
    trade_reason:'Demo · keine Handelsfreigabe',reasons,analysis_bar_at:end,observed_at:end,generated_at:end};
  return {ticker,timeframe,analysis_mode:selected,market:market(ticker),technical:source.technical,decision,forecast,
    forecast_history:[],news:[],hard_data:{items:[],snapshot_id:'demo',fetched_at:null,status:'disabled',sources:[]},
    macro_analysis:null,news_pipeline:{normal_sources:[],hard_sources:[],normal_candidates:0,normal_feed_items:0,
      normal_locally_matched:0,normal_relevant:0,selection_limit:0,ai_batching:'disabled',ai_search_enabled:false,
      context_id:'demo',cache_hit:true,feed_status:'disabled',ai_status:'disabled'},
    ai_analysis_method:'demo',analyzed_at:end,analysis_bar_at:end,
    lightgbm:{status:'DISABLED',direction:'NEUTRAL',usable:false,model_id:null,reason:'Portfolio-Demo · Modelltraining steht in der lokalen Vollversion zur Verfügung.'}};
}

export async function demoRequest<T>(path: string, body?: unknown): Promise<T> {
  const url = new URL(path,'https://demo.invalid');
  const p = url.searchParams;
  const ticker = p.get('ticker') || 'NVDA';
  const timeframe = p.get('timeframe') || '15m';
  const requestedMode = p.get('mode') === 'gemini' ? 'gemini' : p.get('mode') === 'python' ? 'python' : mode;
  let result: unknown;
  switch(url.pathname) {
    case '/api/analysis-mode': {
      if (body) { const next = (body as {mode:AnalysisMode}).mode; if (next==='python'||next==='gemini') mode=next; }
      result={mode,manual_only:true}; break;
    }
    case '/api/klines': result={ticker,timeframe,market:market(ticker),klines:bars(ticker,timeframe)}; break;
    case '/api/analysis': result=analysis(ticker,timeframe,requestedMode); break;
    case '/api/search': {
      const query=(p.get('q')||'').toLowerCase();
      result=Object.values(data).map(item=>item.instrument).filter(item=>(item.symbol+' '+item.name).toLowerCase().includes(query)).slice(0,10); break;
    }
    case '/api/background': result={settings:{enabled:false,symbols:Object.keys(data),timeframes:['15m'],python_enabled:true,
      gemini_enabled:false,api_hour_limit:0,api_day_limit:0,report_interval_minutes:60,revision:0},
      worker_alive:false,gemini_configured:false,price_bars:0,regular_analyses_per_hour:0,
      api_usage:{hour:0,day:0,day_basis:'UTC'},runtime:{status:'DISABLED'}} satisfies BackgroundStatus; break;
    case '/api/scan': {
      const universe = p.get('symbols')?.split(',').filter(symbol=>data[symbol]) ?? Object.keys(data);
      const minimum = Number(p.get('min_strength') || 75);
      const direction = p.get('direction') || 'ALL';
      const eligible = universe.map(symbol=>analysis(symbol,timeframe,requestedMode))
        .filter(item=>item.decision.strength>=minimum && (direction==='ALL'||item.decision.signal_type===direction))
        .sort((a,b)=>b.decision.strength-a.decision.strength);
      result={timeframe,min_strength:minimum,universe,scanned:universe.length,analyzed:universe.length,eligible:eligible.length,
        matches:eligible,best:eligible[0]||null,ranked_at:end,cached:true,ranking:'Synthetische Demo-Szenarien',message:'Keine passenden Beispielszenarien für diese Filter.',errors:[]}; break;
    }
    case '/api/backtests': {
      const slot={status:'DEMO',message:'In der Portfolio-Demo werden keine historischen Renditen oder KI-Freigaben simuliert. Backtests stehen in der lokalen Vollversion zur Verfügung.',result:null};
      result={ticker,timeframe,python:slot,gemini_quant:slot}; break;
    }
    default: throw new Error('Diese Funktion benötigt die lokal betriebene Vollversion. Die öffentliche Demo sendet keine API-Anfragen.');
  }
  return structuredClone(result) as T;
}
