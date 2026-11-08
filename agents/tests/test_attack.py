"""The attacker listener extracts keys, tries each against the provider as an
outsider, and never prints a credential value."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import attack  # noqa: E402

# Built by concatenation so no contiguous literal trips the gitleaks secret gate.
# These are synthetic shapes, not real credentials, and the provider call is
# injected in every test, so nothing is ever presented to a real service.
CANARY_SHAPED = "mpk_live_" + "A" * 32
AWS_SHAPED = "AKIA" + "QWERTYUIOPASDF12"  # AKIA + 16 chars


def test_extract_keys_finds_both_shapes():
    text = f"PROVIDER_API_KEY={CANARY_SHAPED}\nAWS_ACCESS_KEY_ID={AWS_SHAPED}\n"
    assert attack.extract_keys(text) == [CANARY_SHAPED, AWS_SHAPED]


def test_extract_keys_ignores_references_and_prose():
    text = ("PROVIDER_API_KEY={{burn:ref:burn/agent-reader/api_key}}\n"
            "the key was rotated in October, nothing usable here\n"
            "CRM_DB_URL=postgres://svc_crm:{{burn:ref:burn/shared/crm_db_url}}@crm-db.internal:5432/crm\n")
    assert attack.extract_keys(text) == []


def test_extract_keys_dedupes_and_keeps_first_seen_order():
    text = f"{AWS_SHAPED} ... {CANARY_SHAPED} ... {AWS_SHAPED}"
    assert attack.extract_keys(text) == [AWS_SHAPED, CANARY_SHAPED]


def test_fingerprint_is_short_stable_and_not_the_value():
    fp = attack.fingerprint(CANARY_SHAPED)
    assert len(fp) == 12
    assert CANARY_SHAPED not in fp
    assert attack.fingerprint(CANARY_SHAPED) == fp  # stable
    assert attack.fingerprint(AWS_SHAPED) != fp


def test_process_payload_tries_each_key_and_prints_only_fingerprints(capsys):
    tried = []

    def present(url, token):
        tried.append(token)
        return "refused"

    body = f"# pre-rotation backup\nPROVIDER_API_KEY={CANARY_SHAPED}\nAWS=  {AWS_SHAPED}\n"
    results = attack.process_payload(body, "http://provider.example", present=present)

    assert tried == [CANARY_SHAPED, AWS_SHAPED]
    assert results == [("refused", attack.fingerprint(CANARY_SHAPED)),
                       ("refused", attack.fingerprint(AWS_SHAPED))]
    out = capsys.readouterr().out
    assert "refused" in out
    assert attack.fingerprint(CANARY_SHAPED) in out and attack.fingerprint(AWS_SHAPED) in out
    # The whole point: a value never appears in the output.
    assert CANARY_SHAPED not in out and AWS_SHAPED not in out


def test_process_payload_with_no_keys_tries_nothing(capsys):
    calls = []
    results = attack.process_payload("a friendly note, no secrets here", "http://p",
                                     present=lambda u, t: calls.append(t) or "accepted")
    assert results == [] and calls == []
    assert "no key-shaped tokens" in capsys.readouterr().out


def test_present_is_accepted_only_on_200():
    assert attack.present_to_provider("http://p", "tok", get=lambda u, h: 200) == "accepted"
    assert attack.present_to_provider("http://p", "tok", get=lambda u, h: 401) == "refused"


def test_present_uses_whoami_and_bearer_with_no_session_proof():
    seen = {}

    def get(url, headers):
        seen["url"], seen["headers"] = url, headers
        return 401

    attack.present_to_provider("http://provider.example/", "the-token", get=get)
    assert seen["url"] == "http://provider.example/v1/whoami"
    assert seen["headers"] == {"Authorization": "Bearer the-token"}
    assert not any(k.lower() == "x-burn-session-proof" for k in seen["headers"])


def test_reads_leaked_text_from_stdin(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(f"here is the backup\nPROVIDER_API_KEY={CANARY_SHAPED}\n"))
    assert attack.read_leaked("-") == f"here is the backup\nPROVIDER_API_KEY={CANARY_SHAPED}\n"


def test_stdin_pipeline_tries_the_key_and_prints_no_value(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(f"leaked:\n{CANARY_SHAPED}\n"))
    tried = []
    results = attack.process_payload(attack.read_leaked("-"), "http://provider.example",
                                     present=lambda u, t: tried.append(t) or "refused")
    assert tried == [CANARY_SHAPED]
    assert results == [("refused", attack.fingerprint(CANARY_SHAPED))]
    out = capsys.readouterr().out
    assert attack.fingerprint(CANARY_SHAPED) in out and "refused" in out
    assert CANARY_SHAPED not in out  # same no-value guarantee as the listener


def test_listener_binds_loopback_only():
    server = attack.make_server(0, "http://provider.example")  # port 0: an ephemeral port
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert attack.HOST == "127.0.0.1"
    finally:
        server.server_close()
