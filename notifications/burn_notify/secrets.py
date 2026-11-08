"""Resolve a destination's secret reference to its value.

Reference forms:
  env:NAME                 local demo only; read from an environment variable
  aws-sm:<secret-id>       Secrets Manager, whole SecretString
  aws-sm:<secret-id>#key   Secrets Manager, one key of a JSON SecretString

Values are cached in memory for a short TTL (rotation is picked up within minutes)
and are never logged, returned in errors, or written to the delivery table.
"""
from __future__ import annotations

import json
import os
import threading
import time


class SecretError(Exception):
    """A secret could not be resolved. Permanent configuration error; message is value-free."""


class SecretResolver:
    def __init__(self, *, ttl_seconds: float = 300.0, client=None, environ=None, allow_env: bool = True):
        self.ttl = ttl_seconds
        self._client = client
        self._environ = os.environ if environ is None else environ
        self._allow_env = allow_env
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def _sm(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("secretsmanager")
        return self._client

    def get(self, ref: str) -> str:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(ref)
            if hit and hit[0] > now:
                return hit[1]
        value = self._fetch(ref)
        with self._lock:
            self._cache[ref] = (now + self.ttl, value)
        return value

    def _fetch(self, ref: str) -> str:
        if not isinstance(ref, str) or ":" not in ref:
            raise SecretError("secret reference must be env:NAME or aws-sm:ID[#key]")
        kind, _, name = ref.partition(":")
        if kind == "env":
            if not self._allow_env:
                raise SecretError("env: secret references are disabled in this deployment")
            value = self._environ.get(name)
            if not value:
                raise SecretError(f"environment variable {name} is not set")
            return value
        if kind == "aws-sm":
            secret_id, _, key = name.partition("#")
            try:
                raw = self._sm().get_secret_value(SecretId=secret_id)["SecretString"]
            except Exception as exc:  # botocore errors: report the class only
                raise SecretError(f"Secrets Manager lookup failed ({type(exc).__name__})") from None
            if not key:
                return raw
            try:
                value = json.loads(raw)[key]
            except (ValueError, KeyError, TypeError):
                raise SecretError("secret JSON does not contain the requested key") from None
            if not isinstance(value, str) or not value:
                raise SecretError("secret key is empty")
            return value
        raise SecretError("unsupported secret reference type")
