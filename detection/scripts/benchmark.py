"""Local timing check with generated inputs; reports timings, never payloads."""
import secrets
import statistics
import time

from burn_detect import Scanner


def main():
    scanner = Scanner(secrets.token_bytes(32), lambda _: {})
    samples = {
        "prose": ("Customer report is ready. Send a confirmation to the support team. " * 170)[:10240],
        "random": secrets.token_hex(5120),
    }
    for name, text in samples.items():
        timings = []
        for _ in range(20):
            start = time.perf_counter()
            scanner.scan(text)
            timings.append(1000 * (time.perf_counter() - start))
        print(f"{name}: 10 KiB median={statistics.median(timings):.2f} ms, max={max(timings):.2f} ms")


if __name__ == "__main__":
    main()
