"""Build reproducible, synthetic portfolio fixtures without network or credentials."""
import json
import math
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from instruments import CATALOG
from technical_analysis import run_technical_analysis

BASES = [180, 280, 150, 220, 440, 210, 175, 620, 85, 250, 580, 510, 2700, 85000, 3200, 160]
END = pd.Timestamp("2026-11-11T18:00:00Z")
fixtures = {}
for i, item in enumerate(CATALOG):
    rng = random.Random(4200 + i)
    base = BASES[i]
    rows = []
    previous = base * .96
    for n in range(300):
        close = base * (.96 + n * .00013 + .008 * math.sin(n / 13 + i) + .003 * math.sin(n / 3))
        close *= 1 + rng.uniform(-.0014, .0014)
        rows.append({"Open": round(previous, 5), "High": round(max(previous, close) * (1 + rng.uniform(.0005, .003)), 5),
                     "Low": round(min(previous, close) * (1 - rng.uniform(.0005, .003)), 5), "Close": round(close, 5),
                     "Volume": rng.randint(100000, 1200000)})
        previous = close
    frame = pd.DataFrame(rows, index=pd.date_range(end=END, periods=len(rows), freq="15min"))
    technical = run_technical_analysis(frame)
    bars = [{"timestamp": int(at.timestamp() * 1000), **{key.lower(): value for key, value in row.items()}}
            for at, row in frame.iterrows()]
    fixtures[item["symbol"]] = {"instrument": item, "klines": bars, "technical": technical}
target = ROOT / "frontend" / "src" / "demo-data.json"
target.write_text(json.dumps(fixtures, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
print(f"Created {len(fixtures)} synthetic instruments ({target.stat().st_size:,} bytes)")
