import { ArrowDownRight, ArrowUpRight, MinusCircle, ShieldAlert, Target } from 'lucide-react';
import { price, timeLabel } from '../api';
import type { ForecastSnapshot, TechnicalForecast } from '../types';

export function TechnicalForecastPanel({ forecast, history = [], currency }: {
  forecast?: TechnicalForecast; history?: ForecastSnapshot[]; currency?: string | null;
}) {
  if (!forecast) return <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-5 text-xs text-slate-400">Noch kein aktueller technischer Ausblick verfügbar.</section>;
  const available = forecast.status === 'AVAILABLE';
  const up = forecast.direction === 'UP';
  const color = available ? (up ? 'text-emerald-300' : 'text-red-300') : 'text-slate-300';
  const poi = forecast.poi_check;
  const poiLabels: Record<string, string> = {
    fair_value_gaps: 'FVG', order_blocks: 'Orderblock', support: 'Unterstützung',
    local_support: 'Lokale Unterstützung', resistance: 'Widerstand', local_resistance: 'Lokaler Widerstand',
  };
  return <section aria-label="Technischer Ausblick" className="rounded-xl border border-cyan-500/25 bg-[#0f172a] p-4 shadow-xl">
    <h2 className="flex items-center gap-2 text-sm font-bold text-cyan-200"><Target className="h-5 w-5" /> Technischer Ausblick ab aktuellem Kurs</h2>
    <div className="mt-4 rounded-xl border border-slate-700 bg-slate-900/70 p-4">
      <div className={'flex items-center gap-2 text-lg font-bold ' + color}>
        {available ? (up ? <ArrowUpRight /> : <ArrowDownRight />) : <MinusCircle />}
        {available ? (up ? 'Tendenz aufwärts' : 'Tendenz abwärts') : 'Keine klare Fortsetzung'}
      </div>
      <p className="mt-3 text-sm leading-relaxed text-slate-200">{forecast.summary}</p>
      <p className="mt-3 text-xs text-slate-400">{forecast.ticker} · {forecast.timeframe} · Indikatorstärke {forecast.indicator_strength.toFixed(1)}/100</p>
      <p className="mt-1 text-[10px] text-slate-500">Beobachteter Kurs {timeLabel(forecast.observed_at)} · Analysekerze {timeLabel(forecast.analysis_bar_at)}</p>
      {forecast.min_distance_pct != null && <p className="mt-2 text-xs text-cyan-200">Mindeststrecke für {forecast.timeframe}: {forecast.min_distance_pct.toLocaleString('de-DE')} %</p>}
    </div>
    {available && <>
      <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="text-slate-400">Aktueller Bezugskurs</p><strong className="mt-1 block text-base text-cyan-300">{price(forecast.reference_price, currency)}</strong></div>
        <div className="rounded-lg border border-cyan-500/25 bg-cyan-500/5 p-3"><p className="text-slate-400">Technisches Hauptziel · {forecast.timeframe}</p><strong className={'mt-1 block text-base ' + color}>{price(forecast.target_price, currency)}</strong><p className="mt-1 text-[10px] text-slate-400">Zone {price(forecast.target_zone_min)} – {price(forecast.target_zone_max)}</p></div>
        <div className="rounded-lg border border-red-500/20 bg-slate-900/60 p-3"><p className="flex items-center gap-1 text-slate-400"><ShieldAlert className="h-3 w-3" /> Einschätzung ungültig ab</p><strong className="mt-1 block text-base text-red-300">{price(forecast.invalidation_price, currency)}</strong><p className="mt-1 text-[10px] text-slate-500">Technische Grenze gegen die erwartete Richtung</p></div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="text-slate-400">Strecke bis zum Ziel</p><strong className="mt-1 block text-base text-cyan-200">{forecast.distance_pct?.toFixed(2)}%</strong><p className="mt-1 text-[10px] text-slate-500">Ab dem beobachteten Kurs</p></div>
      </div>
      {forecast.secondary_target_price != null && <p className="mt-3 text-xs text-slate-400">Weitere technische Zone: {price(forecast.secondary_target_price,currency)}</p>}
      <div className={'mt-3 rounded-lg border p-3 text-xs ' + (forecast.trade_eligible ? 'border-emerald-500/25 text-emerald-200' : 'border-amber-500/25 text-amber-200')}>
        {forecast.trade_reason}{forecast.risk_reward_ratio != null && <span className="mt-1 block">Potenzial / Risiko ab aktuellem Kurs: {forecast.risk_reward_ratio.toFixed(2)}R · vor Kosten</span>}
      </div>
    </>}
    {!available && forecast.nearest_zone_price != null && <p className="mt-3 text-xs text-amber-200">Nähere Kurszone bei {price(forecast.nearest_zone_price, currency)} erfüllt die Mindeststrecke nicht.</p>}
    {poi && <div className={'mt-4 rounded-lg border p-3 text-xs ' + (poi.status === 'CLEAR' ? 'border-emerald-500/25' : 'border-amber-500/25')}>
      <p className="font-semibold text-slate-200">POI-Prüfung bis zum Hauptziel</p>
      <p className="mt-1 text-slate-300">{poi.status === 'CLEAR' ? 'Keine blockierenden Gegenzonen erkannt.'
        : poi.status === 'BLOCKED' ? 'Gegenzone vor dem Hauptziel: keine Trade-Freigabe.'
        : poi.status === 'INCOMPLETE' ? 'Prüfung unvollständig: keine Trade-Freigabe.'
        : 'Wird geprüft, sobald ein technisches Hauptziel vorliegt.'}</p>
      {poi.status !== 'NOT_CHECKED' && <p className="mt-1 text-[10px] text-slate-400">Geprüft: {forecast.timeframe}{poi.checked_timeframes.length ? ' · ' + poi.checked_timeframes.join(' · ') : ''}</p>}
      {!!poi.unavailable_timeframes.length && <p className="mt-2 text-amber-200">Daten fehlen oder sind veraltet: {poi.unavailable_timeframes.join(', ')}</p>}
      {!!poi.obstacles.length && <ul className="mt-2 space-y-2">{poi.obstacles.slice(0, 6).map((item, index) => <li key={index} className={item.blocking ? 'text-amber-200' : 'text-slate-400'}>
        {item.blocking ? 'Blockiert' : 'Zwischenzone'} · {item.timeframe} · {poiLabels[item.kind] ?? item.kind} bei {price(item.price, currency)}
        <span className="mt-0.5 block text-[10px] text-slate-400">{item.reason}</span>
      </li>)}</ul>}
      {poi.obstacles.length > 6 && <p className="mt-2 text-slate-400">Weitere {poi.obstacles.length - 6} Zwischenzonen vorhanden.</p>}
    </div>}
    <p className="mt-4 text-[10px] leading-relaxed text-slate-500">Technisches Szenario anhand des aktuellen Kurses und abgeschlossener Analysekerzen. Kursziel und Indikatorstärke sind keine gemessene Trefferwahrscheinlichkeit. Neue Daten können den Ausblick ändern.</p>
    {!!history.length && <details className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-400"><summary className="cursor-pointer">Verlauf der Ausblicke</summary><ol className="mt-2 space-y-2">{history.map(item => <li key={item.id} className="rounded border border-slate-800 p-2"><p>{timeLabel(item.forecast.generated_at)} · {item.forecast.direction === 'UP' ? 'Aufwärts' : item.forecast.direction === 'DOWN' ? 'Abwärts' : 'Unklar'}</p><p className="mt-1 text-[10px]">Bezug {price(item.forecast.reference_price,currency)} · Ziel {price(item.forecast.target_price,currency)}</p></li>)}</ol></details>}
  </section>;
}
