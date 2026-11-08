"""Read applied AWS Service Quotas without running load or changing limits.

A quota snapshot lists candidate limits; it does not prove which caused a 429.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re


def capture(client, service_code):
    if not isinstance(service_code, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", service_code):
        raise ValueError("use a ServiceCode returned by list_services")
    quotas, token = [], None
    while True:
        args = {"ServiceCode": service_code}
        if token:
            args["NextToken"] = token
        page = client.list_service_quotas(**args)
        for row in page.get("Quotas", []):
            value = row["Value"]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid quota value")
            quotas.append({"name": row["QuotaName"], "code": row["QuotaCode"], "value": value,
                           "unit": row["Unit"], "global": row.get("GlobalQuota", False)})
        token = page.get("NextToken")
        if not token:
            break
    return {"scope": "applied Service Quotas snapshot", "service_code": service_code,
            "measured_at": datetime.now(timezone.utc).isoformat(), "quotas": quotas,
            "causal_attribution": "unverified",
            "note": "Applied quota values are not proof that any particular limit caused a throttled request."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-services", action="store_true")
    group.add_argument("--service-code")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent/"out/service-quotas.json")
    args = parser.parse_args()
    import boto3
    client = boto3.client("service-quotas", region_name="us-east-1")
    if args.list_services:
        for page in client.get_paginator("list_services").paginate():
            print(json.dumps([{ "name": s["ServiceName"], "code": s["ServiceCode"]} for s in page["Services"]]))
        return
    result = capture(client, args.service_code)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(f"Wrote applied quota snapshot to {args.output}; causal attribution remains unverified.")


if __name__ == "__main__":
    main()
