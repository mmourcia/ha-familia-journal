#!/usr/bin/env python3
"""Transforme le nom d'un groupe WhatsApp (« Les Bouvet 2.0 ») en identifiant (« …@g.us ») avec la liste de WAHA.
Un identifiant déjà complet est renvoyé tel quel. Sur la sortie standard : l'identifiant seul.
Variables : WAHA_URL, WAHA_KEY, WAHA_SESSION. Usage : resolve_group.py "<nom ou identifiant>" """
import json
import os
import re
import sys
import unicodedata
import urllib.request


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def main():
    wanted = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not wanted:
        print("aucun groupe indiqué dans les options (group_id)", file=sys.stderr)
        return 1
    if wanted.endswith("@g.us"):
        print(wanted)
        return 0
    url = f"{os.environ['WAHA_URL'].rstrip('/')}/api/{os.environ.get('WAHA_SESSION', 'default')}/chats/overview?limit=500"
    req = urllib.request.Request(url, headers={"X-Api-Key": os.environ["WAHA_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        chats = json.load(r)
    groups = [(c.get("id"), c.get("name") or "") for c in chats if str(c.get("id", "")).endswith("@g.us")]
    n = norm(wanted)
    exact = [g for g in groups if norm(g[1]) == n]
    found = exact or [g for g in groups if n and n in norm(g[1])]
    if len(found) == 1:
        print(found[0][0])
        print(f"groupe « {found[0][1].strip()} » retrouvé", file=sys.stderr)
        return 0
    if found:
        print(f"plusieurs groupes correspondent à « {wanted} » : " + " | ".join(g[1].strip() for g in found[:10]) +
              ". Précise le nom dans les options.", file=sys.stderr)
    else:
        print(f"aucun groupe ne s'appelle « {wanted} ». Groupes connus : " +
              " | ".join(g[1].strip() for g in groups[:40]), file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("liste des groupes illisible :", repr(exc), file=sys.stderr)
        sys.exit(1)
