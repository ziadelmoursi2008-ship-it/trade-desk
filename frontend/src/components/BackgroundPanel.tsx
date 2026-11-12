import { useEffect, useRef, useState } from 'react';
import { apiGet, apiPost, timeLabel } from '../api';
import type { BackgroundSettings, BackgroundStatus } from '../types';
import { backgroundStateLabel } from '../backgroundStatus';

const stocks = ['NVDA','TSLA','AMD','AAPL','MSFT','AMZN','GOOGL','META','PLTR','COIN'];

export function BackgroundPanel() {
  const [data,setData] = useState<BackgroundStatus | null>(null);
  const [draft,setDraft] = useState<BackgroundSettings | null>(null);
  const [dirty,setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [saving,setSaving] = useState(false);
  const [error,setError] = useState('');
  const [offline,setOffline] = useState(false);
  const savingRef = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    async function load() {
      if (inFlight || savingRef.current) return;
      inFlight = true;
      try {
        const result = await apiGet<BackgroundStatus>('/api/background',controller.signal,15000);
        if (!controller.signal.aborted) {
          setData(result); setOffline(false);
          if (!dirtyRef.current) setDraft(result.settings);
        }
      } catch {
        if (!controller.signal.aborted) setOffline(true);
      } finally { inFlight = false; }
    }
    void load();
    const timer = setInterval(() => void load(),10000);
    return () => {controller.abort();clearInterval(timer);};
  },[]);

  function edit(values: Partial<BackgroundSettings>) {
    setDraft(current => current ? {...current,...values} : current);
    setDirty(true); dirtyRef.current = true;
  }
  async function save(enabled: boolean) {
    if (!draft || saving) return;
    setSaving(true); savingRef.current = true; setError('');
    try {
      const {revision: _revision,...body} = {...draft,enabled};
      const settings = await apiPost<BackgroundSettings>('/api/background/settings',body,undefined,15000);
      setDraft(settings); setData(current=>current ? {...current,settings} : current);
      setDirty(false); dirtyRef.current=false; setOffline(false);
    } catch(reason) { setError(reason instanceof Error ? reason.message : 'Einstellungen konnten nicht gespeichert werden.'); }
    finally { setSaving(false);savingRef.current=false; }
  }
  const enabled = !!data?.settings.enabled;
  const state = backgroundStateLabel(data,offline);
  const estimate = draft ? draft.symbols.length*draft.timeframes.reduce((sum,tf)=>sum+(tf==='5m'?12:4),0) : 0;
  return <section aria-label="Dauerbetrieb" className="mb-4 rounded-xl border border-cyan-500/30 bg-cyan-500/5 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-bold text-cyan-200">Dauerbetrieb · ohne geöffneten Browser</h2>
        <p role="status" className={'mt-1 text-xs '+(offline?'text-amber-200':'text-slate-300')}>{state}</p></div>
      <button role="switch" aria-checked={enabled} disabled={!draft || saving || offline}
        onClick={()=>void save(!enabled)} className="rounded-lg border border-cyan-400/50 px-3 py-2 text-xs font-bold text-cyan-200 disabled:opacity-40">
        {saving?'Speichere…':enabled?'Dauerbetrieb ausschalten':'Dauerbetrieb einschalten'}
      </button>
    </div>
    <p className="mt-2 text-[11px] text-slate-400">Der Backend-Prozess beobachtet Kurse und erstellt neue Analysen je abgeschlossener Kerze. Backtest-Berichte werden regelmäßig aktualisiert. PC und Backend müssen eingeschaltet bleiben; im Energiesparmodus läuft keine Beobachtung.</p>
    <p className="mt-1 text-[11px] text-slate-400">Die Hintergrund-Watchlist ist unabhängig von der geöffneten Aktie. Der lokale Quant-Filter gilt für beide Profile. Für den Start nach der Windows-Anmeldung liegt „Autostart-einrichten.cmd“ im Projektordner.</p>
    {draft?.gemini_enabled && <p className="mt-2 text-[11px] text-amber-200">Einschalten erlaubt echte Gemini-Anfragen: für diese Auswahl regulär etwa {estimate} Analysen pro Handelsstunde, zuzüglich möglicher Reparaturen. Grenze: {draft.api_hour_limit} Hintergrundversuche/Stunde und {draft.api_day_limit}/Tag UTC. Manuelle Analysen zählen zusätzlich.</p>}
    {data && <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-slate-400">
      <span>Hintergrund-API-Versuche: {data.api_usage.hour}/{data.settings.api_hour_limit} letzte Stunde · {data.api_usage.day}/{data.settings.api_day_limit} heute UTC</span>
      <span>Gespeicherte Kurskerzen: {data.price_bars.toLocaleString('de-AT')}</span>
      <span>Letzte Analyse: {timeLabel((data.runtime.last_analysis_at || 0)*1000)}</span>
      {enabled && data.runtime.current && <span>{data.runtime.current}</span>}
    </div>}
    {enabled && data?.runtime.last_error && <p className="mt-2 text-[11px] text-amber-200">{data.runtime.last_error}</p>}
    {enabled && data?.settings.gemini_enabled && data.runtime.gemini_message && <p className="mt-2 text-[11px] text-amber-200">{data.runtime.gemini_message}</p>}
    {draft && <details open className="mt-3 text-xs text-slate-300">
      <summary className="cursor-pointer text-cyan-200">Aktien, Profile und API-Limits einstellen</summary>
      <div className="mt-3 flex flex-wrap gap-3">{stocks.map(symbol=><label key={symbol} className="flex items-center gap-1.5">
        <input type="checkbox" disabled={saving} checked={draft.symbols.includes(symbol)} onChange={event=>edit({symbols:event.target.checked?[...draft.symbols,symbol]:draft.symbols.filter(v=>v!==symbol)})}/>{symbol}</label>)}</div>
      <div className="mt-3 flex flex-wrap gap-4">{['5m','15m'].map(tf=><label key={tf} className="flex items-center gap-1.5">
        <input type="checkbox" disabled={saving} checked={draft.timeframes.includes(tf)} onChange={event=>edit({timeframes:event.target.checked?[...draft.timeframes,tf]:draft.timeframes.filter(v=>v!==tf)})}/>{tf}</label>)}
        <label className="flex items-center gap-1.5"><input type="checkbox" disabled={saving} checked={draft.python_enabled} onChange={e=>edit({python_enabled:e.target.checked})}/>Python</label>
        <label className="flex items-center gap-1.5"><input type="checkbox" disabled={saving} checked={draft.gemini_enabled} onChange={e=>edit({gemini_enabled:e.target.checked})}/>Gemini + LightGBM automatisch</label>
      </div>
      <div className="mt-3 flex flex-wrap gap-4">
        <label>API-Versuche / Stunde<input aria-label="API-Limit pro Stunde" type="number" min={1} max={500} disabled={saving} value={draft.api_hour_limit} onChange={e=>edit({api_hour_limit:Number(e.target.value)})} className="mt-1 block w-28 rounded border border-slate-700 bg-slate-900 p-2"/></label>
        <label>API-Versuche / Tag UTC<input aria-label="API-Limit pro Tag" type="number" min={1} max={5000} disabled={saving} value={draft.api_day_limit} onChange={e=>edit({api_day_limit:Number(e.target.value)})} className="mt-1 block w-28 rounded border border-slate-700 bg-slate-900 p-2"/></label>
        <label>Backtest-Berichte alle<select aria-label="Abstand automatischer Backtests" disabled={saving} value={draft.report_interval_minutes} onChange={e=>edit({report_interval_minutes:Number(e.target.value)})} className="mt-1 block rounded border border-slate-700 bg-slate-900 p-2">
          {[30,60,120,360,1440].map(minutes=><option key={minutes} value={minutes}>{minutes<60?minutes+' Minuten':minutes/60+' Stunden'}</option>)}</select></label>
      </div>
      <p className="mt-3 text-[11px] text-slate-400">{draft.gemini_enabled?<>Alle Kerzen dieser Auswahl entsprächen etwa {estimate} Gemini-Analysen pro Handelsstunde. Dein Budget bleibt bei {draft.api_hour_limit} Versuchen/Stunde. Der Dienst verteilt die Aufrufe zeitlich und berücksichtigt die Aktien abwechselnd; starke lokale Richtungswerte entscheiden bei Gleichstand. Reparaturen zählen ebenfalls gegen das Limit.</>:'Gemini pausiert. Mit der Auswahl „Gemini + LightGBM automatisch“ und „Einstellungen speichern“ kannst du es für den Dauerbetrieb aktivieren. Der lokale Filter funktioniert in beiden Profilen.'}</p>
      <p className="mt-1 text-[10px] text-slate-400">Die Grenzen zählen Aufrufversuche, keinen Geldbetrag. Manuelle Website-Analysen kommen zusätzlich hinzu. Nachträglich verpasste KI-Entscheidungen werden nicht erfunden.</p>
      <button disabled={!dirty || saving} onClick={()=>void save(enabled)} className="mt-3 rounded border border-cyan-400/40 px-3 py-2 text-cyan-200 disabled:opacity-40">Einstellungen speichern</button>
    </details>}
    {error && <p role="alert" className="mt-2 text-xs text-amber-200">{error}</p>}
  </section>;
}
