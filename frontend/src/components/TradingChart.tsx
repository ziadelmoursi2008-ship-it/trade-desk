import { memo, useEffect, useRef, useState } from 'react';
import { CandlestickChart, Layers, LineChart, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { CandleType, IndicatorSeries, dispose, init, registerIndicator, registerOverlay } from 'klinecharts';
import type { Chart, OverlayFigure } from 'klinecharts';
import { price } from '../api';
import type { KlineItem, NewsItem, TechnicalForecast, TradeDecision, TradeMarker } from '../types';

const COLORS = ['#38bdf8', '#fbbf24', '#a78bfa', '#fb923c', '#f472b6'];
const PERIODS = [9, 20, 50, 100, 200];
registerIndicator({
  name: 'AGENT_EMA', shortName: 'EMA', series: IndicatorSeries.Price,
  figures: PERIODS.map((period, i) => ({ key: 'ema' + period, title: String(period) + ': ', type: 'line' as const, styles: () => ({ color: COLORS[i], size: 1 }) })),
  calc: data => {
    const values = [...Array(PERIODS.length)].map(() => 0);
    return data.map((bar, index) => {
      const result: Record<string, number> = {};
      PERIODS.forEach((period, i) => {
        values[i] = index === 0 ? bar.close : values[i] + 2 / (period + 1) * (bar.close - values[i]);
        if (index + 1 >= period) result['ema' + period] = values[i];
      });
      return result;
    });
  },
});
registerIndicator({
  name: 'AGENT_RANGE', shortName: '', series: IndicatorSeries.Price,
  figures: [{ key: 'lower', type: 'line' }, { key: 'upper', type: 'line' }],
  calc: (data, indicator) => data.map(() => indicator.extendData || {}), draw: () => true,
  createTooltipDataSource: () => ({ name: '', calcParamsText: '', values: [], icons: [] }),
});

function line(x1: number, x2: number, y: number, color: string, dashed = false): OverlayFigure {
  return { type: 'line', attrs: { coordinates: [{ x: x1, y }, { x: x2, y }] }, styles: { color, size: 1.5, style: dashed ? 'dashed' : 'solid', dashedValue: [5, 4] }, ignoreEvent: true };
}
function text(x: number, y: number, label: string, color: string, backgroundColor = '#0f172a'): OverlayFigure {
  return { type: 'text', attrs: { x, y, text: label, baseline: 'middle' }, styles: { color, size: 10, weight: 'bold', backgroundColor, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, borderRadius: 3 }, ignoreEvent: true };
}
function box(x: number, y1: number, y2: number, width: number, color: string, borderColor: string): OverlayFigure {
  return { type: 'rect', attrs: { x, y: Math.min(y1, y2), width, height: Math.max(1, Math.abs(y2 - y1)) },
    styles: { style: 'stroke_fill', color, borderColor, borderSize: 1 }, ignoreEvent: true };
}
registerOverlay({
  name: 'agentRiskReward', totalStep: 1, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    if (coordinates.length < 4) return [];
    const [entry, stop, tp1, tp2] = coordinates;
    const d = overlay.extendData as TradeDecision;
    const left = entry.x;
    const right = Math.max(left + 100, stop.x);
    const width = right - left;
    const entryPrice = d.entry_price!;
    const target = (value: number) => Math.abs((value - entryPrice) / entryPrice * 100).toFixed(2);
    return [
      box(left, entry.y, tp1.y, width, 'rgba(16,185,129,0.18)', '#10b981'),
      ...(d.take_profit_2 != null ? [box(left, tp1.y, tp2.y, width, 'rgba(52,211,153,0.29)', '#34d399')] : []),
      box(left, entry.y, stop.y, width, 'rgba(239,68,68,0.22)', '#f87171'),
      line(left, right, tp1.y, '#10b981', true),
      ...(d.take_profit_2 != null ? [line(left, right, tp2.y, '#34d399', true)] : []),
      line(left, right, stop.y, '#f87171', true), line(left, right, entry.y, '#38bdf8'),
      text(left + 5, entry.y, (d.lifecycle_status === 'VORBEREITET' ? 'GEPLANTER ENTRY ' : 'ENTRY ') + price(entryPrice), '#7dd3fc'),
      text(left + 5, tp1.y, 'TP1 ' + price(d.take_profit_1) + '  +' + target(d.take_profit_1!) + '%', '#6ee7b7'),
      ...(d.take_profit_2 != null ? [text(left + 5, tp2.y, 'TP2 ' + price(d.take_profit_2) + '  +' + target(d.take_profit_2) + '%', '#a7f3d0')] : []),
      text(left + 5, stop.y, 'SL ' + price(d.stop_loss) + '  −' + target(d.stop_loss!) + '%', '#fca5a5'),
      text(left + 5, (entry.y + stop.y) / 2, 'RISIKO · 1:' + d.risk_reward_ratio?.toFixed(2), '#fca5a5', 'rgba(69,10,10,0.8)'),
    ];
  },
});
registerOverlay({
  name: 'agentMarker', totalStep: 1, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    if (!coordinates[0]) return [];
    const data = overlay.extendData as { text: string; color: string; above: boolean };
    const point = coordinates[0];
    const y = point.y + (data.above ? -18 : 18);
    return [{ type: 'text', attrs: { x: point.x, y, text: data.text, align: 'center', baseline: 'middle' },
      styles: { color: data.color, size: 10, weight: 'bold', backgroundColor: '#0f172a', paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, borderColor: data.color, borderSize: 1, borderRadius: 3 } }];
  },
});

