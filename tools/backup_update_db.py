"""Consistent SQLite backup; never copies an active .db file without its journal."""
import argparse
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("source",type=Path)
parser.add_argument("destination",type=Path)
args = parser.parse_args()
if args.source.is_file():
    source = sqlite3.connect(args.source.resolve().as_uri()+"?mode=ro",uri=True,timeout=20)
    destination = sqlite3.connect(args.destination)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print("Konsistente Datenbanksicherung erstellt.")
