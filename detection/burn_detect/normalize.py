"""Bounded decoding of common credential transports; nothing is logged."""
import base64
import binascii
import json
import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit

TOKENS = re.compile(r"[A-Za-z0-9_+/=.\-]{4,}")
_ENCODED = re.compile(r"(?<![A-Za-z0-9_+/\-])[A-Za-z0-9_+/\-]{8,}={0,2}")
_HEX = re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{8,}(?![A-Fa-f0-9])")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s<>\"']+")
_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2}))")
_SEPARATORS = re.compile(r"[\s.\-:,|]+")
_ALNUM_SEPARATORS = re.compile(r"[\W_]+")
MAX_VARIANTS = 96
MAX_TEXT = 262_144


def _printable(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and all(c.isprintable() or c.isspace() for c in text):
        return text
    return None


def _decoded(text: str):
    decoded = unquote_plus(text)
    if decoded != text:
        yield decoded, "url"
    for match in _URL.finditer(text) if "://" in text else ():
        try:
            for _, value in parse_qsl(urlsplit(match[0]).query, keep_blank_values=True):
                yield value, "url"
        except ValueError:
            continue
    if "\\" in text:
        try:
            value = json.loads(text)
            if isinstance(value, str):
                yield value, "json"
        except (ValueError, RecursionError):
            pass
        def unescape(match):
            try:
                value = chr(int(next(g for g in match.groups() if g), 16))
                return value if not 0xD800 <= ord(value) <= 0xDFFF else match[0]
            except ValueError:
                return match[0]
        value = _ESCAPE.sub(unescape, text)
        if value != text:
            yield value, "unicode"
    for match in _HEX.finditer(text):
        if len(match[0]) % 2 == 0:
            value = _printable(bytes.fromhex(match[0]))
            if value:
                yield value, "hex"
    for match in _ENCODED.finditer(text):
        token = match[0]
        if len(token) % 4 == 1:
            continue
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            continue
        value = _printable(raw)
        if value:
            yield value, "base64"


def variants(text: str) -> list[tuple[str, str]]:
    """Decode at most two transport layers, then undo separators and reversal."""
    if len(text) > MAX_TEXT:
        raise ValueError("payload exceeds scanner size limit")
    result = [(text, "plain")]
    seen = {text}
    frontier = result[:]
    for _ in range(2):
        following = []
        for value, parent in frontier:
            for decoded, encoding in _decoded(value):
                if decoded not in seen:
                    label = encoding if parent == "plain" else parent + "+" + encoding
                    seen.add(decoded)
                    following.append((decoded, label))
                    result.append((decoded, label))
                    if len(result) >= MAX_VARIANTS // 4:
                        break
            if len(result) >= MAX_VARIANTS // 4:
                break
        frontier = following
        if not frontier or len(result) >= MAX_VARIANTS // 4:
            break
    for value, parent in result[:]:
        for transformed, encoding in ((_SEPARATORS.sub("", value), "separators"),
                (_ALNUM_SEPARATORS.sub("", value), "separators"), (value[::-1], "reversed")):
            if transformed not in seen:
                seen.add(transformed)
                result.append((transformed, encoding if parent == "plain" else parent + "+" + encoding))
        # Shingles are lowercased by the contracts helper, without another variant.
    return result[:MAX_VARIANTS]


def candidates(text: str) -> set[str]:
    urls = (m[0] for m in _URL.finditer(text)) if "://" in text else ()
    return {text.strip(), *(match[0] for match in TOKENS.finditer(text)), *urls} - {""}
