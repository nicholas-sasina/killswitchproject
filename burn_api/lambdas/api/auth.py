"""Identity extracted exclusively from API Gateway HTTP API JWT authorizer claims."""


def signed_in_user(event: dict) -> dict | None:
    claims = (event.get("requestContext", {}).get("authorizer", {})
              .get("jwt", {}).get("claims", {}))
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return None
    return {"sub": subject, "username": claims.get("username") or claims.get("cognito:username") or subject}
