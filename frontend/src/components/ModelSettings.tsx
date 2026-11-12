import { useEffect, useRef, useState } from 'react';
import { Cpu, Database, RefreshCw, Search } from 'lucide-react';
import { apiGet, apiPost, timeLabel } from '../api';
import type { BackgroundStatus, ModelInventory, ModelInventoryRow } from '../types';

const RUNNING = new Set(['QUEUED', 'RUNNING']);
const modelLabels: Record<string,string> = { READY:'Freigegeben', STALE:'Veraltet', INCOMPATIBLE:'Neu trainieren', OBSERVATION:'Beobachtung', NOT_TRAINED:'Kein Modell' };
const jobLabels: Record<string,string> = { QUEUED:'Wartet', RUNNING:'Trainiert', READY:'Abgeschlossen', OBSERVATION:'Prüfung nicht bestanden', NEEDS_DATA:'Daten fehlen', FAILED:'Fehlgeschlagen', INTERRUPTED:'Unterbrochen', UNCHANGED:'Unverändert' };
const date = (value?: number | null) => value ? timeLabel(value) : 'Noch nicht erfasst';

export function ModelSettings({ ticker, timeframe, onEnabled, onSelect }: {
  ticker:string; timeframe:string; onEnabled:(enabled:boolean)=>void; onSelect:(ticker:string,timeframe:string)=>void;
}) {
  const [data,setData] = useState<ModelInventory | null>(null);
  const [watchlist,setWatchlist] = useState<string[]>([]);
  const [scope,setScope] = useState<'current'|'watchlist'>('current');
  const [frames,setFrames] = useState<string[]>([timeframe === '5m' ? '5m' : '15m']);
  const [search,setSearch] = useState('');
  const [frameFilter,setFrameFilter] = useState('all');
  const [statusFilter,setStatusFilter] = useState('all');
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller;
    let inFlight = false;
    async function load() {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await apiGet<ModelInventory>('/api/lightgbm/inventory',controller.signal);
        if (!controller.signal.aborted) setData(result);
      } catch(reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Datenübersicht nicht erreichbar.'); }
      finally { inFlight=false; }
    }
    void load();
    apiGet<BackgroundStatus>('/api/background',controller.signal).then(result=>{
      if (!controller.signal.aborted) setWatchlist(result.settings.symbols);
    }).catch(()=>{});
    const timer = setInterval(()=>void load(),4000);
    return ()=>{controller.abort(); clearInterval(timer);};
  },[]);

  async function train(row?: ModelInventoryRow) {
    if (busy || !abort.current) return;
    const symbols = row ? [row.ticker] : scope === 'current' ? [ticker] : watchlist;
    const timeframes = row ? [row.timeframe] : frames;
    setBusy(true); setError(''); setNotice('');
    try {
      await apiPost('/api/lightgbm/train-batch',{symbols,timeframes,refresh_data:true},abort.current.signal);
      if (abort.current.signal.aborted) return;
      setNotice(`Auftrag für ${symbols.length * timeframes.length} Modelle gespeichert. Fortschritt und Ergebnisse stehen in der Datenübersicht; die Einstellungen dürfen geschlossen werden.`);
      setData(await apiGet<ModelInventory>('/api/lightgbm/inventory',abort.current.signal));
    } catch(reason) { if (!abort.current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Auftrag konnte nicht gestartet werden.'); }
    finally { setBusy(false); }
  }
  async function toggle() {
    if (!data || busy || !abort.current) return;
    setBusy(true); setError('');
    try {
      const result = await apiPost<{enabled:boolean}>('/api/lightgbm/settings',{enabled:!data.enabled},abort.current.signal);
      if (!abort.current.signal.aborted) { setData({...data,enabled:result.enabled}); onEnabled(result.enabled); }
    } catch(reason) { if (!abort.current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Speichern fehlgeschlagen.'); }
    finally { setBusy(false); }
  }
  const queued = !!data?.queue.length;
  const selected = scope==='current' ? [ticker] : watchlist;
  const rows = data?.rows.filter(row=>(!search || (row.ticker+' '+row.name).toLowerCase().includes(search.toLowerCase()))
    && (frameFilter==='all' || row.timeframe===frameFilter) && (statusFilter==='all' || row.model_status===statusFilter)) ?? [];
  return <div className="model-settings">
    <div className="setting-card flex flex-wrap items-center justify-between gap-4">
      <div><h3 className="flex items-center gap-2 font-semibold"><Cpu size={17}/>Quant mit LightGBM</h3><p className="mt-1 text-xs text-slate-400">Ein gemeinsamer lokaler Filter für Python und Gemini. Modelle und Kursarchive bleiben gespeichert.</p></div>
      <button type="button" className={'desk-button '+(data?.enabled?'desk-button-primary':'')} role="switch" aria-checked={data?.enabled ?? false} disabled={!data || busy} onClick={()=>void toggle()}>{data?.enabled?'Quant eingeschaltet':'Quant ausgeschaltet'}</button>
    </div>
    <section className="setting-card mt-4" aria-label="Qualität der Richtungsprognosen">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">Qualität vor Signalstärke</h3><span className="desk-tag">Zeitlich getrennte Prüfung</span></div>
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <div><p className="text-2xl font-semibold tabular-nums text-emerald-200">{data?.rows.filter(row=>row.model_status==='READY').length ?? '—'}</p><p className="mt-1 text-xs text-slate-400">Aktuelle, freigegebene Modelle</p></div>
        <div><p className="text-sm font-semibold text-slate-200">Training → Kalibrierung → Test</p><p className="mt-1 text-xs leading-relaxed text-slate-400">Eigener Zeitraum für die Anpassung der Modellwerte. Der Abschlusstest bleibt davon getrennt.</p></div>
        <div><p className="text-sm font-semibold text-slate-200">Steigt · Seitwärts · Fällt</p><p className="mt-1 text-xs leading-relaxed text-slate-400">Drei folgende Kerzen nach Kostenhürde. Unsichere Modelle bleiben in Beobachtung.</p></div>
      </div>
    </section>
    <div className="setting-card mt-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">Daten & Modelle aktualisieren</h3><p className="mt-1 text-xs text-slate-400">Neue Kurse laden, unveränderte Daten wiederverwenden, bei Bedarf neu trainieren. Kein Gemini-Aufruf.</p></div><span className="desk-tag">5m / 15m</span></div>
      <div className="flex flex-wrap items-center gap-3">
        <fieldset className="segmented" aria-label="Umfang der Modellaktualisierung">
          <button type="button" aria-pressed={scope==='current'} onClick={()=>setScope('current')}>Diese Aktie · {ticker}</button>
          <button type="button" aria-pressed={scope==='watchlist'} onClick={()=>setScope('watchlist')}>Watchlist · {watchlist.length}</button>
        </fieldset>
        <fieldset className="flex gap-3 text-xs" aria-label="Timeframes trainieren">{['5m','15m'].map(tf=><label key={tf} className="flex items-center gap-2"><input type="checkbox" checked={frames.includes(tf)} onChange={e=>setFrames(e.target.checked?[...frames,tf]:frames.filter(value=>value!==tf))}/>{tf}</label>)}</fieldset>
        <button type="button" className="desk-button desk-button-primary ml-auto" disabled={!data?.installed || busy || queued || !selected.length || !frames.length} onClick={()=>void train()}><RefreshCw size={14} className={queued?'animate-spin':''}/>{queued?'Training läuft…':`${selected.length*frames.length} ${selected.length*frames.length===1?'Modell':'Modelle'} aktualisieren`}</button>
      </div>
      {data && !data.installed && <p className="mt-3 text-xs text-amber-200">LightGBM fehlt in der Python-Umgebung. Die Installationsdatei ausführen.</p>}
      {data?.batch && <div className="mt-4" role="status"><div className="mb-2 flex justify-between text-xs"><span>Warteschlange · {data.queue.find(job=>job.status==='RUNNING')?.ticker ?? 'Startet gleich'} {data.queue.find(job=>job.status==='RUNNING')?.timeframe}</span><span>{data.batch.completed} / {data.batch.total} fertig</span></div><progress className="w-full" value={data.batch.completed} max={data.batch.total}/></div>}
      {notice && <p role="status" className="mt-3 text-xs text-emerald-200">{notice}</p>}
      {error && <p role="alert" className="mt-3 text-xs text-amber-200">{error}</p>}
    </div>
    <div className="mt-7 flex flex-wrap items-end justify-between gap-3"><div><h3 className="flex items-center gap-2 font-semibold"><Database size={17}/>Gespeicherte Daten</h3><p className="mt-1 text-xs text-slate-400">Welche Aktien und Timeframes vorliegen – mit echten Daten- und Trainingszeitpunkten.</p></div><span className="desk-tag">{data?.rows.length ?? 0} Datensätze</span></div>
    <div className="my-4 flex flex-wrap gap-2"><label className="desk-search flex min-w-48 flex-1 items-center gap-2"><Search size={14}/><input aria-label="Gespeicherte Aktien filtern" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Aktie suchen…"/></label>
      <select className="desk-select" aria-label="Daten nach Timeframe filtern" value={frameFilter} onChange={e=>setFrameFilter(e.target.value)}><option value="all">Alle Timeframes</option><option>5m</option><option>15m</option></select>
      <select className="desk-select" aria-label="Nach Modellstatus filtern" value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}><option value="all">Alle Modelle</option>{Object.entries(modelLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
    </div>
    <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Aktie / Timeframe</th><th>Kursdaten</th><th>Zuletzt aktualisiert</th><th>Modell</th><th><span className="sr-only">Aktionen</span></th></tr></thead>
      <tbody>{rows.map(row=><tr key={row.ticker+row.timeframe}>
        <td><button className="font-semibold text-slate-100 hover:text-emerald-200" onClick={()=>onSelect(row.ticker,row.timeframe)}>{row.ticker} <span className="ml-1 text-xs text-slate-400">{row.timeframe}</span></button><span className="table-detail">{row.name}</span></td>
        <td><span>{row.candles.toLocaleString('de-AT')} Kerzen</span><span className="table-detail">Von {row.first_bar_at ? timeLabel(row.first_bar_at):'—'}</span><span className="table-detail">Bis {row.last_bar_at ? timeLabel(row.last_bar_at):'—'}</span></td>
        <td>{date(row.last_updated_at)}<span className="table-detail">Abruf geprüft: {date(row.last_checked_at)}</span></td>
        <td><span className={'desk-tag '+(row.model_status==='READY'?'text-emerald-200':'')}>{modelLabels[row.model_status]}</span><span className="table-detail">Letztes Training: {row.trained_at ? timeLabel(row.trained_at):'noch nie'}</span>
          {row.active_model && row.latest_model?.id!==row.active_model.id && <span className="table-detail">Aktives Modell: {timeLabel(row.active_model.created_at)}</span>}
          {row.latest_model?.metrics && <span className="table-detail">Testvorteil: {((row.latest_model.metrics.accuracy-row.latest_model.metrics.baseline_accuracy)*100).toFixed(1)} Prozentpunkte</span>}
          {row.latest_model?.calibration && <span className="table-detail">Kalibrierung: {row.latest_model.calibration_samples} separate Beispiele</span>}
          {row.latest_model?.nonoverlapping_metrics && <span className="table-detail">Ohne Fensterüberlappung: {(row.latest_model.nonoverlapping_metrics.accuracy*100).toFixed(1)} % / {row.latest_model.nonoverlapping_metrics.samples} Prognosen</span>}
          {row.latest_model?.release_failures?.map(reason=><span key={reason} className="table-detail text-amber-200">{reason}</span>)}
          {row.job && <span className="table-detail" title={row.job.message}>{jobLabels[row.job.status] ?? row.job.status}{RUNNING.has(row.job.status)?'…':''}</span>}
          {(row.latest_model || row.job?.message) && <details className="mt-2 text-[11px] text-slate-400"><summary className="cursor-pointer">Details</summary><p className="mt-2 max-w-72">{row.job?.message}</p>{row.latest_model && <p className="mt-2 max-w-72">Zeitlicher Test: {row.latest_model.metrics?.samples ?? 0} Beispiele. Richtungsklassen {((row.latest_model.metrics?.accuracy ?? 0)*100).toFixed(1)} % richtig; Vergleich {((row.latest_model.metrics?.baseline_accuracy ?? 0)*100).toFixed(1)} %. Kein Handelsrendite-Nachweis.</p>}</details>}
        </td>
        <td><button type="button" aria-label={`${row.ticker} ${row.timeframe} aktualisieren`} className="desk-icon-button" title="Nur diese Aktie und diesen Timeframe aktualisieren" disabled={!data?.installed || queued || busy} onClick={()=>void train(row)}><RefreshCw size={15}/></button></td>
      </tr>)}</tbody></table>{!rows.length && <p className="p-8 text-center text-sm text-slate-400">{data?'Keine passenden gespeicherten Daten. Wähle oben eine Aktie und starte die Aktualisierung.':'Lade gespeicherte Daten…'}</p>}</div>
    <p className="mt-4 text-[11px] leading-relaxed text-slate-500">„Kursdaten bis“ bezeichnet die letzte Kerze. „Zuletzt aktualisiert“ bezeichnet neue oder korrigierte gespeicherte Kurse. „Trainiert“ bezeichnet den letzten Modelllauf. Bei älteren Archiven ohne protokollierten Abrufzeitpunkt steht „Noch nicht erfasst“. Training läuft nur auf ausdrücklichen Auftrag und verändert den Hauptchart nicht.</p>
  </div>;
}
