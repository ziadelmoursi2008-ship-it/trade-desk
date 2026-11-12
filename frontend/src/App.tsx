import { useEffect, useRef, useState } from 'react';
import { AlertCircle, BarChart3, CandlestickChart, FileText, RefreshCw, Search, Settings2, Target, X } from 'lucide-react';
import { TradingChart } from './components/TradingChart';
import { TradingViewWidget } from './components/TradingViewWidget';
import { TechnicalPanel } from './components/TechnicalPanel';
import { OptionsContextPanel } from './components/OptionsContextPanel';
import { DecisionPanel } from './components/DecisionPanel';
import { TechnicalForecastPanel } from './components/TechnicalForecastPanel';
import { NewsPanel } from './components/NewsPanel';
import { BacktestComparisonPanel } from './components/BacktestComparisonPanel';
import { ScannerPanel } from './components/ScannerPanel';
import { AnalysisModeSwitch } from './components/AnalysisModeSwitch';
import { GeminiStatus } from './components/GeminiStatus';
import { SettingsDialog } from './components/SettingsDialog';
import { WorkspaceStatus } from './components/WorkspaceStatus';
import { apiGet, apiPost, IS_DEMO, STATUS_URL, normalizeSymbol, price, saveAnalysisMode, TIMEFRAMES, timeLabel, WS_BASE } from './api';
import type { AnalysisMode, AnalysisResponse, KlineItem, KlinesResponse, MarketMetadata } from './types';

interface SearchResult { symbol: string; name: string; exchange: string; type: string }
const POPULAR = ['NVDA', 'TSLA', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'META', 'PLTR', 'SPY', 'XAUUSD', 'BTC-USD', 'ETH-USD'];

