import { useEffect, useRef, useState } from 'react';
import { apiGet, apiPost, IS_DEMO } from '../api';
import type { BacktestComparison, BacktestProfile } from '../types';
import { BacktestPanel } from './BacktestPanel';

export function BacktestComparisonPanel({ ticker, timeframe, refreshKey }: {ticker: string; timeframe: string; refreshKey?: number}) {
  const [data, setData] = useState<BacktestComparison | null>(null);
  const [running, setRunning] = useState<BacktestProfile | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const jobController = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    apiGet<BacktestComparison>('/api/backtests?ticker=' + encodeURIComponent(ticker) + '&timeframe=' + timeframe, controller.signal)
      .then(result => { if (!controller.signal.aborted) { setData(result); setError(''); } })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [ticker, timeframe, refreshKey]);
  useEffect(() => () => jobController.current?.abort(), [ticker, timeframe]);

  async function run(profile: BacktestProfile) {
    jobController.current?.abort();
    const controller = new AbortController();
    jobController.current = controller;
    setRunning(profile); setError(''); setNotice('');
    try {
      const started = await apiPost<{id: string}>('/api/backtests/run', {ticker, timeframe, profile}, controller.signal);
      for (;;) {
        if (controller.signal.aborted) return;
        const job = await apiGet<{status: string; message?: string}>('/api/backtests/jobs/' + started.id, controller.signal);
        if (['COMPLETED', 'FAILED', 'INTERRUPTED'].includes(job.status)) {
          if (job.status !== 'COMPLETED') throw new Error(job.message || 'Test wurde unterbrochen.');
          setNotice(job.message || 'Auswertung abgeschlossen.');
          const current = await apiGet<BacktestComparison>('/api/backtests?ticker=' + encodeURIComponent(ticker) + '&timeframe=' + timeframe, controller.signal);
          if (!controller.signal.aborted) setData(current);
          break;
        }
        await new Promise<void>(resolve => {
          const finish = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', finish); resolve(); };
          const timer = setTimeout(finish, 2000);
          controller.signal.addEventListener('abort', finish, {once:true});
        });
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Test fehlgeschlagen.');
    } finally {
      if (jobController.current === controller) setRunning(null);
    }
  }

  const current = data?.ticker === ticker && data?.timeframe === timeframe ? data : null;
  const supported = !IS_DEMO && ['5m', '15m'].includes(timeframe) && current?.python.status !== 'UNSUPPORTED';
  return <section aria-label="Zwei historische Backtests" className="space-y-3 rounded-xl border border-cyan-500/25 p-4">
    <h2 className="text-sm font-bold text-cyan-200">Historische Backtests · zwei getrennte Profile</h2>
    <p className="text-[11px] text-slate-400">Diese Knöpfe starten einmalige Auswertungen. Im eingeschalteten Dauerbetrieb werden Berichte regelmäßig erneuert. Jeder Test startet unabhängig mit 5.000 EUR; eine Aktienposition, nur Käufe, Ausstieg an TP1 oder Stop. Keine zusätzlichen Gemini-Aufrufe für den Backtest selbst.</p>
    {(['python', 'gemini_quant'] as const).map(profile => {
      const slot = current?.[profile];
      return <div key={profile} className="rounded-lg border border-slate-700 bg-slate-900/40 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-bold">{profile === 'python' ? '1 · Python-Basisstrategie' : '2 · Gemini + LightGBM'}</h3>
          <button disabled={!!running || !current || !supported} onClick={() => run(profile)} className="rounded border border-cyan-400/40 px-3 py-2 text-xs text-cyan-200 disabled:opacity-40">
            {running === profile ? 'Test läuft…' : profile === 'python' ? 'Python neu berechnen' : 'Gemini + LightGBM auswerten'}
          </button>
        </div>
        <p className={'mt-2 text-[11px] ' + (slot?.status === 'READY' ? 'text-slate-400' : 'text-amber-200')}>{slot?.message || 'Gespeicherte Tests werden geladen…'}</p>
        {profile === 'gemini_quant' && <p className="mt-1 text-[10px] text-slate-400">{slot?.contexts ?? 0} passende KI-Kontexte · {slot?.activated_setups ?? 0} aufgezeichnete Aktivierungen. Nur tatsächlich beobachtete Freigaben mit aktuellem Code; kein vollständiger KI-Test jeder alten Kerze.</p>}
        {profile === 'gemini_quant' && slot?.stream && <p className="mt-1 text-[10px] text-slate-400">Quelle: {slot.stream === 'background'?'Hintergrundaufzeichnungen':'manuell gestartete Analysen'}. Beide Aufzeichnungen werden getrennt ausgewertet.</p>}
        {slot?.status === 'READY' && slot.result && <div className="mt-3"><BacktestPanel backtest={slot.result} /></div>}
      </div>;
    })}
    <p className="text-[10px] text-slate-500">Zeiträume und Archivabdeckung beachten: Die beiden Renditen sind nur bei vergleichbarer Datenbasis sinnvoll gegenüberzustellen. Code- oder Datenänderungen machen gespeicherte Ergebnisse ungültig.</p>
    {notice && <p role="status" className="text-xs text-cyan-200">{notice}</p>}
    {error && <p role="alert" className="text-xs text-amber-200">{error}</p>}
  </section>;
}
