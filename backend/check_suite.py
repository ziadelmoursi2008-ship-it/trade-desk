"""Run the existing suite in an isolated, Windows-accessible temp workspace."""
from pathlib import Path
import json
import logging
import os
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parent.parent
temp_root = ROOT / 'test-temp' / 'suite'
temp_root.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(temp_root)

# Python 3.13 mkdtemp(mode=0700) removes the Windows sandbox's inherited access.
# Normal directory creation keeps inherited access for our own isolated test files.
def inherited_mkdtemp(suffix=None, prefix=None, dir=None):
    parent = Path(dir) if dir else temp_root
    if not parent.resolve().is_relative_to(temp_root.resolve()):
        raise ValueError('Test temp directories must stay inside the isolated test workspace.')
    target = parent / f'{prefix or "tmp"}{uuid.uuid4().hex}{suffix or ""}'
    target.mkdir()
    return str(target)

tempfile.mkdtemp = inherited_mkdtemp
logging.disable(logging.CRITICAL)
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_*.py')
with (ROOT / 'test-results.txt').open('w',encoding='utf-8') as log:
    result = unittest.TextTestRunner(stream=log,verbosity=1).run(suite)
summary = {'run': result.testsRun, 'failures':len(result.failures),'errors':len(result.errors),
           'skipped':len(result.skipped),'successful':result.wasSuccessful(),
           'issues': [{'test':str(test),'traceback':detail} for test,detail in result.errors+result.failures]}
(ROOT/'test-results.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print({key:value for key,value in summary.items() if key != 'issues'})
for issue in summary['issues']:
    print(issue['test'],issue['traceback'].splitlines()[-1])
raise SystemExit(0 if result.wasSuccessful() else 1)
