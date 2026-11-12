import { timeLabel } from '../api';
import type { QuantitativeData, QuantitativeSettings } from '../types';

function tone(direction?: string) {
  return direction === 'BULLISH' ? 'text-emerald-300' : direction === 'BEARISH' ? 'text-red-300' : 'text-slate-400';
}
function number(value?: number | null, digits = 2) {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('de-AT', { maximumFractionDigits: digits });
}

export function QuantitativeControls({ settings, running, saving, onToggle, onCalculate }: {
  settings: QuantitativeSettings | null; running: boolean; saving: boolean;
  onToggle: () => void; onCalculate: () => void;
}) {
  return <section aria-label="Quantitative Gemini-Unterfunktion" className="mb-4 rounded-xl border border-purple-500/30 bg-purple-500/5 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-bold text-purple-200">Gemini · Quantitative Analyse (manuell)</h2>
        <p className="mt-1 text-xs text-slate-400">Delta/CVD-Schätzung · bis zu {settings?.configured_weight_pct ?? '—'} % Gesamtgewicht; bei wenigen Quellkerzen reduziert. Dazu Options-Greeks als Risikokontext, sofern verfügbar.</p></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" role="switch" aria-checked={settings?.enabled ?? false} disabled={!settings || saving || running}
          onClick={onToggle} className="rounded-lg border border-purple-400/40 px-3 py-2 text-xs disabled:opacity-50">
          {saving ? 'Speichere…' : settings?.enabled ? 'Quant: EIN' : 'Quant: AUS'}
        </button>
        <button type="button" disabled={!settings?.enabled || running || saving} onClick={onCalculate}
          className="rounded-lg bg-purple-300 px-3 py-2 text-xs font-bold text-slate-950 disabled:opacity-40">
          {running ? 'Berechne und analysiere…' : 'Quant berechnen + Gemini analysieren'}
        </button>
      </div>
    </div>
    <p className="mt-2 text-[11px] text-slate-400">Nur per Knopfdruck: gespeicherte Berechnungen laden und neue Daten ergänzen. Einschalten, Kursupdates und Chartwechsel starten keine Quant-Berechnung. Ein gestarteter Marktscan berechnet Quant für die verglichenen Instrumente, wenn eingeschaltet.</p>
    <p className="mt-1 text-[11px] text-slate-400">Für automatische Analysen ohne geöffneten Browser den separaten Dauerbetrieb einschalten.</p>
  </section>;
}

