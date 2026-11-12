import { Activity } from 'lucide-react';
import { price } from '../api';
import type { IndicatorGroup, TechnicalData } from '../types';

function groupTone(direction: string) {
  return direction === 'BULLISH' ? 'text-emerald-300' : direction === 'BEARISH' ? 'text-red-300' : 'text-slate-300';
}

function GroupCard({ group }: { group: IndicatorGroup }) {
  return <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
    <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-slate-200">{group.name}</h3><span className={'text-xs font-bold ' + groupTone(group.direction)}>{group.direction}</span></div>
    <div className="mt-2 flex items-end justify-between"><strong className={groupTone(group.direction)}>{group.score > 0 ? '+' : ''}{group.score}</strong><span className="text-[10px] text-slate-500">{group.agreement.toFixed(0)}% interne Übereinstimmung</span></div>
    <div className="mt-2 flex flex-wrap gap-1.5">{group.components.map(item => <span key={item.name} title={item.detail || undefined} className={'rounded border px-1.5 py-0.5 text-[9px] ' + (item.signal > 0 ? 'border-emerald-500/20 text-emerald-300' : item.signal < 0 ? 'border-red-500/20 text-red-300' : 'border-slate-700 text-slate-500')}>{item.name}</span>)}</div>
  </div>;
}

export function TechnicalPanel({ technical, currency }: {
  technical?: TechnicalData; currency?: string | null;
}) {
  if (!technical || technical.error) return <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-4 text-xs text-slate-400">{technical?.error || 'Technische Analyse noch nicht verfügbar.'}</section>;
  const groups = technical.indicator_groups;
  const comparison = groups.comparison === 'CONFLICT' ? 'Gruppen widersprechen sich' : groups.comparison.startsWith('CONFIRMED') ? 'Beide Gruppen bestätigen dieselbe Richtung' : 'Nur teilweise bestätigt oder neutral';
  const metrics = [
    ['RSI / Stoch-RSI', technical.rsi.toFixed(1), 'K ' + (technical.stochastic_rsi.k?.toFixed(1) ?? '—') + ' · D ' + (technical.stochastic_rsi.d?.toFixed(1) ?? '—')],
    ['MACD / ROC', price(technical.macd.hist), 'ROC12 ' + (technical.roc_12?.toFixed(2) ?? '—') + '%'],
    ['ADX / DMI', technical.adx.adx?.toFixed(1) ?? '—', '+DI ' + (technical.adx.plus_di?.toFixed(1) ?? '—') + ' · −DI ' + (technical.adx.minus_di?.toFixed(1) ?? '—')],
    ['ATR / Volatilität', price(technical.atr, currency), technical.atr_percent.toFixed(2) + '% vom Kurs'],
    ['VWAP / Supertrend', price(technical.vwap, currency), technical.supertrend.direction],
    ['CCI / Williams %R', technical.cci_20?.toFixed(1) ?? '—', technical.williams_r?.toFixed(1) ?? '—'],
    ['CMF / MFI', technical.volume.cmf_20?.toFixed(3) ?? '—', technical.volume.mfi_14?.toFixed(1) ?? '—'],
    ['Volume Profile POC', price(technical.volume_profile.poc, currency), 'VA ' + price(technical.volume_profile.value_area_low) + ' – ' + price(technical.volume_profile.value_area_high)],
  ];
  return (
    <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-4 shadow-xl" aria-label="Technische Analyse">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3"><h2 className="flex items-center gap-2 text-sm font-bold"><Activity className="h-4 w-4 text-cyan-400" /> Technische Analyse in zwei Gruppen</h2><span className="text-xs text-cyan-200">{comparison}</span></div>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2"><GroupCard group={groups.trend_structure}/><GroupCard group={groups.momentum_participation}/></div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">{metrics.map(([label, value, detail]) => <div key={label} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="text-[11px] text-slate-400">{label}</p><strong className="mt-1 block text-sm text-cyan-200">{value}</strong><p className="mt-1 text-[10px] text-slate-500">{detail}</p></div>)}</div>
      <div className="mt-3 flex flex-wrap gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-[11px]">{Object.entries(technical.ema).map(([period, value]) => <span key={period} className="text-slate-400">EMA{period}: <strong className="text-slate-200">{value == null ? 'Historie fehlt' : price(value, currency)}</strong></span>)}</div>
      <div className="mt-3 flex flex-wrap gap-4 text-[11px] text-slate-400"><span>Support: {price(technical.support, currency)}</span><span>Resistance: {price(technical.resistance, currency)}</span><span>FVGs: {technical.market_structure.fair_value_gaps.length}</span><span>Orderblocks: {technical.market_structure.order_blocks.length}</span><span>{technical.market_structure.event}</span></div>
      {technical.warnings.map(warning => <p key={warning} className="mt-2 text-[10px] text-amber-200">{warning}</p>)}
    </section>
  );
}
