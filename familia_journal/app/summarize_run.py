#!/usr/bin/env python3
"""Résume en clair le journal détaillé d'une rédaction (sortie « stream-json » de Claude Code).

Usage :
  python3 summarize_run.py data/real/last-run.jsonl        # relit un journal terminé
  tail -f data/real/last-run.jsonl | python3 summarize_run.py -   # suit en direct
"""
import json
import sys
from datetime import datetime


def short(s, n=110):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def show(ev):
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"démarrage · modèle {ev.get('model', '?')}"
    if t == "assistant":
        out = []
        for c in (ev.get("message") or {}).get("content", []):
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("Claude : " + short(c["text"]))
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                what = inp.get("file_path") or inp.get("path") or inp.get("command") or ""
                out.append(f"outil {c.get('name')} {short(what, 90)}")
        return "\n".join(out) or None
    if t == "result":
        sec = (ev.get("duration_ms") or 0) / 1000
        cost = ev.get("total_cost_usd")
        tail = f" · coût indicatif {cost:.2f} $" if isinstance(cost, (int, float)) else ""
        return f"FIN · {ev.get('subtype')} · {ev.get('num_turns', '?')} tours · {sec:.0f} s{tail}\n{ev.get('result', '')}"
    return None


def main():
    src = sys.stdin if len(sys.argv) < 2 or sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8")
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = show(ev)
        if msg:
            print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


if __name__ == "__main__":
    main()
