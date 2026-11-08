"""Amazon SES (v2 SendEmail) with a text and an HTML part.

No secret: the Lambda's IAM role is allowed ses:SendEmail for the one verified
sender identity. Recipients are the configured list only (max 10). In the SES
sandbox every recipient must also be a verified identity; SES rejects others with
MessageRejected, which is recorded as a permanent failure.
"""
from __future__ import annotations

import os
import re

from .. import escape
from ..incident import Incident
from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$")
MAX_RECIPIENTS = 10
_RETRYABLE = {"Throttling", "ThrottlingException", "TooManyRequestsException", "LimitExceededException",
              "ServiceUnavailable", "InternalFailure", "InternalServiceError", "RequestTimeout"}


def recipients(dest: Destination) -> list[str]:
    return [r.strip() for r in str(dest.recipient or "").split(",") if r.strip()]


class EmailProvider(Provider):
    name = "email"
    needs_secret = False

    def __init__(self, transport=None, ses_client=None):
        super().__init__(transport=transport or object())
        self._ses = ses_client

    def _client(self):
        if self._ses is None:
            import boto3
            from botocore.config import Config
            # boto3 defaults (60 s connect/read, several retries) would outlast the Lambda's budget.
            region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
            self._ses = boto3.client("sesv2", region_name=region, config=Config(connect_timeout=5, read_timeout=10,
                                                            retries={"max_attempts": 2, "mode": "standard"}))
        return self._ses

    @classmethod
    def validate_destination(cls, dest: Destination) -> None:
        to = recipients(dest)
        if not to or len(to) > MAX_RECIPIENTS or not all(EMAIL_RE.match(r) for r in to):
            raise ValueError(f"destination {dest.id}: recipient must be 1-{MAX_RECIPIENTS} comma-separated emails")
        if not EMAIL_RE.match(str(dest.options.get("from_address", ""))):
            raise ValueError(f"destination {dest.id}: options.from_address must be the verified SES sender")

    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        text_lines = [incident.header, "", incident.title, incident.summary, ""]
        text_lines += [f"{label}: {escape.clean(value)}" for label, value in incident.fields()]
        if incident.dashboard_url:
            text_lines += ["", f"Investigate: {incident.dashboard_url}"]
        text_lines += ["", "Generated from Burn ledger events. No credential values are included."]
        esc = escape.email_html
        color = "#b42318" if incident.priority else "#1f2937"
        rows = "".join(f'<tr><th align="left" style="padding:2px 12px 2px 0">{esc(label)}</th>'
                       f'<td style="font-family:monospace">{esc(value)}</td></tr>' for label, value in incident.fields())
        link = (f'<p><a href="{esc(incident.dashboard_url, 500)}">Investigate in Burn</a></p>'
                if incident.dashboard_url else "")
        html_body = (f'<div style="font-family:system-ui,sans-serif;font-size:14px">'
                     f'<p style="color:{color};font-weight:700">{esc(incident.header)}</p>'
                     f"<p><strong>{esc(incident.title)}</strong><br>{esc(incident.summary, 1500)}</p>"
                     f"<table>{rows}</table>{link}"
                     f'<p style="color:#6b7280">Generated from Burn ledger events. '
                     f"No credential values are included.</p></div>")
        body = {
            "FromEmailAddress": dest.options["from_address"],
            "Destination": {"ToAddresses": recipients(dest)},
            "Content": {"Simple": {
                "Subject": {"Data": escape.clean(incident.subject(), 200), "Charset": "UTF-8"},
                "Body": {"Text": {"Data": "\n".join(text_lines), "Charset": "UTF-8"},
                         "Html": {"Data": html_body, "Charset": "UTF-8"}},
            }},
        }
        if dest.options.get("configuration_set"):
            body["ConfigurationSetName"] = dest.options["configuration_set"]
        preview = {"provider": self.name, "subject": body["Content"]["Simple"]["Subject"]["Data"],
                   "to_count": len(recipients(dest)), "text": body["Content"]["Simple"]["Body"]["Text"]["Data"]}
        return PreparedRequest(url_ref=None, body=body, headers={}, preview=preview)

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        allowed = set(recipients(dest))
        if not set(prepared.body["Destination"]["ToAddresses"]) <= allowed:
            raise DeliveryError("recipient_not_allowed", retryable=False)
        try:
            out = self._client().send_email(**prepared.body)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code is None:  # endpoint/connect/read timeouts from botocore
                raise DeliveryError("network_error", retryable=True) from None
            if code in _RETRYABLE:
                raise DeliveryError("rate_limited" if "Throttl" in code or "Many" in code else "provider_unavailable",
                                    retryable=True) from None
            raise DeliveryError(f"ses_{code}"[:60], retryable=False) from None
        return Ack(provider_ref=out.get("MessageId"), status=200)
