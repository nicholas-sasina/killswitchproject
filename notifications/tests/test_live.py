"""Opt-in live delivery to AUTHORIZED TEST CHANNELS only. Skipped by default.

    BURN_NOTIFY_LIVE=1 BURN_NOTIFY_CONFIG=/path/to/test-config.json \
      uv run pytest -m live tests/test_live.py

The config must name test destinations; their secret_refs point at env vars or
Secrets Manager. Each test sends one synthetic, clearly-labelled alert.
"""
import os
from dataclasses import replace

import pytest

from burn_notify import config as config_mod, fixtures
from burn_notify.processor import Processor
from burn_notify.store import MemoryStore

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("BURN_NOTIFY_LIVE") != "1" or not os.environ.get("BURN_NOTIFY_CONFIG"),
                                 reason="live tests need BURN_NOTIFY_LIVE=1 and BURN_NOTIFY_CONFIG")]


@pytest.fixture(autouse=True)
def no_network():  # overrides the conftest guard: these tests are meant to reach the providers
    yield


def _destinations():
    if os.environ.get("BURN_NOTIFY_LIVE") != "1" or not os.environ.get("BURN_NOTIFY_CONFIG"):
        return []
    return [d.id for d in config_mod.load().destinations if d.enabled]


@pytest.mark.parametrize("dest_id", _destinations())
def test_live_canary_alert(dest_id):
    cfg = config_mod.load()
    dest = replace(cfg.destination(dest_id), min_severity="info")
    proc = Processor(replace(cfg, destinations=(dest,)), MemoryStore())
    result = proc.process_event(fixtures.canary_used())
    assert [o.status for o in result.outcomes] == ["delivered"], result.outcomes
