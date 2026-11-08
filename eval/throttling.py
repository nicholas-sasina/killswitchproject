"""Bounded HTTP evidence. Never retain response bodies, tokens or request ids."""
ERROR_TYPES = frozenset({"ThrottlingException", "TooManyRequestsException", "LimitExceededException",
                         "ServiceQuotaExceededException", "RequestLimitExceeded"})


def response_evidence(response):
    if response.status_code != 429:
        return None
    candidates = [response.headers.get("x-amzn-errortype", "").split(":", 1)[0]]
    try:
        body = response.json()
        if isinstance(body, dict):
            candidates += [body.get(k) for k in ("__type", "code", "Code", "type")]
    except ValueError:
        pass
    code = next((v.rsplit("#", 1)[-1] for v in candidates
                 if isinstance(v, str) and v.rsplit("#", 1)[-1] in ERROR_TYPES), "unidentified")
    return {"endpoint": "AgentCore Gateway", "http_status": 429, "error_type": code}