registerOverlay({
  name: 'agentForecast', totalStep: 1, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    if (coordinates.length < 2) return [];
    const d = overlay.extendData as TechnicalForecast;
    const reference = coordinates[0], target = coordinates[1];
    let next = 2;
    const secondary = d.secondary_target_price != null ? coordinates[next++] : undefined;
    const invalidation = d.invalidation_price != null ? coordinates[next] : undefined;
    const left = reference.x, right = Math.max(left + 100,target.x);
    const figures: OverlayFigure[] = [
      box(left,reference.y,target.y,right-left,'rgba(56,189,248,0.14)','#38bdf8'),
      line(left,right,reference.y,'#94a3b8'),
      line(left,right,target.y,'#38bdf8',true),
      text(left+5,reference.y,'AKTUELL '+price(d.reference_price),'#e2e8f0'),
      text(left+5,target.y,'ZIEL '+price(d.target_price),'#7dd3fc'),
    ];
    if (secondary) figures.push(line(left,right,secondary.y,'#67e8f9',true),text(left+5,secondary.y,'WEITERE ZONE '+price(d.secondary_target_price),'#a5f3fc'));
    if (invalidation) figures.push(box(left,reference.y,invalidation.y,right-left,'rgba(239,68,68,0.10)','#f87171'),
      line(left,right,invalidation.y,'#f87171',true),text(left+5,invalidation.y,'UNGUELTIG AB '+price(d.invalidation_price),'#fca5a5'));
    return figures;
  },
});

