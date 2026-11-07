"""Plain-text output. No colours or boxes, so it pastes cleanly into a terminal log."""


def table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(c) if c is not None else "-" for c in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.upper().ljust(w) for h, w in zip(headers, widths))]
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells]
    return "\n".join(lines)


def event_line(e: dict) -> str:
    what = e.get("secret_ref") or e.get("destination") or e.get("tool") or ""
    parts = [e["ts"][11:23], e["agent_id"].ljust(16), f"{e['source']}/{e['action']}".ljust(22), what]
    if e.get("detail"):
        parts.append(f"- {e['detail']}")
    return " ".join(p for p in parts if p).rstrip()
