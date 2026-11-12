import React, { useEffect, useRef, memo } from 'react';

interface TradingViewWidgetProps {
  ticker: string;
  timeframe: string;
}

export const TradingViewWidget: React.FC<TradingViewWidgetProps> = memo(({ ticker, timeframe }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const element = containerRef.current;
    element.innerHTML = '';

    // Format symbol for TradingView
    let tvSymbol = ticker.trim().toUpperCase();
    if (tvSymbol === 'BTC-USD' || tvSymbol === 'BTC') {
      tvSymbol = 'BINANCE:BTCUSDT';
    } else if (tvSymbol === 'ETH-USD' || tvSymbol === 'ETH') {
      tvSymbol = 'BINANCE:ETHUSDT';
    } else if (tvSymbol === 'SOL-USD' || tvSymbol === 'SOL') {
      tvSymbol = 'BINANCE:SOLUSDT';
    } else if (tvSymbol === 'XAUUSD') {
      tvSymbol = 'OANDA:XAUUSD';
    }

    const widgetDiv = document.createElement('div');
    widgetDiv.className = 'tradingview-widget-container__widget';
    widgetDiv.style.height = '100%';
    widgetDiv.style.width = '100%';
    containerRef.current.appendChild(widgetDiv);

    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: ({ '1m': '1', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '4h': '240', '1D': 'D' } as Record<string, string>)[timeframe] || '15',
      timezone: 'Europe/Vienna',
      theme: 'dark',
      style: '1',
      locale: 'en',
      enable_publishing: false,
      allow_symbol_change: false,
      calendar: false,
      hide_top_toolbar: false,
      hide_side_toolbar: false,
      withdateranges: true,
      save_image: true,
      studies: [],
      support_host: 'https://www.tradingview.com'
    });

    containerRef.current.appendChild(script);

    return () => {
      element.innerHTML = '';
    };
  }, [ticker, timeframe]);

  return (
    <div className="relative w-full h-[620px] bg-[#0f172a] rounded-xl border border-slate-800 overflow-hidden shadow-2xl flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-800 text-xs text-slate-300">
        <span className="font-bold flex items-center gap-1.5 text-cyan-400">
          <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
          TradingView ({ticker} · {timeframe})
        </span>
        <span className="text-[11px] text-slate-400">
          Eigener Datenfeed; Gold hier als Spot-XAUUSD. Trade-Boxen im AI Chart.
        </span>
      </div>
      <div className="flex-1 w-full min-h-0 relative">
        <div ref={containerRef} className="tradingview-widget-container h-full w-full" />
      </div>
    </div>
  );
});

export default TradingViewWidget;
