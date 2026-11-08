"""Requirements 1-5: payload formats and provider error handling (mocked HTTP only)."""
import json

import pytest

from burn_notify import fixtures, incident as inc_mod
from burn_notify.events import parse
from burn_notify.http import Response
from burn_notify.policy import classify
from burn_notify.providers import DeliveryError, Destination
from burn_notify.providers.discord import DiscordProvider
from burn_notify.providers.email import EmailProvider
from burn_notify.providers.slack import SlackProvider
from burn_notify.providers.teams import TeamsProvider
from burn_notify.providers.telegram import TelegramProvider

from conftest import CHAT, DISCORD_URL, SLACK_URL, TEAMS_URL, TELEGRAM_TOKEN, FakeSES, FakeTransport


def incident(name="canary_used", **kw):
    event = parse(fixtures.ALL[name](**kw))
    return inc_mod.build(event, classify(event), dashboard_url="https://burn.example.com/#/alerts")


def hostile_incident():
    from dataclasses import replace
    return replace(incident(), agent_id="agent-x", session_id="<!here> @everyone <@U1>",
                   summary="Ping @here <!channel> [click](https://evil.example) *bold* <b>x</b> `cmd`")


SLACK = Destination("slack-sec", "slack", secret_ref="env:SLACK", options={"private_channel_confirmed": True})
DISCORD = Destination("discord-sec", "discord", secret_ref="env:DISCORD", options={"private_channel_confirmed": True})
TEAMS = Destination("teams", "teams", secret_ref="env:TEAMS")
TG = Destination("tg", "telegram", recipient=CHAT, secret_ref="env:TELEGRAM", options={"allowed_chat_ids": [CHAT]})
EMAIL = Destination("email", "email", recipient="secops@example.com, lead@example.com",
                    options={"from_address": "burn@example.com"})


# ---- Slack -------------------------------------------------------------------------------
def test_slack_block_kit_payload():
    body = SlackProvider(FakeTransport()).build(incident(), SLACK).body
    assert body["blocks"][0] == {"type": "header", "text": {"type": "plain_text",
                                                            "text": "Burn security alert — HIGH", "emoji": False}}
    fields = [f["text"] for b in body["blocks"] if b["type"] == "section" and "fields" in b for f in b["fields"]]
    assert all(len(b.get("fields", [])) <= 10 for b in body["blocks"])
    joined = "\n".join(fields)
    for needle in ("Affected instance", "agent-synthetic-000000", "Containment", "Awaiting verified outcome", "Time",
                   "Event ID"):
        assert needle in joined
    button = next(b for b in body["blocks"] if b["type"] == "actions")["elements"][0]
    assert button["url"] == "https://burn.example.com/#/alerts"
    assert body["unfurl_links"] is False


def test_slack_escapes_mentions_and_links():
    body = json.dumps(SlackProvider(FakeTransport()).build(hostile_incident(), SLACK).body)
    assert "<!here>" not in body and "<!channel>" not in body and "<@U1>" not in body
    assert "&lt;!here&gt;" in body


def test_slack_ack_requires_ok_body():
    transport = FakeTransport(Response(200, {}, b"ok"))
    ack = SlackProvider(transport).send(SlackProvider(transport).build(incident(), SLACK), SLACK, SLACK_URL)
    assert ack.status == 200 and transport.calls[0]["url"] == SLACK_URL
    with pytest.raises(DeliveryError) as err:
        p = SlackProvider(FakeTransport(Response(200, {}, b"something else")))
        p.send(p.build(incident(), SLACK), SLACK, SLACK_URL)
    assert err.value.retryable and err.value.category == "unacknowledged"


@pytest.mark.parametrize("status,body,category,retryable", [
    (429, b"rate_limited", "rate_limited", True),
    (404, b"no_service", "slack_no_service", False),
    (403, b"invalid_token", "slack_invalid_token", False),
    (410, b"channel_is_archived", "slack_channel_is_archived", False),
    (500, b"oops", "provider_unavailable", True),
])
def test_slack_errors(status, body, category, retryable):
    p = SlackProvider(FakeTransport(Response(status, {"retry-after": "7"}, body)))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), SLACK), SLACK, SLACK_URL)
    assert (err.value.category, err.value.retryable) == (category, retryable)
    if status == 429:
        assert err.value.retry_after == 7


