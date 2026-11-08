"""Opt-in NEGATIVE probes of the real provider endpoints. They cannot deliver anything.

    BURN_NOTIFY_PROBE=1 uv run pytest -m live tests/test_live_probe.py

Each probe sends a request with a deliberately invalid, made-up credential through the
production transport (system DNS, public-address check, pinned TLS with certificate and
hostname verification). The provider's rejection proves the network path and our error
handling work against the real API, without any authorized channel or real credential.
"""
import json
import os

import pytest

from burn_notify import fixtures, incident as inc_mod
from burn_notify.events import parse
from burn_notify.policy import classify
from burn_notify.providers import REGISTRY, DeliveryError, Destination

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("BURN_NOTIFY_PROBE") != "1", reason="set BURN_NOTIFY_PROBE=1")]

FAKE = {  # built at runtime; none of these identify a real workspace, bot, webhook or workflow
    "slack": "https://hooks.slack.com/services/" + "T00000000/B00000000/" + "0" * 24,
    "telegram": "100000:" + "0" * 35,
    "discord": "https://discord.com/api/webhooks/" + "1/" + "0" * 20,
    "teams": ("https://prod-27.westus.logic.azure.com:443/workflows/" + "0" * 32
              + "/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig="
              + "0" * 43),
}
DESTS = {
    "slack": Destination("probe", "slack", secret_ref="env:X", options={"private_channel_confirmed": True}),
    "telegram": Destination("probe", "telegram", recipient="-1", secret_ref="env:X",
                            options={"allowed_chat_ids": ["-1"]}),
    "discord": Destination("probe", "discord", secret_ref="env:X", options={"private_channel_confirmed": True}),
    "teams": Destination("probe", "teams", secret_ref="env:X"),
}


@pytest.fixture(autouse=True)
def no_network():  # these probes are meant to reach the real hosts
    yield


@pytest.mark.parametrize("name", sorted(FAKE))
def test_invalid_credential_is_rejected_by_real_provider(name):
    event = parse(fixtures.canary_used())
    inc = inc_mod.build(event, classify(event), dashboard_url=None, notice="SYNTHETIC PROBE")
    provider = REGISTRY[name]()  # production transport
    with pytest.raises(DeliveryError) as err:
        provider.send(provider.build(inc, DESTS[name]), DESTS[name], FAKE[name])
    # A provider answer (not a network or TLS error) proves DNS, pinning and certificate checks worked.
    assert err.value.category not in ("network_error", "tls_verification_failed", "unsafe_url"), err.value.category
    assert not err.value.retryable
    print(json.dumps({"provider": name, "status": err.value.status, "category": err.value.category}))
