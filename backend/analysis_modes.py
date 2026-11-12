"""Only an explicit user action changes the persisted analysis mode."""

from contextlib import closing
from database import get_connection
from analysis_scope import current_scope


class ModeChangedError(RuntimeError):
    pass


class AnalysisBarChangedError(ModeChangedError):
    pass


def get_analysis_mode():
    scoped = current_scope()
    if scoped is not None:
        scoped.guard()
        return scoped.mode
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key='analysis_mode'").fetchone()
        return row[0] if row else "gemini"


def set_analysis_mode(mode):
    if mode not in ("gemini", "python"):
        raise ValueError("Unbekannter Analysemodus.")
    with closing(get_connection()) as connection, connection:
        connection.execute("INSERT INTO app_settings(key,value) VALUES('analysis_mode',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))
    return mode


def ensure_analysis_mode(mode):
    if mode != get_analysis_mode():
        raise ModeChangedError("Analysemodus wurde manuell geändert. Bitte Auswahl aktualisieren.")