interface Props {
  ticker: string; timeframe: string; currency?: string | null; klines: KlineItem[];
  decision?: TradeDecision; news: NewsItem[]; markers: TradeMarker[]; analysisBarAt?: number | null;
  forecast?: TechnicalForecast;
}
export const TradingChart = memo(function TradingChart({ ticker, timeframe, currency, klines, decision, forecast, news, markers, analysisBarAt }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const previousDataRef = useRef<KlineItem[]>([]);
  const [showZones, setShowZones] = useState(true);
  const [showNews, setShowNews] = useState(true);
  const [isLine, setIsLine] = useState(false);
  const active = !forecast && decision?.ticker === ticker && decision.timeframe === timeframe && decision.signal_type !== 'NEUTRAL'
    && decision.entry_price != null && decision.stop_loss != null && decision.take_profit_1 != null;
  const projection = forecast?.ticker === ticker && forecast.timeframe === timeframe && forecast.status === 'AVAILABLE'
    && forecast.reference_price != null && forecast.target_price != null;

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    // Dispose a retained instance before initializing: HMR/StrictMode must never add a second pane.
    dispose(element);
    const chart = init(element, {
      locale: 'en-US', timezone: 'Europe/Vienna',
      styles: { grid: { horizontal: { color: '#1e293b' }, vertical: { color: '#1e293b' } },
        candle: { bar: { upColor: '#10b981', downColor: '#f87171', upBorderColor: '#10b981', downBorderColor: '#f87171', upWickColor: '#10b981', downWickColor: '#f87171' } },
        xAxis: { tickText: { color: '#94a3b8' }, axisLine: { color: '#334155' } },
        yAxis: { tickText: { color: '#94a3b8' }, axisLine: { color: '#334155' } },
        separator: { color: '#334155' },
      },
    });
    if (!chart) return;
    chartRef.current = chart;
    chart.setOffsetRightDistance(210);
    chart.setBarSpace(7);
    chart.createIndicator('AGENT_EMA', false, { id: 'candle_pane' });
    chart.createIndicator('AGENT_RANGE', true, { id: 'candle_pane' });
    chart.createIndicator('VOL', false, { height: 85, minHeight: 60 });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => { observer.disconnect(); chartRef.current = null; dispose(element); };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const data = klines.filter(bar => [bar.timestamp, bar.open, bar.high, bar.low, bar.close].every(Number.isFinite));
    const previous = previousDataRef.current;
    const incremental = previous.length > 2 && data.length >= previous.length && data[0]?.timestamp === previous[0]?.timestamp
      && previous.slice(0, -2).every((bar, i) => data[i]?.timestamp === bar.timestamp && data[i]?.close === bar.close && data[i]?.volume === bar.volume);
    const last = data.at(-1)?.close || 100;
    chart.setPriceVolumePrecision(last < 1 ? 6 : last < 10 ? 4 : 2, 0);
    if (incremental) data.slice(previous.length - 2).forEach(bar => chart.updateData(bar));
    else chart.applyNewData(data, false);
    previousDataRef.current = data;
  }, [klines]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setStyles({ candle: { type: isLine ? CandleType.Area : CandleType.CandleSolid } });
  }, [isLine]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.removeOverlay({ groupId: 'trade' });
    chart.removeOverlay({ groupId: 'markers' });
    chart.removeOverlay({ groupId: 'news' });
    chart.overrideIndicator({
      name: 'AGENT_RANGE',
      extendData: showZones && projection ? {
        lower: Math.min(...[forecast!.reference_price,forecast!.target_price,forecast!.secondary_target_price,forecast!.invalidation_price].filter((value): value is number => value != null)),
        upper: Math.max(...[forecast!.reference_price,forecast!.target_price,forecast!.secondary_target_price,forecast!.invalidation_price].filter((value): value is number => value != null)),
      } : showZones && active ? {
        lower: Math.min(decision!.stop_loss!, decision!.take_profit_2 ?? decision!.take_profit_1!),
        upper: Math.max(decision!.stop_loss!, decision!.take_profit_2 ?? decision!.take_profit_1!),
      } : null,
    }, 'candle_pane');
    if (klines.length === 0) return;
    if (showZones && projection) {
      const lastIndex = klines.length-1;
      const points = [
        {dataIndex:lastIndex,value:forecast!.reference_price!},
        {dataIndex:lastIndex+23,value:forecast!.target_price!},
        ...(forecast!.secondary_target_price != null ? [{dataIndex:lastIndex+23,value:forecast!.secondary_target_price}] : []),
        ...(forecast!.invalidation_price != null ? [{dataIndex:lastIndex+23,value:forecast!.invalidation_price}] : []),
      ];
      chart.createOverlay({name:'agentForecast',groupId:'trade',lock:true,extendData:forecast,zLevel:-1,points});
    }
    if (showZones && active) {
      const lastIndex = klines.length - 1;
      const pending = decision!.lifecycle_status === 'VORBEREITET';
      chart.createOverlay({
        name: 'agentRiskReward', groupId: 'trade', lock: true, extendData: decision, zLevel: -1,
        points: [
          { dataIndex: pending ? lastIndex : Math.max(0, lastIndex - 4), value: decision!.entry_price! },
          { dataIndex: lastIndex + 23, value: decision!.stop_loss! },
          { dataIndex: lastIndex + 23, value: decision!.take_profit_1! },
          { dataIndex: lastIndex + 23, value: decision!.take_profit_2 ?? decision!.take_profit_1! },
        ],
      });
      chart.createOverlay({ name: 'agentMarker', groupId: 'trade', lock: true,
        points: [{ timestamp: pending ? klines.at(-1)!.timestamp : (decision!.activated_at || analysisBarAt || klines.at(-1)!.timestamp), value: decision!.entry_price! }],
        extendData: { text: pending ? 'ENTRY AUSSTEHEND' : (decision!.signal_label || decision!.signal_type) + ' · ' + (decision!.lifecycle_status || 'Setup'), color: decision!.signal_type === 'BUY' ? '#34d399' : '#f87171', above: decision!.signal_type === 'SELL' } });
    }
    markers.slice(-80).forEach(marker => {
      chart.createOverlay({ name: 'agentMarker', groupId: 'markers', lock: true,
        points: [{ timestamp: marker.timestamp, value: marker.price }],
        extendData: { text: marker.type, color: marker.type === 'BUY' ? '#34d399' : '#f87171', above: marker.type === 'SELL' } });
    });
    if (showNews) news.filter(item => item.published_ts != null).forEach(item => {
      const timestamp = item.published_ts! * 1000;
      if (timestamp < klines[0].timestamp || timestamp > klines.at(-1)!.timestamp) return;
      const bar = klines.findLast(value => value.timestamp <= timestamp);
      if (!bar) return;
      chart.createOverlay({ name: 'agentMarker', groupId: 'news', lock: true,
        points: [{ timestamp: bar.timestamp, value: bar.high }],
        extendData: { text: 'N', color: '#fbbf24', above: true },
        onClick: () => { if (/^https?:\/\//.test(item.url)) window.open(item.url, '_blank', 'noopener,noreferrer'); return true; } });
    });
  }, [decision, forecast, projection, klines, markers, news, showZones, showNews, active, analysisBarAt]);

  return (
    <section aria-label={'Kurschart ' + ticker} className="overflow-hidden rounded-xl border border-slate-800 bg-[#0f172a] shadow-xl">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2 text-xs">
        <strong>{ticker} · {timeframe} {currency ? '· ' + currency : ''}</strong>
        <div className="flex flex-wrap gap-1">
          <button aria-label="Kerzenchart" aria-pressed={!isLine} onClick={() => setIsLine(false)} className={'rounded p-2 ' + (!isLine ? 'bg-cyan-500/20 text-cyan-300' : 'text-slate-400')}><CandlestickChart className="h-4 w-4" /></button>
          <button aria-label="Linienchart" aria-pressed={isLine} onClick={() => setIsLine(true)} className={'rounded p-2 ' + (isLine ? 'bg-cyan-500/20 text-cyan-300' : 'text-slate-400')}><LineChart className="h-4 w-4" /></button>
          <button aria-pressed={showZones} onClick={() => setShowZones(value => !value)} className={'flex items-center gap-1 rounded border px-2 ' + (showZones ? 'border-emerald-500/40 text-emerald-300' : 'border-slate-700 text-slate-400')}><Layers className="h-3 w-3" /> {forecast ? 'Ziel / Invalidation' : 'Profit / Stop'}</button>
          <button aria-pressed={showNews} onClick={() => setShowNews(value => !value)} className={'rounded border px-2 ' + (showNews ? 'border-amber-500/40 text-amber-200' : 'border-slate-700 text-slate-400')}>News</button>
          <button aria-label="Vergrößern" onClick={() => chartRef.current?.zoomAtCoordinate(0.3)} className="p-2"><ZoomIn className="h-4 w-4" /></button>
          <button aria-label="Verkleinern" onClick={() => chartRef.current?.zoomAtCoordinate(-0.3)} className="p-2"><ZoomOut className="h-4 w-4" /></button>
          <button aria-label="Chart zurücksetzen" onClick={() => { chartRef.current?.setBarSpace(7); chartRef.current?.setOffsetRightDistance(210); chartRef.current?.scrollToRealTime(); }} className="p-2"><Maximize2 className="h-4 w-4" /></button>
        </div>
      </div>
      {active && klines.length > 0 && <p className="border-b border-slate-800 px-3 py-2 text-[11px] text-slate-400">
        Aktueller Kurs <strong className="text-slate-200">{price(klines.at(-1)!.close, currency)}</strong>
        {' · '}{decision!.lifecycle_status === 'VORBEREITET' ? 'Geplanter Entry' : 'Ursprünglicher Entry'} {price(decision!.entry_price, currency)}
        {' · Abstand '}{(Math.abs(decision!.entry_price! - klines.at(-1)!.close) / klines.at(-1)!.close * 100).toFixed(2)}%
        {decision!.lifecycle_status === 'VORBEREITET' && ' · Noch kein Einstieg ausgelöst.'}
      </p>}
      <div className="relative h-[480px] md:h-[580px]">
        <div ref={containerRef} className="h-full w-full" />
        {klines.length === 0 && <p className="absolute inset-0 flex items-center justify-center bg-slate-900/90 text-xs text-slate-400">Keine Kursdaten geladen.</p>}
      </div>
      <p className="border-t border-slate-800 px-3 py-2 text-[10px] text-slate-400">
        EMA 9 / 20 / 50 / 100 / 200 · Volumen · N = Nachricht · BUY/SELL = simulierte Einstiege.
        {forecast ? (projection ? ' Cyan: technisches Kursziel ab aktuellem Kurs · Rot: Invalidation der Einschätzung.' : ' Kein aktuelles technisches Kursziel verfügbar.') : active ? (decision!.lifecycle_status === 'VORBEREITET' ? ' Geplanter Entry: Einstieg erst nach Erreichen der Zone und Erfüllen der Bedingung. Grün: Ziele · Rot: Stop-Loss.' : ' Grün: TP1 und TP2 · Rot: Stop-Loss · Projektion ab ursprünglichem Entry.') : ' Kein gültiges Setup: keine Profit-/Stop-Box.'}
      </p>
    </section>
  );
});
