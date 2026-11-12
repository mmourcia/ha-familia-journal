#!/usr/bin/env python3
"""Reçoit les événements « message.reaction » de WAHA et les range dans reactions.jsonl (une ligne par réaction).
Usage : reaction_hook.py <fichier.jsonl> [port] [identifiant du groupe]
Ne conserve que : message visé, emoji, auteur (moi ou autre), instant. Rien d'autre n'est écrit."""
import http.server
import json
import sys

OUT = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
GROUP = sys.argv[3] if len(sys.argv) > 3 else ""


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            n = min(int(self.headers.get("content-length", 0)), 200_000)
            d = json.loads(self.rfile.read(n) or b"{}")
            p = d.get("payload") or {}
            r = p.get("reaction") or {}
            if d.get("event") == "message.reaction" and r.get("messageId") and (not GROUP or p.get("from") == GROUP):
                line = {"ts": p.get("timestamp") or int(d.get("timestamp", 0)) // 1000, "mid": r["messageId"],
                        "emoji": r.get("text") or "", "moi": bool(p.get("fromMe")), "par": p.get("participant") or ""}
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:
            print("réaction ignorée :", repr(exc), file=sys.stderr, flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
