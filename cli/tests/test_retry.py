import pytest

from burn_cli import retry
from burn_cli.api import Api, ApiError


def test_retries_429_then_succeeds_with_growing_jittered_waits():
    outcomes = [ApiError("x", 429), ApiError("x", 503), ApiError("x", None), "ok"]
    waits = []

    def flaky():
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    assert retry.call(flaky, sleep=waits.append, rng=lambda: 1.0, base=1.0) == "ok"
    assert waits == [1.0, 2.0, 4.0]  # full-jitter ceiling doubles each attempt
    assert retry.call(lambda: 1, sleep=waits.append) == 1


def test_jitter_spreads_callers_below_the_ceiling():
    assert retry.delay(3, base=1.0, rng=lambda: 0.25) == 2.0
    assert retry.delay(30, base=1.0, cap=15.0, rng=lambda: 1.0) == 15.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_client_errors_are_not_retried(status):
    calls = []

    def fail():
        calls.append(1)
        raise ApiError("no", status)

    with pytest.raises(ApiError):
        retry.call(fail, sleep=lambda _: None)
    assert len(calls) == 1


def test_gives_up_after_the_last_attempt_with_the_last_error():
    with pytest.raises(ApiError) as exc:
        retry.call(lambda: (_ for _ in ()).throw(ApiError("busy", 429)), attempts=3, sleep=lambda _: None)
    assert exc.value.status == 429


def test_api_error_carries_status(fake):
    fake.fail[("GET", "/roles")] = [429]
    with pytest.raises(ApiError) as exc:
        Api(fake.url, "wrong").request("GET", "/roles")
    assert exc.value.status == 401 and not exc.value.retryable
    with pytest.raises(ApiError) as exc:
        Api("http://127.0.0.1:1", "t").request("GET", "/roles")
    assert exc.value.status is None and exc.value.retryable
