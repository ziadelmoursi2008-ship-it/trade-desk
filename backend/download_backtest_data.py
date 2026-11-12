"""Snapshot the running app's read-only price endpoint. Never invoke AI analysis."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from instruments import CATALOG


def download(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    symbols = [item['symbol'] for item in CATALOG if item['type'] == 'EQUITY']
    jobs = [(symbol, tf) for symbol in symbols for tf in ('1m', '5m', '15m')]
    jobs.append(('EURUSD=X', '1D'))  # Accounting conversion only, never a traded asset.

    def fetch(job):
        symbol, timeframe = job
        target = destination / f'{symbol}_{timeframe}.json'
        response = requests.get('http://127.0.0.1:8000/api/klines',
                                params={'ticker': symbol, 'timeframe': timeframe}, timeout=40)
        response.raise_for_status()
        payload = response.json()
        if not payload.get('klines'):
            raise ValueError(f'No real price data: {symbol} {timeframe}')
        payload['snapshot_at'] = pd.Timestamp.now(tz='UTC').isoformat()
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        print(f'{symbol:9} {timeframe:3} {len(payload["klines"]):5} bars', flush=True)
        return str(target)

    with ThreadPoolExecutor(max_workers=3) as executor:
        files = list(executor.map(fetch, jobs))
    (destination / 'manifest.json').write_text(json.dumps({
        'symbols': symbols, 'files': files, 'source': 'Local app /api/klines, Yahoo Finance',
        'created_at': pd.Timestamp.now(tz='UTC').isoformat(),
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    download(Path(__file__).resolve().parent.parent / 'backtest_data')