export default function App() {
  const [ticker, setTicker] = useState('NVDA');
  const [timeframe, setTimeframe] = useState('15m');
  const [engine, setEngine] = useState<'ai' | 'tv'>('ai');
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode | null>(null);
  const [switchingMode, setSwitchingMode] = useState(false);
  const [view,setView] = useState<'market'|'scanner'|'backtests'|'details'>('market');
  const [settingsOpen,setSettingsOpen] = useState(false);
  const [settingsTab,setSettingsTab] = useState<'models'|'background'>('models');
  const [optionsBusy,setOptionsBusy] = useState(false);
  const [klines, setKlines] = useState<KlineItem[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [market, setMarket] = useState<MarketMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [connection, setConnection] = useState('verbinde…');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<{ mode: AnalysisMode }>('/api/analysis-mode', controller.signal)
      .then(result => { if (!controller.signal.aborted) setAnalysisMode(result.mode); })
      .catch(reason => { if (!controller.signal.aborted) { setError(String(reason)); setLoading(false); } });
    return () => controller.abort();
  }, []);

  async function chooseAnalysisMode(next: AnalysisMode) {
    if (switchingMode) return;
    setSwitchingMode(true);
    setAnalysisMode(null); // abort old requests and scanner before saving the explicit selection
    setAnalysis(null);
    setError(null);
    setLoading(true);
    try {
      const result = await saveAnalysisMode(next);
      setAnalysisMode(result.mode);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Moduswechsel fehlgeschlagen.');
      try {
        const current = await apiGet<{ mode: AnalysisMode }>('/api/analysis-mode');
        setAnalysisMode(current.mode);
      } catch { setLoading(false); }
    } finally { setSwitchingMode(false); }
  }

  useEffect(() => {
    if (analysisMode == null) return;
    const controller = new AbortController();
    let inFlight = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    async function load() {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      const params = '?ticker=' + encodeURIComponent(ticker) + '&timeframe=' + timeframe;
      const [bars, details] = await Promise.allSettled([
        apiGet<KlinesResponse>('/api/klines' + params, controller.signal),
        apiGet<AnalysisResponse>('/api/analysis' + params + '&mode=' + analysisMode, controller.signal, 180000),
      ]);
      if (controller.signal.aborted) return;
      const messages: string[] = [];
      if (bars.status === 'fulfilled') {
        setKlines(bars.value.klines);
        setMarket(bars.value.market);
      } else {
        setKlines([]);
        setMarket(null);
        messages.push(bars.reason instanceof Error ? bars.reason.message : 'Keine Kursdaten.');
      }
      if (details.status === 'fulfilled') {
        setAnalysis(details.value);
      } else {
        setAnalysis(previous => previous?.ticker === ticker && previous.timeframe === timeframe
          ? { ...previous, decision: { ...previous.decision, verification_status: 'unavailable' },
              forecast: previous.forecast ? {...previous.forecast,status:'UNAVAILABLE',trade_eligible:false,
                summary:'Aktuelle Analyse nicht verfügbar; vorheriger Ausblick wird nicht als aktuelle Fortsetzung angezeigt.'} : undefined } : null);
        messages.push(details.reason instanceof Error ? details.reason.message : 'Analyse nicht verfügbar.');
      }
      setError(messages.length ? [...new Set(messages)].join(' ') : null);
      setLoading(false);
      inFlight = false;
    }
    void load();
    const timer = setInterval(() => void load(), 60000);

    function connect() {
      if (controller.signal.aborted) return;
      socket = new WebSocket(WS_BASE + '/ws/quotes/' + encodeURIComponent(ticker) + '?timeframe=' + timeframe + '&mode=' + analysisMode);
      socket.onopen = () => { if (!controller.signal.aborted) { setConnection('Feed verbunden'); failures = 0; } };
      socket.onmessage = event => {
        if (controller.signal.aborted) return;
        try {
          const data = JSON.parse(event.data);
          if (data.ticker !== ticker || data.timeframe !== timeframe || data.analysis_mode !== analysisMode) return;
          if (data.error) {
            setConnection('Feed unterbrochen');
            setAnalysis(previous => previous?.ticker === ticker && previous.timeframe === timeframe && previous.forecast
              ? {...previous,forecast:{...previous.forecast,status:'UNAVAILABLE',trade_eligible:false,
                summary:'Kursfeed unterbrochen; kein aktueller technischer Ausblick verfügbar.'}} : previous);
            return;
          }
          if (Array.isArray(data.klines)) {
            setKlines(previous => {
              // Wait for initial history; never replace a full chart with two quote bars.
              if (previous.length === 0) return previous;
              const byTime = new Map(previous.map(bar => [bar.timestamp, bar]));
              for (const bar of data.klines as KlineItem[]) byTime.set(bar.timestamp, bar);
              return [...byTime.values()].sort((a, b) => a.timestamp - b.timestamp).slice(-5000);
            });
          }
          if (data.market) setMarket(data.market);
          if (data.decision || data.forecast) setAnalysis(previous => previous?.ticker === ticker && previous.timeframe === timeframe
            ? { ...previous, decision: data.decision ? { ...data.decision, history: previous.decision.history } : previous.decision,
                forecast: data.forecast ?? previous.forecast, forecast_history: data.forecast_history ?? previous.forecast_history,
                lightgbm: data.lightgbm ?? previous.lightgbm } : previous);
        } catch { setConnection('Ungültige Feed-Antwort'); }
      };
      socket.onerror = () => { if (!controller.signal.aborted) setConnection('Feed unterbrochen'); };
      socket.onclose = () => {
        if (!controller.signal.aborted) {
          setConnection('verbinde erneut…');
          retry = setTimeout(connect, Math.min(30000, 2000 * 2 ** failures++));
        }
      };
    }
    if (IS_DEMO) setConnection('Demo · kein Live-Feed');
    else connect();
    return () => {
      controller.abort();
      clearInterval(timer);
      clearTimeout(retry);
      socket?.close();
    };
  }, [ticker, timeframe, refresh, analysisMode]);

  useEffect(() => {
    const controller = new AbortController();
    const text = query.trim();
    if (!text) return () => controller.abort();
    const timer = setTimeout(async () => {
      try {
        const data = await apiGet<SearchResult[]>('/api/search?q=' + encodeURIComponent(text), controller.signal);
        if (!controller.signal.aborted) { setResults(data); setSearchOpen(true); }
      } catch { /* Manual symbol entry remains available. */ }
    }, 250);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) setSearchOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  function select(symbol: string, selectedTimeframe = timeframe) {
    if (normalizeSymbol(symbol) !== ticker || selectedTimeframe !== timeframe) {
      setKlines([]); setAnalysis(null); setMarket(null);
    }
    setLoading(true);
    setError(null);
    setConnection('verbinde…');
    if (normalizeSymbol(symbol) === ticker && selectedTimeframe === timeframe) setRefresh(value => value + 1);
    setTicker(normalizeSymbol(symbol));
    setTimeframe(selectedTimeframe);
    setQuery('');
    setResults([]);
    setSearchOpen(false);
  }

  async function refreshOptions() {
    if (optionsBusy) return;
    const selectedTicker=ticker;
    setOptionsBusy(true); setError(null);
    try {
      const value=await apiPost<NonNullable<AnalysisResponse['options_context']>>('/api/options/refresh?ticker='+encodeURIComponent(ticker),{});
      setAnalysis(previous=>previous?.ticker===selectedTicker?{...previous,options_context:value}:previous);
    } catch(reason) { setError(reason instanceof Error?reason.message:'Optionsdaten nicht verfügbar.'); }
    finally{setOptionsBusy(false);}
  }
  const currentAnalysis = analysis?.ticker === ticker && analysis?.timeframe === timeframe && analysis?.analysis_mode === analysisMode ? analysis : null;
  const lastPrice = klines.at(-1)?.close;
  const status = market?.data_status === 'market_closed' ? 'Markt geschlossen' : market?.is_stale ? 'Kurse veraltet' : 'Kursfeed verbunden';
  const tabs = [ ['market','Markt',CandlestickChart], ['scanner','Scanner',Target], ['backtests','Backtests',BarChart3], ['details','Details',FileText] ] as const;
  return (
    <main className="dashboard min-h-screen text-slate-100">
      <div className="desk-topline"><a href="#" onClick={event=>{event.preventDefault();setView('market');}} className="desk-brand"><span className="brand-mark"><CandlestickChart size={18}/></span>TRADE<span className="font-normal text-slate-500">/</span>DESK</a>
        <div className="flex items-center gap-3"><a className="desk-button" href={STATUS_URL}>Systemstatus</a><button type="button" onClick={()=>{setSettingsTab('models');setSettingsOpen(true);}} className="desk-button" aria-label="Einstellungen öffnen"><Settings2 size={15}/><span>Einstellungen</span></button></div>
      </div>
      <div className="desk-workspace">
        {IS_DEMO&&<div className="demo-notice"><strong>Portfolio-Demo</strong><span>Synthetische Beispieldaten · keine Live-Kurse oder echten KI-Aufrufe</span></div>}
        <header className="desk-header">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">MARKTÜBERSICHT</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">Marktanalyse</h1></div><AnalysisModeSwitch mode={analysisMode} busy={switchingMode} onSelect={chooseAnalysisMode} compact/></div>
          <div className="instrument-toolbar">
            <div ref={searchRef} className="relative min-w-52 flex-1 md:max-w-80"><form onSubmit={event=>{event.preventDefault();if(query.trim())select(query);}} className="desk-search"><Search size={15}/><input aria-label="Instrument suchen" value={query} onChange={e=>{setQuery(e.target.value);setResults([]);setSearchOpen(false);}} onFocus={()=>setSearchOpen(true)} placeholder="Aktie oder Symbol suchen…"/>{query&&<button type="button" aria-label="Suche leeren" onClick={()=>{setQuery('');setSearchOpen(false);}}><X size={14}/></button>}</form>
              {searchOpen&&results.length>0&&<div className="search-results">{results.map(item=><button key={item.symbol} type="button" onClick={()=>select(item.symbol)}><strong>{item.symbol}</strong><span>{item.name}</span><small>{item.exchange}</small></button>)}</div>}
            </div>
            <div className="instrument-price"><strong>{ticker}</strong><span>{price(lastPrice,market?.currency)}</span></div>
            <label className="ml-auto flex items-center gap-2 text-xs text-slate-500">Timeframe<select className="desk-select" aria-label="Chart Timeframe" value={timeframe} onChange={e=>select(ticker,e.target.value)}>{TIMEFRAMES.map(tf=><option key={tf}>{tf}</option>)}</select></label>
            <button type="button" aria-label="Analyse aktualisieren" title="Nur die aktuelle Aktie analysieren" className="desk-icon-button" disabled={loading} onClick={()=>select(ticker,timeframe)}><RefreshCw size={16} className={loading?'animate-spin':''}/></button>
          </div>
          <div className="watchlist-strip" aria-label="Schnellauswahl">{POPULAR.map(symbol=><button type="button" key={symbol} aria-pressed={ticker===symbol} onClick={()=>select(symbol)}>{symbol==='XAUUSD'?'Gold':symbol}</button>)}</div>
        </header>
        <div className="desk-navigation"><nav aria-label="Arbeitsbereiche">{tabs.map(([key,label,Icon])=><button type="button" key={key} aria-current={view===key?'page':undefined} onClick={()=>setView(key)}><Icon size={15}/>{label}</button>)}</nav><WorkspaceStatus prediction={currentAnalysis?.lightgbm} onSettings={tab=>{setSettingsTab(tab);setSettingsOpen(true);}}/></div>
        {error&&<div role="alert" className="desk-alert"><AlertCircle size={16}/>{error}</div>}
        <div hidden={view!=='market'}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500"><p><span className={'status-dot '+(market&&!market.is_stale&&!IS_DEMO?'status-dot-green':'')}/>{IS_DEMO?'Beispieldaten':market?status:'Daten werden geladen'} · {connection}</p><p>Letzte Kerze {timeLabel(market?.last_bar_at)} · Analyse {timeLabel(currentAnalysis?.analyzed_at)}</p></div>
          <div className="market-grid">
            <div className="min-w-0"><div className="mb-3 flex items-center gap-2"><div className="segmented" aria-label="Chartanbieter">{(IS_DEMO?['ai'] as const:['ai','tv'] as const).map(value=><button type="button" key={value} aria-pressed={engine===value} onClick={()=>setEngine(value)}>{value==='ai'?'Analysechart':'TradingView'}</button>)}</div>{market?.proxy_note&&<span className="ml-auto text-[10px] text-amber-200">{market.proxy_note}</span>}</div>
              {engine==='ai'?<TradingChart key={ticker+':'+timeframe} klines={klines} decision={analysisMode==='python'?undefined:currentAnalysis?.decision} forecast={analysisMode==='python'?currentAnalysis?.forecast:undefined} ticker={ticker} timeframe={timeframe} currency={market?.currency} news={currentAnalysis?.news||[]} markers={analysisMode==='python'?[]:currentAnalysis?.backtest?.markers||[]} analysisBarAt={currentAnalysis?.analysis_bar_at}/>:<TradingViewWidget ticker={ticker} timeframe={timeframe}/>}
            </div>
            <aside className="min-w-0 space-y-4"><div className="mb-3 flex h-8 items-center justify-between"><h2 className="text-xs font-medium text-slate-400">Aktuelle Einschätzung</h2><span className="desk-tag">{analysisMode==='python'?'Python':'Gemini'}</span></div>{analysisMode==='python'?<TechnicalForecastPanel forecast={currentAnalysis?.forecast} history={currentAnalysis?.forecast_history} currency={market?.currency}/>:<DecisionPanel decision={currentAnalysis?.decision} currency={market?.currency} currentPrice={lastPrice}/>}
              {currentAnalysis?.lightgbm&&<div className="quant-note"><span className="eyebrow">QUANT · LIGHTGBM</span><p className="mt-2 text-xs leading-relaxed">{currentAnalysis.lightgbm.filter_action==='BLOCKED'?'Dieser neue Einstieg ist wegen deutlicher modellierter Gegenrichtung gesperrt.':currentAnalysis.lightgbm.reason}</p><button type="button" className="mt-3 text-xs text-emerald-200" onClick={()=>{setSettingsTab('models');setSettingsOpen(true);}}>Modelle & gespeicherte Daten →</button></div>}
            </aside>
          </div>
        </div>
        {view==='scanner'&&<div className="workspace-page"><div className="page-title"><p className="eyebrow">CHANCEN ENTDECKEN</p><h2>Markt-Scanner</h2><p>Vergleiche Märkte mit gezielten Filtern. Ein Scan startet erst auf deinen Klick.</p></div>{analysisMode&&<ScannerPanel mode={analysisMode} onSelect={(symbol,tf)=>{setEngine('ai');setView('market');select(symbol,tf);}}/>}</div>}
        {view==='backtests'&&<div className="workspace-page"><div className="page-title"><p className="eyebrow">ERGEBNISSE PRÜFEN</p><h2>Backtests · {ticker} {timeframe}</h2><p>Gespeicherte Auswertungen und tatsächlich beobachtete Gemini-Entscheidungen.</p></div><BacktestComparisonPanel key={ticker+':'+timeframe} ticker={ticker} timeframe={timeframe} refreshKey={currentAnalysis?.analyzed_at}/></div>}
        {view==='details'&&<div className="workspace-page"><div className="page-title"><p className="eyebrow">HINTER DER ANALYSE</p><h2>Indikatoren & Kontext</h2><p>Alle technischen Werte, Nachrichten und Optionsdaten an einem Ort.</p></div><div className="grid gap-5 xl:grid-cols-2"><TechnicalPanel technical={currentAnalysis?.technical} currency={market?.currency}/><div className="space-y-5">{analysisMode==='gemini'?<><GeminiStatus analysis={currentAnalysis} busy={loading}/><NewsPanel news={currentAnalysis?.news||[]} hardData={currentAnalysis?.hard_data} macroAnalysis={currentAnalysis?.macro_analysis??undefined} pipeline={currentAnalysis?.news_pipeline} policy={currentAnalysis?.decision_policy}/><div className="flex items-center justify-between"><h3 className="text-sm font-medium">Optionsrisiko</h3><button type="button" className="desk-button" disabled={optionsBusy} onClick={()=>void refreshOptions()}>{optionsBusy?'Lädt…':'Optionsdaten aktualisieren'}</button></div><OptionsContextPanel data={currentAnalysis?.options_context}/></>:<div className="setting-card text-sm leading-relaxed text-slate-400">Der Python-Modus verwendet technische Kursdaten und den lokalen Quant-Filter. Nachrichten, Wirtschaftsdaten und Gemini bleiben ausgeschaltet.</div>}</div></div></div>}
        <footer className="desk-footer"><span>Analysewerkzeug · keine automatische Orderausführung</span><span>{IS_DEMO?'React · TypeScript · Python':'Quant lokal · Modelle dauerhaft gespeichert'}</span></footer>
      </div>
      {settingsOpen&&<SettingsDialog initialTab={settingsTab} ticker={ticker} timeframe={timeframe} onClose={()=>setSettingsOpen(false)} onEnabled={enabled=>setAnalysis(previous=>previous?{...previous,lightgbm:{status:enabled?'WAITING':'DISABLED',direction:'NEUTRAL',usable:false,model_id:null,reason:enabled?'Quant wird mit der nächsten regulären Analyse geprüft.':'Quant ist ausgeschaltet.'}}:previous)} onSelect={(symbol,tf)=>{setSettingsOpen(false);setView('market');select(symbol,tf);}}/>}
    </main>
  );
}
