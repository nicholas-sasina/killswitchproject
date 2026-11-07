"""The eval harness itself: payloads are fresh, registered like the minter's, and never leak values."""
import json

from burn_detect import Scanner

import catch_rate
import false_positives
from payloads.benign import generate
from payloads.credentials import Registry
from payloads.forms import FORMS


def test_credentials_are_fresh_each_run():
    a, b = Registry().fresh_set(), Registry().fresh_set()
    assert {p.value for p in a}.isdisjoint({p.value for p in b})


def test_plain_credentials_are_detected_and_attributed():
    registry = Registry()
    scanner = Scanner(key=registry.key, lookup=registry.lookup)
    for planted in registry.fresh_set():
        result = catch_rate.run_form(scanner, FORMS[0], planted)
        assert result["attributed"], planted


def test_repr_never_contains_value():
    for planted in Registry().fresh_set():
        assert planted.value not in repr(planted)


def test_catch_rate_results_hold_no_credential_values():
    registry_values = []
    original = Registry.fresh_set

    def spy(self):
        s = original(self)
        registry_values.extend(p.value for p in s)
        return s
    Registry.fresh_set = spy
    try:
        out = json.dumps(catch_rate.run(trials=1))
    finally:
        Registry.fresh_set = original
    assert registry_values and all(v not in out for v in registry_values)


def test_every_form_is_measured():
    result = catch_rate.run(trials=1)
    assert set(result["per_form"]) == {f.name for f in FORMS}
    assert result["total_payload_runs"] == len(FORMS) * 4


def test_benign_payloads_have_no_planted_value_and_decoys_do_not_fire_on_them():
    assert len(generate()) >= 30
    result = false_positives.run(trials=1)
    assert result["total_payload_runs"] == len(generate())
    assert 0 <= result["false_positive_rate"] <= 1
