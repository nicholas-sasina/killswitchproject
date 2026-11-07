"""Frozen evaluation-only forms, introduced before ws/eval policy changes.

Baseline detector: d618897. Never use these results to tune normalization.
This is a separate test corpus, not a claim of an independent security audit.
"""
import base64
import json
import zlib

from .forms import Form, b64

FORMS = [
    Form("heldout_base85", "ASCII base85", lambda v: [base64.b85encode(v.encode()).decode()]),
    Form("heldout_utf16_base64", "UTF-16LE then base64", lambda v: [base64.b64encode(v.encode("utf-16le")).decode()]),
    Form("heldout_zlib_base64", "zlib then base64", lambda v: [base64.b64encode(zlib.compress(v.encode())).decode()]),
    Form("heldout_html_entities", "decimal HTML entities", lambda v: ["".join(f"&#{ord(c)};" for c in v)]),
    Form("heldout_json_chars", "JSON array of individual characters", lambda v: [json.dumps(list(v))]),
    Form("heldout_json_parts", "two JSON fields containing halves", lambda v: [json.dumps({"left": v[:len(v)//2], "right": v[len(v)//2:]})]),
    Form("heldout_base64_lines", "base64 wrapped every seven characters", lambda v: ["\n".join(b64(v)[i:i+7] for i in range(0, len(b64(v)), 7))]),
    Form("heldout_percent_twice", "double percent encoding", lambda v: ["".join(f"%25{ord(c):02x}" for c in v)]),
]
