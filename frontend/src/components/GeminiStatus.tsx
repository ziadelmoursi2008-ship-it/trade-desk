import { IS_DEMO, timeLabel } from '../api';
import type { AnalysisResponse } from '../types';

export function GeminiStatus({ analysis, busy }: { analysis: AnalysisResponse | null; busy: boolean }) {
  if (IS_DEMO) return <p className="setting-card text-xs leading-relaxed text-slate-400">Demo-Vorschau: Beispielszenario ohne Gemini-Anfrage. Live-Nachrichten und KI-Analysen benötigen die lokale Vollversion.</p>;
  const pipeline = analysis?.news_pipeline;
  const latest = analysis?.latest_ai_decision;
  const evaluated = pipeline?.ai_status === 'evaluated' && analysis?.ai_analysis_method === 'gemini';
  const outcome = !latest ? 'NICHT VERFÜGBAR' : latest.signal_type === 'BUY' ? 'KAUFEN' : latest.signal_type === 'SELL' ? 'VERKAUFEN' : 'ABWARTEN';
  return <section aria-label="Aktueller Gemini-Status" aria-live="polite"
    className="mb-4 rounded-xl border border-slate-700 bg-slate-900/60 p-3 text-xs">
    {busy ? <p className="text-cyan-200">Gemini-Analyse läuft. Die Antwort kann mehrere zehn Sekunden dauern.</p>
      : evaluated ? <>
        <p className="font-semibold text-emerald-200">Gemini hat geantwortet · {timeLabel(pipeline.evaluated_at ?? analysis.analyzed_at)} · neueste Einschätzung: {outcome}{pipeline.cache_hit ? ' · archivierte Antwort' : ''}</p>
        {latest?.validation_status === 'accepted_neutral' && <p className="mt-1 text-slate-400">ABWARTEN ist eine gültige KI-Antwort. Es gibt aktuell keinen freigegebenen neuen Einstieg.</p>}
        {latest?.validation_status === 'rejected' && <p className="mt-1 text-amber-200">Der neueste KI-Plan wurde bei der technischen Prüfung abgelehnt: {latest.reasons[0]}</p>}
        {latest?.reasons?.length ? <p className="mt-1 text-slate-400">{latest.reasons[0]}</p> : null}
        <p className="mt-1 text-[11px] text-slate-500">Die Signalbox zeigt zusätzlich den gespeicherten Handelsplan und dessen Zustand.</p>
      </> : <p className="text-amber-200">{pipeline?.ai_error?.message || 'Noch keine erfolgreiche Gemini-Antwort für diese Ansicht verfügbar.'}</p>}
  </section>;
}
