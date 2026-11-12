#!/usr/bin/env python3
"""Lance une édition aux heures prévues, ou quand le fichier RUN_NOW apparaît dans /share/familia-journal."""
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
opts = json.load(open("/data/options.json"))
times = {t.strip() for t in str(opts.get("times", "13:00,21:00")).split(",") if t.strip()}
base = Path("/share/familia-journal")
run_now = base / "RUN_NOW"
log = base / "last-edition.log"


def ma_adresse():
    """Adresse de cette app sur le réseau interne de HA, pour que WAHA y envoie les réactions."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    req = urllib.request.Request("http://supervisor/addons/self/info", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        host = json.load(r)["data"]["hostname"]
    return f"http://{host}:8099/"


def webhook():
    try:
        subprocess.run(["python3", "/app/waha_webhook.py", ma_adresse()], timeout=90)
    except Exception as exc:
        print("webhook réactions : adresse introuvable", repr(exc), flush=True)


def statut(**kw):
    f = base / "status.json"
    try:
        st = json.loads(f.read_text())
    except Exception:
        st = {}
    st.update(kw)
    f.write_text(json.dumps(st))


def edition():
    statut(running=True, started=int(time.time()))
    try:
        edition_()
    finally:
        statut(running=False, ended=int(time.time()))


def edition_():
    webhook()  # WAHA remet parfois sa configuration à zéro : on revérifie avant chaque édition
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    print(f"[{stamp}] édition lancée", flush=True)
    with open(log, "w", encoding="utf-8") as f:
        f.write(f"Édition du {stamp}\n")
        f.flush()
        try:
            subprocess.run(["/app/edition.sh"], stdout=f, stderr=subprocess.STDOUT, timeout=3600)
        except subprocess.TimeoutExpired:
            f.write("\nTemps dépassé, édition interrompue.\n")
    print(f"[{datetime.now(TZ):%H:%M}] édition terminée", flush=True)


# Panneau d'administration (barre latérale de HA, par ingress)
subprocess.Popen(["python3", "/app/admin.py"], env={**os.environ, "BASE_DIR": str(base), "PORT": "8100"})

# Récepteur des réactions 📰 (WAHA -> reactions.jsonl)
subprocess.Popen(["python3", "/app/reaction_hook.py", str(base / "real" / "reactions.jsonl"), "8099",
                  os.environ.get("GROUP_ID", "")])

if opts.get("run_on_start", True):
    edition()

done = set()
while True:
    now = datetime.now(TZ)
    key = now.strftime("%Y-%m-%d %H:%M")
    if now.strftime("%H:%M") in times and key not in done:
        done.add(key)
        edition()
    if run_now.exists():
        run_now.unlink()
        edition()
    if len(done) > 50:
        done = set(sorted(done)[-10:])
    time.sleep(20)