def test_slack_requires_private_channel_confirmation():
    with pytest.raises(ValueError):
        SlackProvider.validate_destination(Destination("s", "slack", secret_ref="env:X"))


# ---- Telegram ----------------------------------------------------------------------------
def test_telegram_send_message_payload():
    p = TelegramProvider(FakeTransport(Response(200, {}, b'{"ok":true,"result":{"message_id":77}}')))
    prepared = p.build(incident(), TG)
    body = prepared.body
    assert body["chat_id"] == CHAT and body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}
    assert body["text"].startswith("\U0001F6A8 <b>Burn security alert")  # high-priority style
    assert "<b>Affected instance:</b> <code>agent-synthetic-000000</code>" in body["text"]
    assert body["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://burn.example.com/#/alerts"
    ack = p.send(prepared, TG, TELEGRAM_TOKEN)
    assert ack.provider_ref == "77"
    assert p.transport.calls[0]["url"] == f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def test_telegram_escapes_html():
    text = TelegramProvider(FakeTransport()).build(hostile_incident(), TG).body["text"]
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;" in text
    assert "@everyone" not in text


@pytest.mark.parametrize("status,payload,category,retryable,after", [
    (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 13}}, "rate_limited", True, 13),
    (400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, "payload_rejected", False, None),
    (401, {"ok": False, "error_code": 401}, "auth_rejected", False, None),
    (403, {"ok": False, "error_code": 403, "description": "bot was blocked"}, "auth_rejected", False, None),
    (502, {}, "provider_unavailable", True, None),
    (200, {"ok": False}, "unacknowledged", True, None),
])
def test_telegram_api_failures(status, payload, category, retryable, after):
    p = TelegramProvider(FakeTransport(Response(status, {}, json.dumps(payload).encode())))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), TG), TG, TELEGRAM_TOKEN)
    assert (err.value.category, err.value.retryable, err.value.retry_after) == (category, retryable, after)


def test_telegram_timeout_is_transient():
    p = TelegramProvider(FakeTransport(TimeoutError("read timed out")))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), TG), TG, TELEGRAM_TOKEN)
    assert err.value.category == "network_error" and err.value.retryable


def test_telegram_recipient_allowlist():
    with pytest.raises(ValueError):
        TelegramProvider.validate_destination(Destination("t", "telegram", recipient="-100999", secret_ref="env:X",
                                                          options={"allowed_chat_ids": [CHAT]}))
    p = TelegramProvider(FakeTransport())
    prepared = p.build(incident(), TG)
    prepared.body["chat_id"] = "-100999"
    with pytest.raises(DeliveryError) as err:
        p.send(prepared, TG, TELEGRAM_TOKEN)
    assert err.value.category == "recipient_not_allowed" and p.transport.calls == []


def test_telegram_rejects_malformed_token():
    p = TelegramProvider(FakeTransport())
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), TG), TG, "123:abc/../../evil")
    assert err.value.category == "invalid_credential_format" and p.transport.calls == []


def test_telegram_low_severity_is_silent():
    assert TelegramProvider(FakeTransport()).build(incident("flagged_seen"), TG).body["disable_notification"]
    assert not TelegramProvider(FakeTransport()).build(incident(), TG).body["disable_notification"]


# ---- Discord -----------------------------------------------------------------------------
def test_discord_embed_and_mention_suppression():
    p = DiscordProvider(FakeTransport(Response(200, {}, b'{"id":"998877"}')))
    prepared = p.build(hostile_incident(), DISCORD)
    body = prepared.body
    assert body["allowed_mentions"] == {"parse": []}
    embed = body["embeds"][0]
    assert embed["color"] == 0xC4320A and embed["url"] == "https://burn.example.com/#/alerts"
    assert embed["timestamp"].endswith("+00:00") and len(embed["fields"]) <= 25
    raw = json.dumps(body)
    assert "@everyone" not in raw and "@here" not in raw and "<@U1>" not in raw
    ack = p.send(prepared, DISCORD, DISCORD_URL)
    assert ack.provider_ref == "998877"
    assert p.transport.calls[0]["url"] == DISCORD_URL + "?wait=true"


