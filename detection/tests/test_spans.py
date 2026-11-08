"""Finding spans and the canary flag: additive fields that let the Gateway replace a credential."""
import base64
import secrets
import string

from burn_contracts.fingerprint import fingerprint, shingle_fingerprints
from burn_detect import Finding, Scanner

ALNUM = string.ascii_letters + string.digits


def _rand(n):
    return "".join(secrets.choice(ALNUM) for _ in range(n))


def _scanner(*credentials):
    """credentials: (value, body, metadata) tuples registered the way the minter does."""
    key = secrets.token_bytes(32)
    rows = {}
    for value, body, meta in credentials:
        exact = fingerprint(value, key)
        rows[exact] = dict(meta, fingerprint=exact, kind="exact", part="value")
        parts = shingle_fingerprints(body, key)
        for index, fp in parts:
            rows.setdefault(fp, dict(meta, part="random", fingerprint=fp, kind="shingle", index=index,
                                     n_shingles=len(parts)))
    return Scanner(key=key, lookup=lambda fps: {fp: rows[fp] for fp in fps if fp in rows})


def _api_key(agent="agent-a", **extra):
    body = _rand(32)
    meta = {"secret_ref": f"burn/{agent}/api_key", "owner_agent_id": agent, "secret_type": "api_key",
            "version": 1, "status": "active", **extra}
    return "mpk_" + "live_" + body, body, meta


def known(findings):
    return [f for f in findings if f.secret_ref]


def test_plain_match_has_its_exact_span():
    value, body, meta = _api_key()
    text = f"config:\nKEY={value}\nother=1"
    [f] = known(_scanner((value, body, meta)).scan(text))
    assert text[f.start:f.end] == value and f.spans == ((f.start, f.end),)
    assert f.canary is False


def test_every_occurrence_is_located():
    value, body, meta = _api_key()
    text = f"{value} and again {value}."
    [f] = known(_scanner((value, body, meta)).scan(text))
    assert [text[a:b] for a, b in f.spans] == [value, value]


def test_decoded_match_covers_the_whole_encoded_segment():
    value, body, meta = _api_key()
    for encoded in (base64.b64encode(value.encode()).decode(), value.encode().hex(),
                    base64.b64encode(value.encode().hex().encode()).decode()):  # two layers
        text = f"blob: {encoded} end"
        [f] = known(_scanner((value, body, meta)).scan(text))
        assert text[f.start:f.end] == encoded, f.encoding


def test_url_parameter_match_covers_the_url():
    value, body, meta = _api_key()
    url = f"https://example.test/hook?token={value}&x=1"
    text = f"call {url} now"
    found = known(_scanner((value, body, meta)).scan(text))
    assert any(text[a:b] in (value, url) for f in found for a, b in f.spans)


def test_whole_text_transforms_and_fragments_are_not_located():
    value, body, meta = _api_key()
    scanner = _scanner((value, body, meta))
    [reversed_] = known(scanner.scan(f"x {value[::-1]} y"))
    assert reversed_.start is None and reversed_.spans == ()
    [fragment] = known(scanner.scan(f"part: {body[:26]}"))
    assert fragment.kind == "shingle" and fragment.spans == () and fragment.start is None


def test_canary_flag_comes_from_the_fingerprint_row():
    value, body, meta = _api_key(agent="agent-c", canary=True)
    real_value, real_body, real_meta = _api_key(agent="agent-r")
    found = {f.secret_ref: f for f in known(_scanner((value, body, meta), (real_value, real_body, real_meta))
                                            .scan(f"{value} {real_value}"))}
    assert found["burn/agent-c/api_key"].canary is True
    assert found["burn/agent-r/api_key"].canary is False


def test_fields_are_additive():
    old_style = Finding("burn/agent-a/api_key", "f" * 64, "api_key", "agent", "agent-a", "exact", "plain", 1.0)
    assert (old_style.start, old_style.end, old_style.spans, old_style.canary) == (None, None, (), False)


def test_nothing_found_costs_no_locating(monkeypatch):
    value, body, meta = _api_key()
    scanner = _scanner((value, body, meta))
    monkeypatch.setattr(scanner, "_positions", lambda *a: (_ for _ in ()).throw(AssertionError("located")))
    assert scanner.scan("nothing to see here " * 50) == []


def _hex_credential(agent="agent-h"):
    """A credential with no recognizable format, registered whole (exact) plus shingles."""
    value = secrets.token_hex(16)
    meta = {"secret_ref": f"burn/{agent}/token", "owner_agent_id": agent, "secret_type": "other",
            "version": 1, "status": "active"}
    return value, value, meta


def test_key_equals_value_and_trailing_punctuation_are_located():
    value, body, meta = _hex_credential()
    scanner = _scanner((value, body, meta))
    for text in (f"TOKEN={value}\n", f"the token is {value}.", f'"auth":"{value}",', f"({value})"):
        [f] = known(scanner.scan(text))
        assert [text[a:b] for a, b in f.spans] == [value], text


def test_locating_pieces_never_changes_what_is_found():
    value, body, meta = _hex_credential()
    scanner = _scanner((value, body, meta))
    [f] = known(scanner.scan(f"TOKEN={value}"))
    assert f.kind == "shingle"  # same finding as before spans existed; only the location is new
    [fragment] = known(scanner.scan(f"TOKEN={value[:26]}"))
    assert fragment.spans == ()  # a real fragment is still not locatable
