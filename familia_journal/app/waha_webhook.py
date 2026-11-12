#!/usr/bin/env python3
"""S'assure que la session WAHA envoie aussi les réactions à l'app du journal (en plus de l'intégration HA, qu'on ne
touche pas). Ne modifie la configuration que si notre adresse manque. Usage : waha_webhook.py <url de réception>
Variables : WAHA_URL, WAHA_KEY, WAHA_SESSION (défaut default)."""
import json
import os
import sys
import urllib.request


def call(method, path, body=None):
    req = urllib.request.Request(
        os.environ["WAHA_URL"].rstrip("/") + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": os.environ["WAHA_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ensure(url):
    session = os.environ.get("WAHA_SESSION", "default")
    d = call("GET", f"/api/sessions/{session}")
    cfg = d.get("config") or {}
    hooks = cfg.get("webhooks") or []
    if any(w.get("url") == url and "message.reaction" in (w.get("events") or []) for w in hooks):
        return "déjà en place"
    hooks = [w for w in hooks if w.get("url") != url]
    hooks.append({"url": url, "events": ["message.reaction"], "retries": {"policy": "constant", "delaySeconds": 5, "attempts": 3}})
    cfg["webhooks"] = hooks
    call("PUT", f"/api/sessions/{session}", {"name": session, "config": cfg})
    return "ajouté"


if __name__ == "__main__":
    try:
        print("webhook réactions :", ensure(sys.argv[1]))
    except Exception as exc:
        print("webhook réactions impossible :", repr(exc))
        sys.exit(1)
