import { ArrowDownRight, ArrowUpRight, Award, MinusCircle, ShieldAlert, Target } from 'lucide-react';
import { price, timeLabel } from '../api';
import type { TradeDecision } from '../types';

export function DecisionPanel({ decision, currency, currentPrice }: { decision?: TradeDecision; currency?: string | null; currentPrice?: number }) {
  if (!decision) return <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-5 text-xs text-slate-400">Noch keine Analyse verfügbar.</section>;
  const buy = decision.signal_type === 'BUY';
  const sell = decision.signal_type === 'SELL';
  const active = buy || sell;
  const python = decision.decision_source === 'python';
  const hasSetup = !!decision.setup_id || active;
  const quote = currentPrice != null && Number.isFinite(currentPrice) && currentPrice > 0 ? currentPrice : decision.current_price;
  const pending = decision.lifecycle_status === 'VORBEREITET';
  const unavailable = decision.verification_status === 'unavailable' || decision.validation_status === 'unavailable';
  const rejected = decision.verification_status === 'rejected_proposal';
  const heading = active ? (decision.signal_label || decision.signal_type) : unavailable ? (python ? 'TECHNIK NICHT VERFÜGBAR' : 'KI NICHT VERFÜGBAR') : rejected ? 'VORSCHLAG UNGÜLTIG' : 'ABWARTEN';
  const color = buy ? 'text-emerald-400' : sell ? 'text-red-400' : 'text-slate-400';
  const directionScore = Math.max(0, Math.min(100, decision.score));
  return (
    <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-4 shadow-xl" aria-label="Trade-Analyse">
      <div className="flex items-center justify-between border-b border-slate-800 pb-3">
        <h2 className="flex items-center gap-2 text-sm font-bold"><Award className="h-5 w-5 text-indigo-400" /> {python ? 'Trade-Analyse · Python' : 'Trade-Analyse'}</h2>
        <span className="rounded border border-indigo-500/30 bg-indigo-500/10 px-2 py-1 text-xs text-indigo-300">Grade {decision.setup_quality}</span>
      </div>
      <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className={'flex items-center gap-2 ' + color}>
            {buy ? <ArrowUpRight className="h-7 w-7" /> : sell ? <ArrowDownRight className="h-7 w-7" /> : <MinusCircle className="h-7 w-7" />}
            <strong className="text-lg">{heading}</strong>
          </div>
          <div className="text-right"><p className="text-[10px] text-slate-400">Richtungsscore</p><strong className="text-xl text-cyan-300">{directionScore}<span className="text-xs text-slate-500">/100</span></strong></div>
        </div>
        <div role="meter" aria-label="Richtungsscore" aria-valuemin={0} aria-valuemax={100} aria-valuenow={directionScore} aria-valuetext={directionScore+' von 100; 50 ist neutral'} className="relative mt-3 h-2 rounded-full bg-gradient-to-r from-red-500 via-slate-500 to-emerald-500"><span style={{left:directionScore+'%'}} className="absolute -top-0.5 h-3 w-1 -translate-x-1/2 rounded bg-white shadow" /></div>
        <div className="mt-2 flex justify-between text-[10px] text-slate-500"><span>0 = stark bearish</span><span>50 = neutral</span><span>100 = stark bullish</span></div>
        <p className="mt-3 text-xs text-slate-400">{python ? 'Technischer Setup-Score: ' : 'Signalstärke: '}<span className={color}>{decision.strength}/100</span> · {decision.ticker} · {decision.timeframe}{!python && decision.confidence != null ? ' · KI-Konfidenz ' + decision.confidence + '/100' : ''}</p>
        {decision.lifecycle_status && <div className="mt-3 border-t border-slate-700 pt-3 text-xs">
          <p className="font-semibold text-cyan-200">{hasSetup ? decision.lifecycle_status : 'Noch kein bestätigter Handelsplan'}{decision.hold_previous ? ' · vorherige Richtung vorläufig halten' : ''}{decision.tp1_reached ? ' · TP1 erreicht' : ''}</p>
          {hasSetup && <p className="mt-1 text-[10px] text-slate-400">Gegenbestätigungen {decision.opposing_bars || 0}/2 · Konfidenzeinbruch {decision.low_confidence_bars || 0}/2 · Laufzeit {decision.elapsed_bars || 0}/{decision.valid_for_bars || 12} Kerzen</p>}
          <p className="mt-1 text-[10px] text-slate-500">{hasSetup ? 'Erstellt ' + timeLabel(decision.created_at ?? null) + ' · ' : ''}zuletzt geprüft {timeLabel(decision.last_verified_at ?? null)}</p>
          {decision.verification_status !== 'verified' && <p className="mt-2 text-amber-300">{decision.analysis_error?.message || 'Aktuelle Prüfung ausstehend oder nicht verwertbar.'} {hasSetup ? 'Letzter gespeicherter Stand.' : 'Das ist keine bestätigte NEUTRAL-Bewertung.'}</p>}
        </div>}
      </div>
      {active && <p className="mt-3 rounded border border-cyan-500/20 bg-cyan-500/5 p-3 text-xs text-slate-300">
        Aktueller Kurs <strong>{price(quote, currency)}</strong>
        {decision.entry_price != null && quote > 0 && <span className="mt-1 block text-[11px] text-slate-400">{pending ? 'Geplanter Entry' : 'Ursprünglicher Entry'}: {price(decision.entry_price, currency)} · Abstand zum Kurs {(Math.abs(decision.entry_price - quote) / quote * 100).toFixed(2)}%</span>}
        {pending && <span className="mt-1 block text-[11px] text-cyan-200">Noch kein Einstieg ausgelöst. {decision.entry_mode === 'CONFIRMATION' ? 'Entry-Zone und Bestätigung abwarten.' : 'Erreichen der Entry-Zone abwarten.'}</span>}
      </p>}
      {active ? <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="mb-1 flex items-center gap-1 text-slate-400"><Target className="h-3 w-3" /> {decision.entry_kind === 'CURRENT' ? 'Entry am aktuellen Kurs' : decision.entry_mode === 'STOP' ? 'Stop-Entry' : decision.entry_mode === 'CONFIRMATION' ? 'Entry nach Bestätigung' : 'Limit-Entry'}</p><strong className="text-base text-cyan-300">{price(decision.entry_price, currency)}</strong><p className="mt-1 text-[10px] text-slate-500">Zone {price(decision.entry_zone_min)} – {price(decision.entry_zone_max)}</p>{python && decision.entry_price != null && decision.current_price > 0 && <p className="mt-1 text-[10px] text-cyan-200">Abstand zum Kurs {(Math.abs(decision.entry_price - decision.current_price) / decision.current_price * 100).toFixed(2)}%</p>}</div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="mb-1 flex items-center gap-1 text-slate-400"><ShieldAlert className="h-3 w-3" /> Stop-Loss</p><strong className="text-base text-red-400">{price(decision.stop_loss, currency)}</strong><p className="mt-1 text-[10px] text-slate-500">Kursrisiko {decision.risk_pct?.toFixed(2)}% je Einheit</p></div>
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3"><p className="mb-1 text-slate-400">{decision.take_profit_2 == null ? 'Take-Profit-Ziel' : 'Take-Profit-Ziele'}</p><p className="font-bold text-emerald-300">TP1 {price(decision.take_profit_1, currency)}</p>{decision.take_profit_2 != null && <p className="mt-1 text-emerald-400">TP2 {price(decision.take_profit_2, currency)}</p>}<p className="mt-1 text-[10px] text-slate-500">TP1-Kurspotenzial {decision.potential_profit_pct?.toFixed(2)}%</p></div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="mb-1 text-slate-400">Risiko / Chance (TP1)</p><strong className="text-base text-indigo-300">1 : {decision.risk_reward_ratio?.toFixed(2)}</strong><p className="mt-1 text-[10px] text-slate-500">Vor Gebühren und Slippage</p></div>
      </div> : <p className="mt-4 rounded-lg border border-slate-700 bg-slate-900 p-3 text-xs text-slate-400">{hasSetup ? 'Setup beendet oder abgelaufen. Die ursprünglichen Kurslevel bleiben in der Historie erhalten.' : unavailable ? 'Keine aktuelle Auswertung verfügbar. Daraus lässt sich keine Handelsrichtung ableiten.' : rejected ? 'Der Vorschlag enthält einen ungültigen Handelsplan. Den konkreten Grund findest du unten.' : 'Kein gültiger Einstieg. Es werden keine Trade-Ziele vorgeschlagen.'}</p>}
      {active && decision.entry_condition && <p className="mt-3 rounded border border-cyan-500/20 bg-cyan-500/5 p-3 text-xs text-cyan-100">{decision.lifecycle_status === 'VORBEREITET' ? 'Geplante Chance: ' : 'Einstiegsbedingung: '}{decision.entry_condition}<span className="mt-1 block text-[10px] text-slate-400">Invalidation: {decision.invalidation_condition} · {price(decision.invalidation_price ?? null, currency)}</span></p>}
      {decision.latest_ai_proposal && decision.latest_ai_proposal.validation_status !== 'unavailable' && <p className="mt-3 text-[10px] text-slate-400">{python ? 'Letzter technischer Vorschlag' : 'Letzter KI-Vorschlag'}: {decision.latest_ai_proposal.signal_type} · {python ? 'Setup-Score' : 'Konfidenz'} {decision.latest_ai_proposal.confidence}/100 · {decision.latest_ai_proposal.validation_status}</p>}
      {decision.decision_breakdown && <p className="mt-2 text-[10px] text-slate-400">Gewichtete Bewertung des ursprünglichen Setups: {decision.decision_breakdown.weighted_direction_score.toFixed(1)}/100 · {decision.decision_breakdown.normal_news_events} unterschiedliche Nachrichtenereignisse.</p>}
      <h3 className="mb-2 mt-4 text-xs font-semibold text-slate-400">Begründung</h3>
      <ul className="space-y-2">{decision.reasons.map((reason, i) => <li key={i} className="rounded border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs leading-relaxed text-slate-300">{reason}</li>)}</ul>
      <p className="mt-3 text-[10px] text-slate-500">Gruppenvergleich: {decision.group_comparison || 'nicht verfügbar'} · Quelle: {python ? 'Python · nur Technik' : decision.decision_source === 'gemini' ? 'Gemini-Gesamtanalyse' : 'kein KI-Trade'} · Validierung: {decision.validation_status}</p>
      <p className="mt-2 text-[10px] leading-relaxed text-slate-500">{python ? 'Python bestimmt Richtung und Kurslevel anhand technischer Gruppen und Marktstruktur. Keine KI, keine Nachrichten.' : 'Gemini bestimmt Analyse, Richtung und Kurslevel.'} Die Signalverwaltung erhält das ursprüngliche Setup, zählt abgeschlossene Kerzen und überwacht seine Bedingungen. Keine Broker-Order und keine Gewinnwahrscheinlichkeit. Ursprüngliche Analysekerze: {timeLabel(decision.as_of)}.</p>
      {!!decision.history?.length && <details className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-400"><summary className="cursor-pointer">Signalhistorie · letzte {decision.history.length} Ereignisse</summary><ol className="mt-2 space-y-2">{decision.history.map(event => <li key={event.id} className="rounded border border-slate-800 p-2"><p>{timeLabel(event.created_at)} · {event.decision.signal_label} · {event.decision.lifecycle_status}</p><p className="mt-1 text-[10px]">{event.decision.state_reason}</p>{event.decision.setup_id && <p className="mt-1 text-[10px]">Entry {price(event.decision.entry_price)} · SL {price(event.decision.stop_loss)} · TP1 {price(event.decision.take_profit_1)} · TP2 {price(event.decision.take_profit_2)}</p>}</li>)}</ol></details>}
    </section>
  );
}
