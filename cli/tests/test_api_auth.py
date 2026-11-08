"""Every request's credentials come from Api.auth_headers, so a caller that authenticates differently overrides one method."""
from burn_cli.api import Api
from fake_servers import Recorder


def test_requests_carry_auth_headers_and_a_subclass_can_replace_them():
    server = Recorder(lambda headers, body: (200, {}))
    try:
        Api(server.url, "demo").request("GET", "/events")

        class Other(Api):
            def auth_headers(self):
                return {"authorization": "Bearer other"}

        Other(server.url, "demo").request("POST", "/roles/w/tokens", {"uses": 1})
    finally:
        server.close()
    first, second = ({k.lower(): v for k, v in s["headers"].items()} for s in server.seen)
    assert first["x-burn-demo-token"] == "demo" and "authorization" not in first
    assert second["authorization"] == "Bearer other" and "x-burn-demo-token" not in second
    assert second["content-type"] == "application/json"
