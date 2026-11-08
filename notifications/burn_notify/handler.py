"""Lambda entry points.

queue_handler   SQS event source (ReportBatchItemFailures). One message = one stream record
                forwarded by EventBridge Pipes. A message whose destinations need a retry is
                reported as failed, with its visibility timeout set to Retry-After or to a
                jittered exponential backoff; destinations that already succeeded are skipped
                on redelivery by the delivery claims. After max receives the remaining
                destinations are recorded as permanently_failed and SQS moves the message to
                the DLQ.
flush_handler   EventBridge schedule (every minute): sends aggregate summaries and digests.

Environment: BURN_NOTIFY_STATE_TABLE, BURN_NOTIFY_CONFIG_JSON (or BURN_NOTIFY_CONFIG),
BURN_NOTIFY_MAX_RECEIVES (must match the queue's maxReceiveCount), BURN_DASHBOARD_ORIGIN.
"""
from __future__ import annotations

import os
import time

from . import config as config_mod
from .events import InvalidEvent
from .processor import Processor
from .providers.base import backoff
from .redact import get_logger
from .store import DynamoStore

log = get_logger()
DEADLINE_MARGIN = 3.0  # seconds kept free to record outcomes before the Lambda timeout
_processor: Processor | None = None
_sqs = None


def processor() -> Processor:
    global _processor
    if _processor is None:
        cfg = config_mod.load()
        store = DynamoStore(os.environ["BURN_NOTIFY_STATE_TABLE"],
                            lease_seconds=int(os.environ.get("BURN_NOTIFY_LEASE_SECONDS", "90")))
        # BURN_NOTIFY_NOTICE labels every message [TEST] (set while a deployment is being tested).
        _processor = Processor(cfg, store, max_receives=int(os.environ.get("BURN_NOTIFY_MAX_RECEIVES", "5")),
                               notice=os.environ.get("BURN_NOTIFY_NOTICE") or None)
    return _processor


def _queue_url(arn: str) -> str:
    _, _, _, region, account, name = arn.split(":", 5)
    return f"https://sqs.{region}.amazonaws.com/{account}/{name}"


def _delay(receipt: str, arn: str, seconds: int) -> None:
    global _sqs
    try:
        if _sqs is None:
            import boto3
            _sqs = boto3.client("sqs")
        _sqs.change_message_visibility(QueueUrl=_queue_url(arn), ReceiptHandle=receipt,
                                       VisibilityTimeout=max(0, min(int(seconds), 43200)))
    except Exception as exc:  # the queue's default visibility timeout still applies
        log.warning("could not set retry delay: %s", type(exc).__name__)


def queue_handler(event, context=None, *, proc: Processor | None = None, delay=_delay):
    proc = proc or processor()
    failures = []
    deadline = None
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        deadline = time.monotonic() + context.get_remaining_time_in_millis() / 1000 - DEADLINE_MARGIN
    for record in event.get("Records", []):
        receives = int((record.get("attributes") or {}).get("ApproximateReceiveCount", "1"))
        try:
            result = proc.process_record(record, receive_count=receives, deadline=deadline)
        except InvalidEvent as exc:
            # Not a ledger event; retrying cannot fix it. Logged without content and dropped.
            log.error("dropping invalid record %s: %s", record.get("messageId"), exc)
            continue
        except Exception as exc:
            log.error("record %s failed: %s", record.get("messageId"), type(exc).__name__)
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        if result.needs_retry:
            wait = result.retry_after if result.retry_after is not None else backoff(receives, base=5, cap=300)
            if record.get("receiptHandle") and record.get("eventSourceARN"):
                delay(record["receiptHandle"], record["eventSourceARN"], int(wait) + 1)
            failures.append({"itemIdentifier": record["messageId"]})
        elif result.exhausted:
            # Final receive: failed destinations are recorded as permanently_failed; failing the
            # message once more makes SQS move it to the DLQ (maxReceiveCount reached).
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def flush_handler(event=None, context=None, *, proc: Processor | None = None):
    proc = proc or processor()
    deadline = None
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        deadline = time.monotonic() + context.get_remaining_time_in_millis() / 1000 - DEADLINE_MARGIN
    outcomes = proc.flush_windows(deadline=deadline)
    return {"flushed": len(outcomes), "delivered": sum(o.status == "delivered" for o in outcomes)}
