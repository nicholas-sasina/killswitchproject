"""CloudWatch metrics via Embedded Metric Format: one JSON log line, no API call, no extra IAM.

Metrics: Delivered, DeliveryFailed (every failed attempt), PermanentFailure (a destination given up
on: never reaches the DLQ, so it has its own alarm), DeliverySkipped, SendLatency.

Disabled unless BURN_NOTIFY_METRICS=1 (the deployed Lambda sets it), so tests and the
dry run stay quiet. Dimensions are provider and outcome category only; never ids.
"""
from __future__ import annotations

import json
import os
import time

NAMESPACE = "Burn/Notifications"


def emit(metric: str, *, provider: str, outcome: str, latency_ms: float | None = None) -> None:
    if os.environ.get("BURN_NOTIFY_METRICS") != "1":
        return
    metrics = [{"Name": metric, "Unit": "Count"}]
    # Two dimension sets: per provider/outcome, and a dimensionless total the alarms watch.
    doc = {"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": NAMESPACE, "Dimensions": [["Provider", "Outcome"], []], "Metrics": metrics}]},
        "Provider": provider, "Outcome": outcome[:60], metric: 1}
    if latency_ms is not None:
        metrics.append({"Name": "SendLatency", "Unit": "Milliseconds"})
        doc["SendLatency"] = round(latency_ms, 1)
    print(json.dumps(doc))
