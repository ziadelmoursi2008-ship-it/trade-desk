"""Explicit Windows stop command: persist OFF before terminating our backend."""
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--project",type=Path,required=True)
args = parser.parse_args()
backend = args.project.resolve()/"backend"
if not (backend/"background_settings.py").is_file():
    raise SystemExit("Hintergrundmodul im angegebenen Projekt fehlt.")
sys.path.insert(0,str(backend))
from dotenv import load_dotenv
load_dotenv(backend/".env")
from background_settings import settings, save_settings
selected = settings()
selected.pop("revision",None)
selected["enabled"] = False
save_settings(selected)
print("Dauerbetrieb ausgeschaltet und gespeichert.")
