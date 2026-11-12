import { BarChart3 } from 'lucide-react';
import { price, timeLabel } from '../api';
import type { BacktestResult } from '../types';

export function BacktestPanel({ backtest }: { backtest?: BacktestResult }) {
  if (!backtest) return <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-4 text-xs text-slate-400">Backtest noch nicht verfügbar.</section>;
  const metrics = [
    ['Win Rate', backtest.total_trades ? backtest.win_rate + '%' : '—', backtest.winning_trades + ' W / ' + backtest.losing_trades + ' L'],
    ['Profit Factor', backtest.profit_factor?.toFixed(2) ?? '—', backtest.total_trades ? 'Netto-Gewinne / Netto-Verluste' : 'Keine abgeschlossenen Trades'],
    ['Gesamtrendite', backtest.total_return_pct.toFixed(2) + '%', price(backtest.final_capital, backtest.currency || 'USD') + ' Modellkapital'],
    ['Max. Drawdown', '−' + backtest.max_drawdown_pct.toFixed(2) + '%', 'Mit offenen Positionen bewertet'],
  ];
  return (
    <section className="rounded-xl border border-slate-800 bg-[#0f172a] p-4 shadow-xl" aria-label="Backtest">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3"><h2 className="flex items-center gap-2 text-sm font-bold"><BarChart3 className="h-4 w-4 text-emerald-400" /> Historischer Strategietest</h2><span className="text-[11px] text-slate-400">{backtest.total_trades} Trades · Strategie {backtest.strategy_version}</span></div>
      {backtest.error ? <p className="mt-3 text-xs text-amber-200">{backtest.error}</p> : <>
        <p className="mt-3 text-[11px] text-slate-400">{timeLabel(backtest.start_at)} – {timeLabel(backtest.end_at)}</p>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">{metrics.map(([label, value, detail]) => <div key={label} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"><p className="text-[11px] text-slate-400">{label}</p><strong className="mt-1 block text-base text-slate-100">{value}</strong><p className="mt-1 text-[10px] text-slate-500">{detail}</p></div>)}</div>
        <p className="mt-3 text-[11px] leading-relaxed text-slate-400">{backtest.scope}</p>
        {backtest.coverage_note && <p className="mt-2 text-[11px] text-amber-200">{backtest.coverage_note}</p>}
        <p className="mt-1 text-[11px] text-slate-500">{backtest.analysis_mode === 'gemini_quant' ? 'Nachrichten, Makro, Python-Indikatoren und Quant stammen aus den tatsächlich aufgezeichneten KI-Kontexten.' : backtest.news_coverage_bars > 0 ? 'Historische News in ' + backtest.news_coverage_bars + ' Entscheidungszeitpunkten verfügbar.' : 'Dieser Lauf bewertet ausschließlich die Python-Technik.'}</p>
        <details className="mt-3 text-[11px] text-slate-400"><summary className="cursor-pointer">Kosten und Annahmen</summary><p className="mt-2">{backtest.costs}</p><p className="mt-1">{backtest.assumptions}</p><p className="mt-1">{backtest.position_sizing}</p></details>
        {backtest.recent_trades.length > 0 && <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-[11px]"><thead className="text-slate-500"><tr><th className="pb-2">Trade</th><th>Entry</th><th>Exit</th><th>Ergebnis</th><th>Ausgang</th></tr></thead><tbody>{backtest.recent_trades.slice(-5).map((trade, i) => <tr key={i} className="border-t border-slate-800"><td className="py-2">{trade.type}</td><td>{price(trade.entry_price, backtest.quote_currency || 'USD')}</td><td>{price(trade.exit_price, backtest.quote_currency || 'USD')}</td><td className={trade.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{price(trade.pnl, backtest.currency || 'USD')}</td><td className="text-slate-400">{trade.tp1_taken ? 'TP1 + ' : ''}{trade.reason}</td></tr>)}</tbody></table></div>}
      </>}
    </section>
  );
}