export function QuantitativeCard({ data, weight }: { data?: QuantitativeData | null; weight: number }) {
  const current = data?.status === 'AVAILABLE';
  const label = current ? data.direction : data?.status === 'STALE' ? 'VERALTET' : data?.status === 'PARTIAL' ? 'UNVOLLSTÄNDIG' : 'BEREIT';
  const comparisons: Record<string, string> = {
    ALL_ALIGNED: 'Quant bestätigt beide Indikatorgruppen.',
    CONFLICT: 'Quant widerspricht mindestens einer Indikatorgruppe.',
    PARTIAL: 'Nur teilweise mit den Indikatorgruppen bestätigt.',
    NO_DIRECTIONAL_CONFIRMATION: 'Keine zusätzliche Richtungsbestätigung.',
  };
  return <div aria-label="Quantitative Auswertung" className="rounded-lg border border-purple-500/30 bg-slate-900/60 p-3">
    <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-purple-200">Quant · Delta/CVD + Statistik</h3><span className={'text-xs font-bold ' + tone(current ? data?.direction : undefined)}>{label}</span></div>
    <div className="mt-2 flex items-end justify-between"><strong className={'text-lg ' + tone(current ? data?.direction : undefined)}>{current ? number(data.direction_score) + '/100' : '—'}</strong><span className="text-[10px] text-slate-400">{number(current ? weight : 0)} % angewendet</span></div>
    {!current && <p className="mt-2 text-[11px] text-amber-200">{data?.status === 'STALE'
      ? '0 %: Neue oder korrigierte Daten sind vorhanden. Quant erneut per Knopfdruck berechnen.'
      : data?.status === 'PARTIAL'
      ? '0 %: Quellkerzen oder nutzbares Volumen fehlen. Vorhanden: ' + (data.covered_bars ?? 0) + '/' + (data.window_bars ?? 20) + ' Intervalle.'
      : '0 %: Für dieses Instrument und diesen Timeframe wurde noch keine Berechnung gestartet.'}</p>}
    <p className="mt-2 text-[11px] text-slate-400">{data?.reason || 'Quant einschalten und die Berechnung für diesen Chart starten.'}</p>
    {data?.group_comparison && <p className="mt-2 text-[11px] text-purple-200">{comparisons[data.group_comparison]}</p>}
    {data?.computed_at && <p className="mt-2 text-[10px] text-slate-500">Berechnet {timeLabel(data.computed_at)} · Daten bis {timeLabel(data.analysis_bar_at)} · Quelle {data.source_timeframe}</p>}
    {data?.status === 'STALE' && data.direction_score != null && <p className="mt-1 text-[10px] text-slate-500">Archivierter Score: {number(data.direction_score)}/100; nicht aktuell gewichtet.</p>}
    <details className="mt-3 text-[11px] text-slate-400"><summary className="cursor-pointer text-purple-200">Kennzahlen und Gewichtung</summary>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <dt>Delta geschätzt</dt><dd>{number(data?.delta)} · {number(data?.delta_pct)} %</dd>
        <dt>CVD geschätzt</dt><dd>{number(data?.cvd)}</dd>
        <dt>CVD-Anker</dt><dd>{timeLabel(data?.cvd_anchor_at)}</dd>
        <dt>CVD letzte 20 Kerzen</dt><dd>{number(data?.cvd_change_20)} · {number(data?.cvd_pressure_pct)} %</dd>
        <dt>Z-Score (20)</dt><dd>{number(data?.z_score_20)}</dd>
        <dt>Marktregime</dt><dd>{data?.market_regime || '—'} · ER {number(data?.efficiency_ratio_20)}</dd>
        <dt>Volatilität je Kerze</dt><dd>{number(data?.realized_volatility_20_pct)} %</dd>
        <dt>Relative Stärke / {data?.benchmark || 'Vergleich'}</dt><dd>{number(data?.relative_strength_20_pct)} %</dd>
        <dt>Abgedeckte Kerzen</dt><dd>{data?.covered_bars ?? 0}/{data?.window_bars ?? 20}</dd>
        <dt>Abweichendes Chartvolumen</dt><dd>{data?.volume_mismatch_bars ?? 0} Intervalle</dd>
        <dt>Unbekanntes Quellvolumen</dt><dd>{data?.missing_volume_bars ?? 0} Intervalle</dd>
        <dt>Quellkerzen je Hauptkerze</dt><dd>{number(data?.source_bars_per_main)}</dd>
        <dt>Freigegebener Gewichtsanteil</dt><dd>{number((data?.weight_quality_factor ?? 0) * 100)} % der Obergrenze</dd>
        <dt>Relative Stärke, risikoskaliert</dt><dd>{number(data?.relative_strength_context_score)}/100 · nur Kontext</dd>
      </dl>
      <ul className="mt-3 space-y-1">{data?.components?.map(component => <li key={component.key} className={tone(component.direction)}>{component.name}: {component.direction} · {number(component.score)}/100 · {component.weight_pct} % im Quant-Block</li>)}</ul>
      <p className="mt-2">Die Einzelkerze wird nur angezeigt. Für die Richtung zählen getrennte Fenster mit 3 und 17 Kerzen; geringe Aktivität wird gedämpft. Momentum, relative Stärke, Z-Score und Marktregime dienen als Preis-Kontext. Startgewichte, keine gemessenen Erfolgswahrscheinlichkeiten.</p>
      {data?.divergence === 'PRICE_UP_CVD_DOWN' && <p className="mt-2 text-amber-200">Widerspruch: Kurs steigt, geschätztes CVD fällt über 20 Kerzen.</p>}
      {data?.divergence === 'PRICE_DOWN_CVD_UP' && <p className="mt-2 text-amber-200">Widerspruch: Kurs fällt, geschätztes CVD steigt über 20 Kerzen.</p>}
    </details>
    <p className="mt-3 text-[10px] text-amber-200">{data?.method_note || 'OHLCV-Schätzung; keine Messung aggressiver Käufer und Verkäufer.'}</p>
    {data?.warnings?.map(warning => <p key={warning} className="mt-1 text-[10px] text-amber-200">{warning}</p>)}
  </div>;
}
