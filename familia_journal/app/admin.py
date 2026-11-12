#!/usr/bin/env python3
"""Panneau d'administration du journal (barre latérale de Home Assistant, via « ingress »).

Réservé au propriétaire : l'accès passe par la connexion à HA, et on n'accepte que la passerelle d'HA (172.30.32.2)
ou la machine locale. Rien n'est supprimé : retirer = masquer (overrides.json), que les éditions ne touchent pas.

Variables : DATA_DIR (données), BASE_DIR (dossier partagé : status.json, RUN_NOW, last-edition.log), PORT (8100),
SITE_PORT (8080, site de la famille), SITE_URL (option de l'app)."""
import json
import os
import re
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
DATA = Path(os.environ.get("DATA_DIR", "data/real"))
BASE = Path(os.environ.get("BASE_DIR", str(DATA.parent)))
PORT = int(os.environ.get("PORT", "8100"))
SITE_PORT = os.environ.get("SITE_PORT", "8080")
ALLOWED = {"172.30.32.2", "127.0.0.1", "::1"}


def rd(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def wr(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def tail(path, n=200):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-n:])


def fmt(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%d/%m %H:%M") if ts else ""


def overrides():
    ov = rd(DATA / "overrides.json", {})
    ov.setdefault("retires", {}).setdefault("items", [])
    ov["retires"].setdefault("articles", [])
    ov.setdefault("une", {}).setdefault("on", [])
    ov["une"].setdefault("off", [])
    return ov


def status():
    st = rd(BASE / "status.json", {})
    running = bool(st.get("running")) and time.time() - st.get("started", 0) < 2 * 3600
    queued = (BASE / "RUN_NOW").exists()
    log = tail(BASE / "last-edition.log", 300)
    fin = re.findall(r"== fin : (.*)", log)
    report = rd(DATA / "build-report.json", {})
    admin = rd(DATA / "admin.json", {})
    return {
        "running": running, "queued": queued, "started": fmt(st.get("started")), "ended": fmt(st.get("ended")),
        "result": fin[-1] if fin else ("en cours" if running else "aucune édition pour le moment"),
        "report": {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()},
        "log": log, "claude_log": tail(DATA / "last-run.log", 200),
        "public_url": admin.get("public_url", os.environ.get("SITE_URL", "")),
        "site_port": SITE_PORT,
    }


def contenu():
    ov = overrides()
    hidden_i, hidden_a = set(ov["retires"]["items"]), set(ov["retires"]["articles"])
    une_on, une_off = set(ov["une"]["on"]), set(ov["une"]["off"])
    enrich = rd(DATA / "enrich.json", {})
    held = set((enrich.get("hold") or {}).keys())
    items = []
    for it in sorted(rd(DATA / "items.json", []), key=lambda x: x["ts"], reverse=True)[:80]:
        une = (bool(it.get("une")) or it["id"] in une_on) and it["id"] not in une_off
        items.append({
            "id": it["id"], "when": it.get("when", ""), "author": it.get("author", ""), "kind": it["kind"],
            "text": (it.get("text") or "")[:160], "thumb": it.get("poster") or (it.get("media") if it["kind"] == "photo" else ""),
            "retire": it["id"] in hidden_i, "une": une, "mis_de_cote": it["id"] in held,
        })
    arts = [{"id": a["id"], "title": a.get("title", ""), "date": fmt(a.get("date")), "retire": a["id"] in hidden_a}
            for a in sorted(enrich.get("articles", []), key=lambda a: a.get("date", 0), reverse=True)]
    return {"items": items, "articles": arts}


def rebuild():
    """Reconstruit le site tout de suite (sans Claude) pour que le changement soit visible."""
    import shutil
    import subprocess
    out = Path(os.environ.get("OUT_DIR", "/data/out"))
    tmp = out.with_name(out.name + ".admin")
    shutil.rmtree(tmp, ignore_errors=True)
    here = Path(__file__).parent
    r = subprocess.run([sys.executable, str(here / "build.py"), "--data", str(DATA), "--out", str(tmp)],
                       capture_output=True, timeout=300)
    if r.returncode == 0:
        old = out.with_name(out.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if out.exists():
            out.rename(old)
        tmp.rename(out)


def set_membership(lst, value, present):
    if present and value not in lst:
        lst.append(value)
    if not present and value in lst:
        lst.remove(value)


def act(path, body):
    if path == "api/run":
        (BASE / "RUN_NOW").write_text("1")
        return {"ok": True}
    if path == "api/config":
        url = str(body.get("public_url", "")).strip()
        if url and not re.match(r"^https?://[^\s]+$", url):
            return {"ok": False, "erreur": "L'adresse doit commencer par http:// ou https://"}
        admin = rd(DATA / "admin.json", {})
        admin["public_url"] = url
        wr(DATA / "admin.json", admin)
        return {"ok": True}
    if path in ("api/retirer", "api/une"):
        kind, ident, on = body.get("type"), str(body.get("id", "")), bool(body.get("on"))
        if not re.match(r"^[\w.-]{1,80}$", ident):
            return {"ok": False, "erreur": "identifiant invalide"}
        ov = overrides()
        if path == "api/retirer":
            if kind not in ("items", "articles"):
                return {"ok": False, "erreur": "type invalide"}
            set_membership(ov["retires"][kind], ident, on)
        else:
            set_membership(ov["une"]["on"], ident, on)
            set_membership(ov["une"]["off"], ident, not on)
        wr(DATA / "overrides.json", ov)
        rebuild()
        return {"ok": True}
    return {"ok": False, "erreur": "action inconnue"}


PAGE = (Path(__file__).parent / "admin.html").read_text(encoding="utf-8") if (Path(__file__).parent / "admin.html").exists() else "<p>admin.html manquant</p>"


class H(BaseHTTPRequestHandler):
    def _ok(self):
        return self.client_address[0] in ALLOWED

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._ok():
            return self._send(403, "interdit", "text/plain")
        path = self.path.split("?")[0].lstrip("/")
        if path in ("", "index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "api/status":
            return self._send(200, json.dumps(status(), ensure_ascii=False))
        if path == "api/contenu":
            return self._send(200, json.dumps(contenu(), ensure_ascii=False))
        if path.startswith("media/") and re.match(r"^media/[\w.-]+$", path):
            f = DATA / path
            if f.is_file():
                ctype = "video/mp4" if f.suffix == ".mp4" else "image/jpeg"
                return self._send(200, f.read_bytes(), ctype)
        return self._send(404, "introuvable", "text/plain")

    def do_POST(self):
        if not self._ok():
            return self._send(403, "interdit", "text/plain")
        try:
            n = min(int(self.headers.get("content-length", 0)), 30_000_000)
            body = json.loads(self.rfile.read(n) or b"{}")
            res = act(self.path.split("?")[0].lstrip("/"), body)
        except Exception as exc:
            res = {"ok": False, "erreur": repr(exc)}
        self._send(200, json.dumps(res, ensure_ascii=False))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
