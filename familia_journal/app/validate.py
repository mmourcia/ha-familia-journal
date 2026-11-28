#!/usr/bin/env python3
"""Vérifie enrich.json avant de construire le site. Sortie 1 si une erreur est trouvée.

Usage : python3 validate.py [dossier_de_données]  (défaut data/real)
"""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else "data/real")
errors = []


def load(name, default):
    try:
        return json.loads((d / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        errors.append(f"{name} : JSON invalide ({exc})")
        return default


items = load("items.json", [])
cfg = load("categories.json", {})
enr = load("enrich.json", {})
ids = {i["id"] for i in items}
slugs = {c["slug"] for c in cfg.get("categories", [])}
slugs.add(cfg.get("fallback", {}).get("slug", "en-vrac"))

if not isinstance(enr, dict):
    errors.append("enrich.json doit être un objet")
    enr = {}

for k, v in enr.get("classification", {}).items():
    if k not in ids:
        errors.append(f"classification : id inconnu {k}")
    if not isinstance(v, list) or any(s not in slugs for s in v):
        errors.append(f"classification[{k}] : rubrique inconnue {v}")
for k in enr.get("reviewed", []):
    if k not in ids:
        errors.append(f"reviewed : id inconnu {k}")
for k in enr.get("fit", []):
    if k not in ids:
        errors.append(f"fit : id inconnu {k}")
for k in enr.get("importants", []):
    if k not in ids:
        errors.append(f"importants : id inconnu {k}")
for k, v in enr.get("breves", {}).items():
    if k not in ids:
        errors.append(f"breves : id inconnu {k}")
    if not isinstance(v, str) or len(v.split()) > 25:
        errors.append(f"breves[{k}] : texte absent ou de plus de 25 mots")
for k, v in enr.get("hold", {}).items():
    if k not in ids:
        errors.append(f"hold : id inconnu {k}")
    if not isinstance(v, str):
        errors.append(f"hold[{k}] doit être un texte")
for k, v in enr.get("captions", {}).items():
    if k not in ids:
        errors.append(f"captions : id inconnu {k}")
    if not isinstance(v, str) or len(v) > 160:
        errors.append(f"captions[{k}] : texte absent ou trop long")

seen = set()
for a in enr.get("articles", []):
    for f in ("id", "title", "chapo", "paragraphs", "date"):
        if f not in a:
            errors.append(f"article {a.get('id', '?')} : champ {f} manquant")
    if a.get("id") in seen:
        errors.append(f"article en double : {a.get('id')}")
    seen.add(a.get("id"))
    if a.get("category") and a["category"] not in slugs:
        errors.append(f"article {a.get('id')} : rubrique inconnue {a['category']}")
    if not isinstance(a.get("paragraphs", []), list) or not all(isinstance(p, str) for p in a.get("paragraphs", [])):
        errors.append(f"article {a.get('id')} : paragraphs doit être une liste de textes")
    for iid in a.get("items", []) + ([a["cover"]] if a.get("cover") else []):
        if iid not in ids:
            errors.append(f"article {a.get('id')} : id inconnu {iid}")
    held = set(enr.get("hold", {}))
    for iid in a.get("items", []) + ([a["cover"]] if a.get("cover") else []):
        if iid in held:
            errors.append(f"article {a.get('id')} : utilise l'élément {iid}, mis de côté")
if enr.get("featured") and enr["featured"] not in seen:
    errors.append(f"featured : article inconnu {enr['featured']}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("enrich.json valide")
