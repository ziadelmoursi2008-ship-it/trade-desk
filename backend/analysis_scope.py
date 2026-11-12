"""Thread-local background selection; never rewrites the user's UI mode/toggle."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AnalysisScope:
    mode: str
    revision: int
    guard: Callable
    before_api_call: Callable


_scope = ContextVar("background_analysis_scope", default=None)


def current_scope():
    return _scope.get()


@contextmanager
def background_analysis(mode, revision, guard, before_api_call):
    token = _scope.set(AnalysisScope(mode, revision, guard, before_api_call))
    try:
        guard()
        yield
    finally:
        _scope.reset(token)
