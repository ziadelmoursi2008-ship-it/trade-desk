import { useEffect, useRef, useState } from 'react';
import { apiGet, apiPost, timeLabel } from '../api';
import type { LightGBMData, LightGBMStatus } from '../types';

const RUNNING = ['QUEUED', 'RUNNING'];
const percent = (value?: number | null) => value == null ? '—' : (value * 100).toLocaleString('de-AT', { maximumFractionDigits: 1 }) + ' %';

export function LightGBMPanel({ ticker, timeframe, prediction, onChange }: {
  ticker: string; timeframe: string; prediction?: LightGBMData; onChange: () => void;
}) {
  const [data, setData] = useState<LightGBMStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const callback = useRef(onChange);
  const lastJob = useRef<{ id: string; status: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { callback.current = onChange; }, [onChange]);

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    let inFlight = false;
    async function load() {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await apiGet<LightGBMStatus>('/api/lightgbm?ticker=' + encodeURIComponent(ticker) + '&timeframe=' + timeframe, abort.signal);
        if (abort.signal.aborted) return;
        const previous = lastJob.current;
        lastJob.current = result.job;
        setData(result);
        setError(null);
        if (previous && result.job?.id === previous.id && RUNNING.includes(previous.status) && !RUNNING.includes(result.job.status)) callback.current();
      } catch (reason) {
        if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'Modellstatus nicht verfügbar.');
      } finally { inFlight = false; }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { abort.abort(); clearInterval(timer); };
  }, [ticker, timeframe]);

  async function train() {
    if (!data || submitting || !controller.current) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await apiPost<NonNullable<LightGBMStatus['job']>>('/api/lightgbm/train', { ticker, timeframe, refresh_data: true }, controller.current.signal);
      if (!controller.current.signal.aborted) {
        lastJob.current = job;
        setData(previous => previous ? { ...previous, job } : previous);
      }
    } catch (reason) {
      if (!controller.current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Training konnte nicht gestartet werden.');
    } finally { setSubmitting(false); }
  }

  async function toggle() {
    if (!data || saving || !controller.current) return;
    setSaving(true);
    try {
      const result = await apiPost<{ enabled: boolean }>('/api/lightgbm/settings', { enabled: !data.enabled }, controller.current.signal);
      if (!controller.current.signal.aborted) {
        setData(previous => previous ? { ...previous, enabled: result.enabled } : previous);
        callback.current();
      }
    } catch (reason) {
      if (!controller.current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Auswahl konnte nicht gespeichert werden.');
    } finally { setSaving(false); }
  }

  const model = data?.active_model;
  const latest = data?.latest_model;
  const training = submitting || !!(data?.job && RUNNING.includes(data.job.status));
  const supported = timeframe === '5m' || timeframe === '15m';
  const live = prediction?.status === 'AVAILABLE' && data?.enabled;
  const labels: Record<string, string> = { BLOCKED: 'Neuer Einstieg gesperrt', PASS: 'Richtung geprüft', EXISTING_SETUP: 'Bestehendes Setup bleibt verwaltet', INACTIVE: 'Kein Filtereinfluss' };

  return <section aria-label="LightGBM Richtungsfilter" className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-bold text-emerald-200">LightGBM · lokaler Richtungsfilter</h2>
        <p className="mt-1 text-xs text-slate-400">Eigenes Modell für {ticker} · {timeframe}. {supported ? `Ausblick: ${timeframe === '5m' ? 15 : 45} Minuten (3 Kerzen).` : 'Training für 5m und 15m verfügbar.'} Keine KI-API erforderlich.</p></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" role="switch" aria-checked={data?.enabled ?? false} disabled={!data || saving}
          onClick={() => void toggle()} className="rounded-lg border border-emerald-400/40 px-3 py-2 text-xs disabled:opacity-40">
          {saving ? 'Speichere…' : data?.enabled ? 'Filter: EIN' : 'Filter: AUS'}
        </button>
        <button type="button" disabled={!supported || !data?.installed || training} onClick={() => void train()}
          className="rounded-lg bg-emerald-300 px-3 py-2 text-xs font-bold text-slate-950 disabled:opacity-40">
          {training ? 'Training läuft im Hintergrund…' : 'Daten aktualisieren & trainieren'}
        </button>
      </div>
    </div>
    {!data?.installed && data && <p className="mt-3 text-xs text-amber-200">LightGBM ist noch nicht installiert. „LightGBM-installieren.cmd“ ausführen und das Backend neu starten.</p>}
    {error && <p role="alert" className="mt-3 text-xs text-amber-200">{error}</p>}
    {data?.job && <p role="status" aria-live="polite" className="mt-3 text-xs text-emerald-100">{data.job.message || 'Training ist vorgemerkt.'}</p>}
    {supported && <div className="mt-3 grid gap-3 text-xs md:grid-cols-3">
      <div className="rounded-lg bg-slate-900/70 p-3"><p className="text-slate-400">Gespeichertes Kursarchiv</p><strong>{data?.archive.candles.toLocaleString('de-AT') ?? '—'} Kerzen</strong><p className="mt-1 text-[11px] text-slate-500">{timeLabel(data?.archive.from)} bis {timeLabel(data?.archive.through)}</p></div>
      <div className="rounded-lg bg-slate-900/70 p-3"><p className="text-slate-400">Modell</p><strong>{model ? 'Zeitliche Prüfung bestanden' : latest ? 'Beobachtung · kein freigegebenes Modell' : 'Noch kein freigegebenes Modell'}</strong><p className="mt-1 text-[11px] text-slate-500">{model ? 'Gespeichert: ' + timeLabel(model.created_at) : 'Mindestens 30 Handelstage und 600 nutzbare Beispiele.'}</p></div>
      <div className="rounded-lg bg-slate-900/70 p-3"><p className="text-slate-400">Aktuelle Prüfung</p><strong className={prediction?.filter_action === 'BLOCKED' ? 'text-amber-200' : 'text-emerald-200'}>{!data?.enabled ? 'Filter ausgeschaltet' : labels[prediction?.filter_action ?? ''] || 'Warte auf Analyse'}</strong><p className="mt-1 text-[11px] text-slate-500">{!data?.enabled ? 'Gespeicherte Modelle bleiben erhalten.' : prediction?.reason}</p></div>
    </div>}
    {live && prediction.probabilities && <p className="mt-3 text-xs text-slate-300">Modellausgabe: Aufwärts {percent(prediction.probabilities.up)} · Seitwärts {percent(prediction.probabilities.flat)} · Abwärts {percent(prediction.probabilities.down)}. Keine gemessenen Trade-Trefferquoten.</p>}
    {(latest || model) && <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer text-emerald-200">Modellprüfung und Datenzeitraum</summary>
      {[['Freigegebenes Modell', model], ...(latest?.id !== model?.id ? [['Letzter Trainingsversuch', latest]] : [])].map(([label, candidate]) => {
        const row = candidate as LightGBMStatus['active_model'];
        if (!row) return null;
        return <div key={row.id} className="mt-3 rounded-lg bg-slate-900/70 p-3"><strong className="text-slate-200">{label as string}</strong>
          <p className="mt-1">Training {row.training_samples} · Validierung {row.validation_samples} · spätere Prüfung {row.metrics.samples} Beispiele.</p>
          <p>Treffer der Richtungsklassen: {percent(row.metrics.accuracy)} · einfache Vergleichsprognose: {percent(row.metrics.baseline_accuracy)}.</p>
          <p>Log-Loss: {row.metrics.log_loss.toFixed(3)} · Vergleich: {row.metrics.baseline_log_loss.toFixed(3)} (kleiner ist besser).</p>
          <p>Training ab {timeLabel(row.training_from)} · Test ab {timeLabel(row.test_from)} · geprüft bis {timeLabel(row.validated_through)}.</p>
          <p className="mt-1">{row.target}</p>
        </div>;
      })}
    </details>}
    <p className="mt-3 text-[11px] text-slate-400">Modelle bleiben über Neustarts gespeichert. Training nur per Knopfdruck; neue Kurskerzen werden bei Analysen archiviert. Nur freigegebene, aktuelle Modelle dürfen neue Einstiege bei deutlicher Gegenrichtung sperren. Bestehende Python-Backtests zeigen die Basisstrategie ohne LightGBM.</p>
  </section>;
}