@pytest.mark.parametrize("status,body,category,retryable", [
    (429, b'{"message":"You are being rate limited.","retry_after":2.5,"global":false}', "rate_limited", True),
    (404, b'{"message":"Unknown Webhook","code":10015}', "destination_not_found", False),
    (401, b"{}", "auth_rejected", False),
    (503, b"", "provider_unavailable", True),
])
def test_discord_errors(status, body, category, retryable):
    p = DiscordProvider(FakeTransport(Response(status, {}, body)))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), DISCORD), DISCORD, DISCORD_URL)
    assert (err.value.category, err.value.retryable) == (category, retryable)
    if status == 429:
        assert err.value.retry_after == 2.5


# ---- Teams -------------------------------------------------------------------------------
def test_teams_adaptive_card_payload():
    body = TeamsProvider(FakeTransport()).build(incident(), TEAMS).body
    assert body["type"] == "message"
    att = body["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = att["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    allowed = {"TextBlock", "FactSet"}
    assert {b["type"] for b in card["body"]} <= allowed
    facts = {f["title"]: f["value"] for f in card["body"][-1]["facts"]}
    assert "Affected instance" in facts and facts["Severity"] == "HIGH"
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Investigate in Burn",
                                "url": "https://burn.example.com/#/alerts"}]
    json.dumps(body)  # serializable


def test_teams_accepts_202_and_throttling():
    p = TeamsProvider(FakeTransport(Response(202, {}, b"")))
    assert p.send(p.build(incident(), TEAMS), TEAMS, TEAMS_URL).status == 202
    p = TeamsProvider(FakeTransport(Response(429, {"retry-after": "30"}, b"")))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), TEAMS), TEAMS, TEAMS_URL)
    assert err.value.retry_after == 30 and err.value.retryable


def test_teams_refuses_retired_connector_urls():
    p = TeamsProvider(FakeTransport())
    for url in ("https://contoso.webhook.office.com/webhookb2/abc", "https://outlook.office.com/webhook/abc"):
        with pytest.raises(DeliveryError) as err:
            p.send(p.build(incident(), TEAMS), TEAMS, url)
        assert err.value.category == "retired_connector_endpoint" and not err.value.retryable
    assert p.transport.calls == []


def test_teams_escapes_markdown():
    card = TeamsProvider(FakeTransport()).build(hostile_incident(), TEAMS).body["attachments"][0]["content"]
    text = json.dumps(card)
    assert "[click](https://evil.example)" not in text and "@here" not in text


# ---- Email (SES) -------------------------------------------------------------------------
def test_ses_email_generation():
    ses = FakeSES()
    p = EmailProvider(ses_client=ses)
    prepared = p.build(incident(), EMAIL)
    body = prepared.body
    simple = body["Content"]["Simple"]
    assert simple["Subject"]["Data"].startswith("[Burn][HIGH] Canary credential presented")
    assert "Affected instance: agent-synthetic-000000" in simple["Body"]["Text"]["Data"]
    assert "<table>" in simple["Body"]["Html"]["Data"]
    assert body["Destination"]["ToAddresses"] == ["secops@example.com", "lead@example.com"]
    ack = p.send(prepared, EMAIL, None)
    assert ack.provider_ref == "ses-msg-1" and ses.calls[0]["FromEmailAddress"] == "burn@example.com"


def test_ses_subject_per_severity():
    assert EmailProvider(ses_client=FakeSES()).build(incident("rotation_failed"), EMAIL).body["Content"]["Simple"][
        "Subject"]["Data"].startswith("[Burn][CRITICAL]")
    assert EmailProvider(ses_client=FakeSES()).build(incident("canary_revoked"), EMAIL).body["Content"]["Simple"][
        "Subject"]["Data"].startswith("[Burn][UPDATE]")


def test_ses_html_is_escaped():
    html = EmailProvider(ses_client=FakeSES()).build(hostile_incident(), EMAIL).body["Content"]["Simple"]["Body"][
        "Html"]["Data"]
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


class _ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


@pytest.mark.parametrize("code,retryable", [("MessageRejected", False), ("TooManyRequestsException", True),
                                            ("MailFromDomainNotVerifiedException", False)])
def test_ses_errors(code, retryable):
    p = EmailProvider(ses_client=FakeSES(_ClientError(code)))
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(incident(), EMAIL), EMAIL, None)
    assert err.value.retryable == retryable


def test_ses_recipient_validation():
    with pytest.raises(ValueError):
        EmailProvider.validate_destination(Destination("e", "email", recipient="not-an-email",
                                                       options={"from_address": "burn@example.com"}))
    with pytest.raises(ValueError):
        EmailProvider.validate_destination(Destination("e", "email", recipient="a@example.com", options={}))
