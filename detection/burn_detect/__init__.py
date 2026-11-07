"""Stdlib-only credential detection shared by the gateway and exit check."""
from .scanner import Finding, Scanner
from .window import SessionWindow

__all__ = ["Finding", "Scanner", "SessionWindow"]
