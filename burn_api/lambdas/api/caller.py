"""Who the current request is from, so routes can record it.

auth.authenticate() decides it once per request; the handler sets it here for the length of the request.
Events take their `actor` from it and new rows their `created_by`. A context variable, so concurrent
requests in tests cannot see each other's caller; worker threads (scoped burns) get a copy of it.
"""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Caller:
    actor: str | None   # ledger `actor`: a person's email, "admin", "instance:<session_id>", or None (a join)
    person: str | None  # the signed-in person's email, or None


NOBODY = Caller(actor=None, person=None)
_current: ContextVar[Caller] = ContextVar("burn_caller", default=NOBODY)


def get() -> Caller:
    return _current.get()


def use(who: Caller):
    """Set the caller; returns a token for reset()."""
    return _current.set(who)


def reset(token) -> None:
    _current.reset(token)
