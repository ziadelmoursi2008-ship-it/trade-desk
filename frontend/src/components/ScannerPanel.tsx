import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Search, Sparkles } from 'lucide-react';
import { apiGet, price, TIMEFRAMES, timeLabel } from '../api';
import type { AnalysisMode, ScanResponse, BackgroundStatus } from '../types';

export function ScannerPanel({ onSelect, mode = 'gemini' }: {
  onSelect: (ticker: string, timeframe: string) => void; mode?: AnalysisMode;
}) {
  const [timeframe, setTimeframe] = useState('15m');
  const [strength, setStrength] = useState(75);
  const [scope,setScope] = useState('all');
  const [direction,setDirection] = useState('ALL');
  const [watchlist,setWatchlist] = useState<string[]>([]);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => controllerRef.current?.abort(), []);
  useEffect(()=>{const controller=new AbortController();apiGet<BackgroundStatus>('/api/background',controller.signal).then(value=>{if(!controller.signal.aborted)setWatchlist(value.settings.symbols);}).catch(()=>{});return ()=>controller.abort();},[]);

  async function scan() {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await apiGet<ScanResponse>('/api/scan?timeframe=' + timeframe + '&min_strength=' + strength + '&mode=' + mode +
        '&direction='+direction+(scope==='watchlist'?'&symbols='+encodeURIComponent(watchlist.join(',')):''), controller.signal, 480000);
      if (!controller.signal.aborted) setResult(data);
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Scan fehlgeschlagen.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  const matches = result?.matches ?? (result?.best ? [result.best] : []);
  return (
    <section className="mb-6 rounded-xl border border-cyan-500/25 bg-gradient-to-r from-cyan-950/25 to-slate-900/80 p-4" aria-label="Opportunity Scanner">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-bold text-cyan-200"><Sparkles className="h-4 w-4" /> Marktchancen finden</h2>
          <p className="mt-1 text-xs text-emerald-200">Gespeicherte LightGBM-Modelle prüfen neue Einstiege lokal. Ein Scan trainiert keine Modelle.</p>
          <p className="mt-1 max-w-2xl text-xs text-slate-400">{mode === 'python' ? 'Python vergleicht 16 Märkte ausschließlich anhand technischer Daten; keine KI und keine Nachrichten. Nächstes technisches Kursziel ab aktuellem Kurs unter A+/A/B-Ausblicken, mindestens 1R.' : 'Vergleicht 16 Aktien, ETFs, Gold und Kryptowährungen anhand validierter Gemini-Gesamtanalysen. A+/A/B-Setups mit mindestens 1,5R.'} Alle passenden Treffer ab der gewählten Signalstärke werden angezeigt.</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-400">Märkte<select className="desk-select mt-1 block" aria-label="Scanner Märkte" disabled={loading} value={scope} onChange={e=>{setScope(e.target.value);setResult(null);}}><option value="all">Alle Märkte</option><option value="watchlist" disabled={!watchlist.length}>Meine Watchlist</option></select></label>
          <label className="text-[11px] text-slate-400">Richtung<select className="desk-select mt-1 block" aria-label="Scanner Richtung" disabled={loading} value={direction} onChange={e=>{setDirection(e.target.value);setResult(null);}}><option value="ALL">Beide</option><option value="BUY">Aufwärts / BUY</option><option value="SELL">Abwärts / SELL</option></select></label>
          <label className="text-[11px] text-slate-400">Zeitrahmen
            <select aria-label="Scanner Zeitrahmen" disabled={loading} value={timeframe} onChange={e => { setTimeframe(e.target.value); setResult(null); }} className="mt-1 block rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100">
              {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-slate-400">Min. Signalstärke
            <select aria-label="Scanner Signalstärke" disabled={loading} value={strength} onChange={e => { setStrength(Number(e.target.value)); setResult(null); }} className="mt-1 block rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100">
              {[65, 75, 85].map(value => <option key={value} value={value}>{value}/100</option>)}
            </select>
          </label>
          <button onClick={scan} disabled={loading} className="flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-xs font-bold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-wait disabled:opacity-60">
            <Search className={'h-4 w-4 ' + (loading ? 'animate-pulse' : '')} />{loading ? 'Analysiere Märkte…' : 'Markt scannen'}
          </button>
        </div>
      </div>
      <div aria-live="polite">
        {loading && <p className="mt-3 text-xs text-slate-400">{mode === 'python' ? 'Technische Indikatoren und Kurszonen werden von Python geprüft. Es werden keine KI-Anfragen gesendet.' : 'Technik, harte Daten und Nachrichten werden je Instrument gemeinsam von Gemini geprüft. Archivierte Kerzenkontexte werden wiederverwendet.'}</p>}
        {error && <p role="alert" className="mt-3 text-xs text-amber-300">{error}</p>}
        {result && (
          <div className="mt-4 border-t border-slate-700/70 pt-3">
            <p className="mb-3 text-[11px] text-slate-400">{result.analyzed}/{result.scanned} analysiert · {result.eligible} passend · {timeLabel(result.ranked_at)}{result.cached ? ' · kürzlich gescannt' : ''}{result.quantitative_enabled ? ' · inklusive Quant und verfügbarem Optionskontext' : ''}</p>
            <details className="mb-3 text-[11px] text-slate-400"><summary className="cursor-pointer">Verglichene Märkte anzeigen</summary><p className="mt-2">{result.universe.join(', ')}</p></details>
            {matches.length > 0 ? <>
              <h3 className="mb-3 text-xs font-semibold text-slate-300">{matches.length} Treffer ab {result.min_strength}/100 Signalstärke</h3>
              <ul aria-label="Passende Marktchancen" className="space-y-2">{matches.map(item=><li key={item.ticker} className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-slate-700/70 bg-slate-900/40 p-4">
                <div className="min-w-0 flex-1 basis-64">
                  <p className="text-sm font-semibold text-white">{item.ticker} <span className={'ml-2 text-xs '+(item.decision.signal_type==='BUY'?'text-emerald-400':'text-red-400')}>{mode==='python'?(item.forecast?.direction==='UP'?'Tendenz aufwärts':'Tendenz abwärts'):item.decision.signal_label||item.decision.signal_type}</span><span className="ml-2 text-[11px] font-normal text-slate-500">{result.timeframe} · Grade {item.decision.setup_quality}</span></p>
                  <p className="mt-2 text-[11px] text-slate-400">Kurs {price(item.decision.current_price,item.market.currency)} · Ziel {price(mode==='python'?item.forecast?.target_price:item.decision.take_profit_1,item.market.currency)} · {mode==='python'?'Ungültig ab':'Stop'} {price(mode==='python'?item.forecast?.invalidation_price:item.decision.stop_loss,item.market.currency)}</p>
                  {item.market.proxy_note&&<p className="mt-1 text-[11px] text-amber-300">{item.market.proxy_note}</p>}
                </div>
                <dl className="flex flex-wrap gap-5 text-xs">
                  <div><dt className="text-[10px] text-slate-500">Signalstärke</dt><dd className="mt-1 font-semibold text-slate-200">{item.decision.strength}/100</dd></div>
                  <div><dt className="text-[10px] text-slate-500">{mode==='python'?'Ziel-Abstand':'TP1-Potenzial'}</dt><dd className="mt-1 text-slate-300">{item.decision.potential_profit_pct?.toFixed(2)}%</dd></div>
                  <div><dt className="text-[10px] text-slate-500">Risiko / Chance</dt><dd className="mt-1 text-slate-300">1:{item.decision.risk_reward_ratio?.toFixed(2)}</dd></div>
                </dl>
                <button type="button" aria-label={'Chart für '+item.ticker+' öffnen'} onClick={()=>onSelect(item.ticker,result.timeframe)} className="flex items-center gap-2 rounded-lg border border-cyan-500/40 px-3 py-2 text-xs font-semibold text-cyan-300 hover:bg-cyan-500/10">Im Chart öffnen <ArrowRight className="h-4 w-4" /></button>
              </li>)}</ul>
            </> : <p className="text-xs text-amber-200">{result.message||'Keine passenden Marktchancen für diese Filter.'}</p>}
            {result.errors.length > 0 && <details className="mt-3 text-[11px] text-slate-400"><summary className="cursor-pointer">{result.errors.length} Instrumente nicht verfügbar</summary><ul className="mt-2 space-y-1">{result.errors.map(item => <li key={item.ticker}>{item.ticker}: {item.reason}</li>)}</ul></details>}
            <p className="mt-3 text-[10px] text-slate-500">Vergleich innerhalb der angezeigten Märkte. Kursziele und Signalstärke sind keine Gewinnwahrscheinlichkeit.</p>
          </div>
        )}
      </div>
    </section>
  );
}
