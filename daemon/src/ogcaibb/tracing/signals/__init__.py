"""Implicit feedback signal detectors.

Each detector observes some side-effect of a completed turn (git commit,
file retention, clean tool returns, …) and emits a `Signal` after its
configured delay. Signals join explicit ratings in the WAL and ship to
the hub on the same channel.
"""

from .base import ImplicitSignal, SignalContext
from .registry import all_signals, get_signal

__all__ = ["ImplicitSignal", "SignalContext", "all_signals", "get_signal"]
