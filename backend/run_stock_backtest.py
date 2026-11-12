"""Reproducible 5m/15m stock-only replay from frozen real app price snapshots."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd

from market_data import candle_end
from technical_backtest import Costs, EuroRates, Replay, simulate_portfolio, strategy_fingerprint

ROOT = Path(__file__).resolve().parent.parent


def load_frame(path, timeframe):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if payload['timeframe'] != timeframe:
        raise ValueError('Snapshot timeframe mismatch.')
    frame = pd.DataFrame(payload['klines']).rename(columns=str.title)
    frame.index = pd.to_datetime(frame.pop('Timestamp'), unit='ms', utc=True)
    frame.attrs = {'timeframe': timeframe, 'currency': payload['currency'],
                   'exchange_timezone': payload['exchange_timezone'],
                   'instrument_type': 'CURRENCY' if payload['ticker'] == 'EURUSD=X' else 'EQUITY'}
    cutoff = pd.Timestamp(payload['snapshot_at'])
    ends = [candle_end(timestamp, timeframe, frame.attrs) for timestamp in frame.index]
    frame = frame.loc[[end <= cutoff for end in ends]].copy()
    return frame


def hashes(directory, names):
    return {str(name): hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def build_symbol_cache(job):
    symbol, data_dir, output_dir, start, end = job
    data_hash = hashes(data_dir, [f'{symbol}_{tf}.json' for tf in ('1m','5m','15m')])
    code_hash = strategy_fingerprint()
    key = {'data_hash': data_hash, 'code_hash': code_hash, 'start': start.isoformat(), 'end': end.isoformat()}
    replay = None
    for timeframe in ('5m','15m'):
        path = output_dir / f'candidates_{symbol}_{timeframe}.json'
        cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if cached.get('key') != key:
            if replay is None:
                raw = {tf: load_frame(data_dir / f'{symbol}_{tf}.json', tf) for tf in ('1m','5m','15m')}
                replay = Replay(raw)
            candidates,diagnostics = replay.candidates(symbol,timeframe,start=start,end=end)
            path.write_text(json.dumps({'key':key,'candidates':candidates,'diagnostics':diagnostics},allow_nan=False),encoding='utf-8')
            print(f'REPLAY {timeframe} {symbol}: {diagnostics.get("evaluated_bars",0)} bars; {len(candidates)} eligible',flush=True)
    return symbol


def write_report(reports, individual, metadata, destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {'metadata': metadata, 'portfolios': reports, 'individual': individual}
    (destination / 'results.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    for result in reports:
        label = result['timeframe']
        (destination / f'portfolio_{label}.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        with (destination / f'trades_{label}.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            trades = result['trades']
            fields = list(trades[0]) if trades else ['ticker','type','entry_time','time','entry_price','exit_price','pnl','reason']
            writer = csv.DictWriter(handle, fieldnames=fields+['entry_utc','exit_utc'])
            writer.writeheader()
            for trade in trades:
                writer.writerow({**trade,'entry_utc':pd.Timestamp(trade['entry_time'],unit='ms',tz='UTC').isoformat(),
                                 'exit_utc':pd.Timestamp(trade['time'],unit='ms',tz='UTC').isoformat()})
    for result in individual:
        ticker = result['symbols'][0]
        (destination / f'{ticker}_{result["timeframe"]}.json').write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    lines = [
        '# Aktien-Backtest: aktuelle Python-Technik, nur TP1', '',
        f"Stand: {metadata['created_at']}",
        f"Zeitraum: {metadata['start']} bis {metadata['end']} (UTC).", '',
        'Je Timeframe ein unabhängiges Konto mit 5.000 EUR; höchstens eine offene Aktie insgesamt.',
        'Aktien: ' + ', '.join(metadata['symbols']) + '.',
        ('Käufe und modellhafte Leerverkäufe.' if reports[0]['allow_short'] else 'Nur Aktienkäufe; SELL-Signale werden nicht als Leerverkäufe gehandelt.'), '',
        '| Chart | Endkapital EUR | Netto EUR | Rendite | Trades | Gewinnquote | Profit Factor | Max. Drawdown |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for result in reports:
        pf = f"{result['profit_factor']:.3f}" if result['profit_factor'] is not None else 'nicht definiert'
        lines.append(f"| {result['timeframe']} | {result['final_capital']:.2f} | {result['final_capital']-5000:.2f} | {result['total_return_pct']:.3f}% | {result['total_trades']} | {result['win_rate']:.2f}% | {pf} | {result['max_drawdown_pct']:.3f}% |")
    lines += ['', '## Wie häufig wurde ein Szenario freigegeben?', '',
              'Zählung je Aktie und abgeschlossener Hauptkerze; ausschließlich nach vollständigem Warmup und während des Marktes.', '',
              '| Chart | Prüfungen | Gruppen uneinig/neutral | Kein geeignetes Ziel | POI blockiert | Chance/Risiko ungenügend | Freigegeben BUY/SELL |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for result in reports:
        counts=Counter()
        for diagnostics in result['diagnostics'].values(): counts.update(diagnostics)
        lines.append(f"| {result['timeframe']} | {counts['evaluated_bars']} | {counts['UNCLEAR']} | {counts['NO_TARGET']} | {counts['POI_BLOCKED']} | {counts['RISK_REWARD']} | {counts['eligible_signals']} |")
    lines.append('')
    for result in reports:
        eligible=sum(d.get('eligible_signals',0) for d in result['diagnostics'].values())
        skipped=result['execution_rejections'].get('sell_skipped_long_only',0)
        lines += [f"{result['timeframe']}: {eligible} freigegebene BUY-/SELL-Szenarien; {result['total_trades']} ausgeführte Trades; {skipped} SELL-Szenarien wegen Kaufmodus übersprungen.", '']
    if max(result['total_trades'] for result in reports) <= 1:
        lines += ['Höchstens ein ausgeführter Trade reicht nicht aus, um Trefferquote, Erwartungswert oder Profitabilität belastbar zu schätzen. Die Zahlen sind nur die beobachtete Stichprobe.', '']
    for result in reports:
        for trade in result['trades']:
            entry=pd.Timestamp(trade['entry_time'],unit='ms',tz='UTC').isoformat()
            exit_at=pd.Timestamp(trade['time'],unit='ms',tz='UTC').isoformat()
            lines += [f"Ausgeführter Trade {trade['ticker']} ({result['timeframe']}): {trade['type']} am {entry}, Einstieg {trade['entry_price']:.6f} USD, TP1 {trade['take_profit_1']:.6f} USD, Stop {trade['stop_loss']:.6f} USD. Ausgang {trade['reason']} am {exit_at} zu {trade['exit_price']:.6f} USD nach Slippage/Spread; netto {trade['pnl']:.2f} EUR.", '']
    lines += ['', '## Ausführung und Grenzen', '', reports[0]['costs'], '', reports[0]['assumptions'], '',
              'Bei gleichzeitig handelbaren Signalen gewinnt die höchste technische Signalstärke; Gleichstand nach Aktiensymbol. Es wird kein Kapital parallel mehrfach eingesetzt.', '',
              'Die Konten der beiden Timeframes sind getrennte Alternativen, kein gemeinsam finanzierbares 10.000-EUR-Portfolio.', '',
              'Dies ist ein Replay einmal je abgeschlossener Hauptkerze. Die Originalfunktionen für Indikatoren, Prognosen und Planvalidierung werden verwendet; zusätzliche Live-Neuberechnungen innerhalb einer Hauptkerze werden nicht rekonstruiert.', '',
              'Der Zeitraum ist durch die 1m-Daten begrenzt. Das ist eine erste Diagnose und kein belastbarer Nachweis einer profitablen Strategie.',
              'COIN bezeichnet die Coinbase-Aktie; es wurde keine Kryptowährung gehandelt. EURUSD wurde ausschließlich zur Euro-Abrechnung verwendet.', '',
              '## Gründe ohne Trade-Freigabe', '']
    for result in reports:
        lines += [f"### {result['timeframe']}", '', '```json', json.dumps(result['diagnostics'], indent=2, ensure_ascii=False), '```', '',
                  'Ausführung/Positionsbegrenzung:', '```json', json.dumps(result['execution_rejections'], indent=2), '```', '']
    lines += ['## Separate Einzelaktien-Tests', '',
              'Jede Zeile startet unabhängig mit 5.000 EUR. Diese Ergebnisse dürfen nicht zum gemeinsamen Konto addiert werden.', '',
              '| Aktie | Chart | Trades | Netto EUR | Rendite | Max. Drawdown |', '|---|---|---:|---:|---:|---:|']
    for result in individual:
        lines.append(f"| {result['symbols'][0]} | {result['timeframe']} | {result['total_trades']} | {result['final_capital']-5000:.2f} | {result['total_return_pct']:.3f}% | {result['max_drawdown_pct']:.3f}% |")
    (destination / 'BERICHT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    # Standard vector plot, standalone and usable without additional plotting packages.
    colors = {'5m': '#38bdf8', '15m': '#a78bfa'}
    values = [point['equity'] for result in reports for point in result['equity_curve']] + [5000]
    low, high = min(values), max(values)
    padding = max((high-low)*.12, 1.)
    low, high = low-padding, high+padding
    begin = pd.Timestamp(metadata['start']).timestamp()*1000
    finish = pd.Timestamp(metadata['end']).timestamp()*1000
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="440" viewBox="0 0 1000 440">',
           '<rect width="1000" height="440" fill="#0f172a"/>',
           '<text x="75" y="35" fill="white" font-family="sans-serif" font-size="20">Aktien-Backtest · 5.000 EUR · TP1</text>']
    for i in range(5):
        value = low+(high-low)*i/4
        y = 360-(value-low)/(high-low)*280
        svg += [f'<path d="M75 {y:.2f}H950" stroke="#334155"/>',
                f'<text x="10" y="{y+4:.2f}" fill="#cbd5e1" font-family="sans-serif" font-size="12">{value:.0f} EUR</text>']
    for result in reports:
        points = ' '.join(f"{75+(p['timestamp']-begin)/(finish-begin)*875:.2f},{360-(p['equity']-low)/(high-low)*280:.2f}" for p in result['equity_curve'])
        svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[result["timeframe"]]}" stroke-width="2"/>')
    svg += [f'<text x="75" y="400" fill="#38bdf8" font-family="sans-serif">5 Minuten</text>',
            '<text x="220" y="400" fill="#a78bfa" font-family="sans-serif">15 Minuten</text>',
            '<text x="500" y="400" fill="#cbd5e1" font-family="sans-serif">Zeitachse UTC; Übernachtlücken enthalten</text>', '</svg>']
    (destination / 'kapitalverlauf.svg').write_text('\n'.join(svg), encoding='utf-8')


def run(data_dir, output_dir, *, allow_short=False):
    initial_fingerprint = strategy_fingerprint()
    manifest = json.loads((data_dir / 'manifest.json').read_text(encoding='utf-8'))
    symbols = manifest['symbols']
    # Explicit whitelist from the project: no ETF, future, FX or cryptocurrency trades.
    from instruments import CATALOG
    permitted = {item['symbol'] for item in CATALOG if item['type'] == 'EQUITY'}
    if not set(symbols) <= permitted:
        raise ValueError('Only catalog stocks are permitted.')
    raw = {}
    for symbol in symbols:
        raw[symbol] = {tf: load_frame(data_dir / f'{symbol}_{tf}.json', tf) for tf in ('1m', '5m', '15m')}
    start = max(candle_end(frames['1m'].index[199], '1m', frames['1m'].attrs) for frames in raw.values())
    end = min(candle_end(frames['1m'].index[-1], '1m', frames['1m'].attrs) for frames in raw.values())
    if start >= end:
        raise ValueError('No shared post-warmup execution history.')
    fx = load_frame(data_dir / 'EURUSD=X_1D.json', '1D')
    rates = EuroRates(pd.Series(fx.Close.to_numpy(), index=fx.index + pd.Timedelta(days=1)))
    costs, reports, individual = Costs(), [], []
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(symbol,data_dir,output_dir,start,end) for symbol in symbols]
    # Cached forecasts may be prepared in independent processes. No background
    # service or interprocess pipe is needed; the default also runs sequentially.
    for job in jobs:
        build_symbol_cache(job)
    for timeframe in ('5m', '15m'):
        candidates, diagnostics = [], {}
        for symbol in symbols:
            cache_path = output_dir / f'candidates_{symbol}_{timeframe}.json'
            data_hash = hashes(data_dir, [f'{symbol}_{tf}.json' for tf in ('1m', '5m', '15m')])
            code_hash = strategy_fingerprint()
            key = {'data_hash': data_hash, 'code_hash': code_hash, 'start': start.isoformat(), 'end': end.isoformat()}
            cached = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
            if cached.get('key') == key:
                proposed, counts = cached['candidates'], cached['diagnostics']
            else:
                proposed, counts = Replay(raw[symbol]).candidates(symbol, timeframe, start=start, end=end)
                output_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({'key': key, 'candidates': proposed, 'diagnostics': counts}, allow_nan=False), encoding='utf-8')
            candidates.extend(proposed)
            diagnostics[symbol] = counts
            print(f'{timeframe:3} {symbol:5}: {counts.get("evaluated_bars",0):3} bars, {len(proposed):3} eligible', flush=True)
        executions = {symbol: frames['1m'] for symbol, frames in raw.items()}
        common = dict(rates=rates, timeframe=timeframe, start=start, end=end,
                      initial_capital=5000., allow_short=allow_short, costs=costs)
        report = simulate_portfolio(executions, candidates, diagnostics=diagnostics, **common)
        reports.append(report)
        for symbol in symbols:
            individual.append(simulate_portfolio({symbol: executions[symbol]},
                [candidate for candidate in candidates if candidate['ticker'] == symbol],
                diagnostics={symbol: diagnostics[symbol]}, **common))
        print(f'PORTFOLIO {timeframe}: EUR {report["final_capital"]:.2f}; {report["total_trades"]} trades; {report["total_return_pct"]:.3f}%', flush=True)
    source_names = ['technical_backtest.py','run_stock_backtest.py','technical_analysis.py','technical_forecast.py',
                    'technical_strategy.py','timeframe_analysis.py','decision_engine.py','instruments.py','market_data.py']
    if strategy_fingerprint() != initial_fingerprint:
        raise RuntimeError('Strategy source changed during replay; do not publish mixed-version results.')
    metadata = {'created_at': pd.Timestamp.now(tz='UTC').isoformat(), 'symbols': symbols,
                'start': start.isoformat(), 'end': end.isoformat(),
                'code_sha256': hashes(Path(__file__).parent, source_names),
                'data_sha256': hashes(data_dir, [path.name for path in data_dir.glob('*.json')]),
                'data_source': manifest['source'], 'warmup_bars': 200,
                'parameter_search': False, 'candidate_selection': 'strength descending, ticker ascending'}
    write_report(reports, individual, metadata, output_dir)
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT / 'backtest_data')
    parser.add_argument('--output', type=Path, default=ROOT / 'backtest_results')
    parser.add_argument('--allow-short', action='store_true')
    parser.add_argument('--prepare-symbol', nargs='+')
    args = parser.parse_args()
    if args.prepare_symbol:
        manifest = json.loads((args.data / 'manifest.json').read_text(encoding='utf-8'))
        one_minute = {symbol:load_frame(args.data / f'{symbol}_1m.json','1m') for symbol in manifest['symbols']}
        start = max(frame.index[199]+pd.Timedelta(minutes=1) for frame in one_minute.values())
        end = min(frame.index[-1]+pd.Timedelta(minutes=1) for frame in one_minute.values())
        args.output.mkdir(parents=True,exist_ok=True)
        for symbol in args.prepare_symbol:
            if symbol not in manifest['symbols']:
                raise ValueError('Stock is not in this frozen dataset.')
            build_symbol_cache((symbol,args.data,args.output,start,end))
    else:
        run(args.data, args.output, allow_short=args.allow_short)
