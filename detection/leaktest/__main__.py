"""TEST TOOL command line: prints only the sanitized attempt result."""
import argparse
import json

from leaktest.service import ENCODINGS, LeakTest


def main():
    parser = argparse.ArgumentParser(description="Burn leak test (TEST TOOL)")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--encoding", choices=ENCODINGS, default="random")
    parser.add_argument("--secret-ref")
    parser.add_argument("--free-text")
    args = parser.parse_args()
    try:
        result = LeakTest().run(agent_id=args.agent, encoding=args.encoding,
                                secret_ref=args.secret_ref, free_text=args.free_text)
    except Exception:
        raise SystemExit("Leak test unavailable; check session, ledger, AWS access and proxy.") from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
