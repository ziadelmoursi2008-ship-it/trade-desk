"""Verify a real installed CPU model before applying the update. No production DB writes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent.parent / "backend"))
import lightgbm
from test_lightgbm import NativeTrainingTests

result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeTrainingTests))
if not result.wasSuccessful() or result.skipped:
    raise SystemExit(1)
print("LightGBM",lightgbm.__version__,"CPU-Training und Modell-Wiederherstellung erfolgreich.")
