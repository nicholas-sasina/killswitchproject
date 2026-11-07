"""Compare normalized tokens and distinct shingles with registered fingerprints."""
from dataclasses import dataclass
import re
from typing import Callable

from burn_contracts.fingerprint import fingerprint, shingle_fingerprint, shingle_windows

from .normalize import TOKENS, candidates, variants
from .window import SessionWindow

_FORMATS = (
    (re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"), "aws_key"),
    (re.compile(r"(?<![A-Za-z0-9_])mpk_live_[A-Za-z0-9]{32}(?![A-Za-z0-9])"), "api_key"),
    (re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/@:]+:[^\s/@]+@[^\s<>\"']+"), "other"),
)


@dataclass(frozen=True)
class Finding:
    secret_ref: str | None
    fingerprint: str
    secret_type: str
    owner: str
    owner_agent_id: str | None
    kind: str
    encoding: str
    coverage: float


def _finding(row: dict, fp: str, kind: str, encoding: str, coverage: float) -> Finding:
    agent = row.get("owner_agent_id")
    owner = "shared" if agent == "shared" else "agent" if agent else "unknown"
    return Finding(row.get("secret_ref"), fp, row.get("secret_type", "other"), owner,
                   agent if owner == "agent" else None, kind, encoding, coverage)


class Scanner:
    def __init__(self, key: bytes, lookup: Callable[[set[str]], dict[str, dict]], *, threshold: float = 0.60):
        if not key or not 0 < threshold <= 1:
            raise ValueError("a key and a threshold in (0, 1] are required")
        self.key = key
        self.lookup = lookup
        self.threshold = threshold

    def scan(self, text: str, window: SessionWindow | None = None) -> list[Finding]:
        exact, shingles, formats = self._collect_candidates(text)
        rows = self._lookup_fingerprints(exact, shingles, formats)
        found = self._exact_matches(rows, exact)
        evidence = self._add_shingle_matches(found, rows, shingles, window)
        self._add_format_only_findings(found, rows, formats, evidence)
        return sorted(found.values(), key=lambda f: (f.secret_ref or "", f.fingerprint, f.kind))

    def _collect_candidates(self, text: str) -> tuple[dict, dict, dict]:
        exact, shingles, formats = {}, {}, {}
        token_cache = {}
        shingle_cache = {}
        window_cache = {}

        def parts(token):
            # Repeated text should not repeat HMAC work; these caches live for one scan.
            if token not in shingle_cache:
                fps = []
                for part in set(shingle_windows(token)):
                    if part not in window_cache:
                        window_cache[part] = shingle_fingerprint(part, self.key)
                    fps.append(window_cache[part])
                shingle_cache[token] = fps
            return shingle_cache[token]
        for value, encoding in variants(text):
            for token in candidates(value):
                if token not in token_cache:
                    token_cache[token] = fingerprint(token, self.key)
                exact.setdefault(token_cache[token], encoding)
            for match in TOKENS.finditer(value):
                lowered = match[0].lower()
                for fp in parts(lowered):
                    shingles.setdefault(fp, encoding)
            for pattern, secret_type in _FORMATS:
                if secret_type == "other" and "://" not in value:
                    continue
                for match in pattern.finditer(value):
                    fp = fingerprint(match[0], self.key)
                    exact.setdefault(fp, encoding)
                    token = match[0].lower()
                    formats.setdefault(fp, (secret_type, encoding, parts(token)))
        return exact, shingles, formats

    def _lookup_fingerprints(self, exact: dict, shingles: dict, formats: dict) -> dict:
        # A lookup can cross the network, so collect every candidate into one batch.
        return self.lookup(set(exact) | set(shingles) | set(formats))

    def _exact_matches(self, rows: dict, exact: dict) -> dict:
        found = {}
        for fp, row in rows.items():
            if row.get("kind") == "exact" and fp in exact:
                found[(row.get("secret_ref"), row.get("part"), row.get("version"))] = _finding(row, fp, "exact", exact[fp], 1.0)
        return found

    def _add_shingle_matches(self, found: dict, rows: dict, shingles: dict, window: SessionWindow | None) -> dict:
        evidence = {}
        for fp, row in rows.items():
            if row.get("kind") == "shingle" and fp in shingles:
                # Parts and versions cannot contribute coverage to one another.
                group = (row.get("secret_ref"), row.get("part"), row.get("version"))
                if group not in evidence:
                    evidence[group] = (row, set(), shingles[fp])
                evidence[group][1].add(int(row["index"]))
        current = evidence
        if window is not None:
            evidence = window.accumulate(evidence)
        exact_secrets = {(group[0], group[2]) for group in found}
        for group, (row, indices, encoding) in evidence.items():
            # Old evidence alone must not block an unrelated follow-up request.
            if group not in current:
                continue
            # Exact attribution already identifies the credential, even for a different part.
            if (group[0], group[2]) in exact_secrets:
                continue
            total = int(row.get("n_shingles", 0))
            if total <= 0:
                continue
            coverage = min(1.0, len(indices) / total)
            if coverage >= self.threshold and group not in found:
                if len(current.get(group, ({}, set(), ""))[1]) / total < self.threshold:
                    encoding = "multi_request"
                found[group] = _finding(row, row["fingerprint"], "shingle", encoding, coverage)
        return evidence

    def _add_format_only_findings(self, found: dict, rows: dict, formats: dict, evidence: dict) -> None:
        for fp, (secret_type, encoding, format_parts) in formats.items():
            if fp in rows or any(f.fingerprint == fp for f in found.values()):
                continue
            attributed = {}
            for part_fp in format_parts:
                row = rows.get(part_fp, {})
                group = (row.get("secret_ref"), row.get("part"), row.get("version"))
                if row.get("kind") == "shingle" and group in found:
                    attributed.setdefault(group, set()).add(int(row["index"]))
            # A recased known credential should retain its owner, not become an unknown format hit.
            if any(len(indices) / int(evidence[group][0]["n_shingles"]) >= self.threshold
                   for group, indices in attributed.items()):
                continue
            found[(None, fp)] = Finding(None, fp, secret_type, "unknown", None, "format", encoding, 1.0)
