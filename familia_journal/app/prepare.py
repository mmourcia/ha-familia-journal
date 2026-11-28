#!/usr/bin/env python3
"""Prépare le travail d'une passe : ne donne à Claude que le NOUVEAU.

Usage : python3 prepare.py <classer|raconter> <dossier_de_données> <dossier_de_travail>

Écrit <dossier_de_travail>/todo.json et affiche {"n": <nombre de choses à faire>, "riches": 0}.

- classer  : les éléments pas encore classés (textes) ou pas encore examinés (photos, vidéos).
- raconter : un article par rubrique ET par jour. Pour chaque couple (rubrique, jour) qui contient du nouveau et au
             moins ARTICLE_SEUIL éléments (défaut 3), Claude écrit ou complète l'article `<rubrique>-<jour>`.
             Les éléments qui n'entrent dans aucun article restent « isolés » : ils peuvent recevoir une brève.
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
CONTEXTE = 12  # messages qui précèdent le premier nouvel élément, pour comprendre la conversation
SEUIL = int(os.environ.get("ARTICLE_SEUIL", "3"))
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre"]


def load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def jour_iso(ts):
    return datetime.fromtimestamp(ts, TZ).date().isoformat()


def jour_lettres(ts):
    d = datetime.fromtimestamp(ts, TZ)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def brief(i, enr=None):
    d = {k: i[k] for k in ("id", "when", "author", "kind", "text", "flags") if i.get(k)}
    if i.get("media"):
        d["media"] = "data/real/" + i["media"]
    if i.get("poster"):
        d["poster"] = "data/real/" + i["poster"]
    if i.get("une"):
        d["demande_une"] = True  # un membre a mis le code 📰 : à valoriser
    if i.get("consigne"):
        d["consigne_proprietaire"] = i["consigne"]  # instruction du propriétaire, saisie dans le panneau d'administration
    if i.get("manuel"):
        d["ajout_manuel"] = True
    if enr is not None:
        d["rubriques"] = enr.get("classification", {}).get(i["id"], [])
        if enr.get("captions", {}).get(i["id"]):
            d["legende"] = enr["captions"][i["id"]]
    return d


def brevable(i):
    """Un élément isolé mérite une brève s'il porte un peu de matière : photo, vidéo ou message de 8 mots ou plus."""
    return i["kind"] in ("photo", "video") or i.get("manuel") or len((i.get("text") or "").split()) >= 8


def main():
    mode, data, work = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    items = sorted(load(data / "items.json", []), key=lambda x: x["ts"])
    enr = load(data / "enrich.json", {})
    cfg = load(data / "categories.json", {})
    noms = {c["slug"]: c["name"] for c in cfg.get("categories", [])}
    held = set(enr.get("hold", {}))
    cl = enr.get("classification", {})
    reviewed = set(enr.get("reviewed", []))
    todo = {"mode": mode}

    if mode == "classer":
        new = [i for i in items if i["id"] not in held and (
            (i["kind"] == "text" and i["id"] not in cl) or (i["kind"] != "text" and i["id"] not in reviewed))]
        todo["nouveaux"] = [brief(i) for i in new]
        n = len(new)
    elif mode == "raconter":
        since = enr.get("narrated_until", 0)
        visibles = [i for i in items if i["id"] not in held and (i["kind"] == "text" or i["id"] in reviewed)]
        new = [i for i in visibles if i["ts"] > since]
        new_ids = {i["id"] for i in new}
        articles = {a["id"]: a for a in enr.get("articles", [])}
        groupes = defaultdict(list)
        dedies = [i for i in visibles if i.get("article_dedie")]
        for i in visibles:
            if i.get("article_dedie"):
                continue  # un ajout manuel « en faire un article » a son propre article
            for slug in cl.get(i["id"], []):
                if slug in noms:  # pas d'article pour « En vrac »
                    groupes[(slug, jour_iso(i["ts"]))].append(i)
        a_ecrire, couverts = [], set()
        retouches = load(data / "retouches.json", [])
        for (slug, jour), its in sorted(groupes.items(), key=lambda kv: kv[0][1]):
            nouveaux = [i for i in its if i["id"] in new_ids]
            if not nouveaux:
                continue
            aid = f"{slug}-{jour}"
            existant = articles.get(aid)
            if len(its) < SEUIL and not existant:
                continue  # sous le seuil : ces éléments restent isolés
            couverts |= {i["id"] for i in nouveaux}
            a_ecrire.append({
                "article_id": aid, "rubrique": slug, "rubrique_nom": noms[slug], "jour": jour,
                "jour_en_lettres": jour_lettres(its[0]["ts"]), "n_total": len(its),
                "article_existant": existant, "ids_nouveaux": [i["id"] for i in nouveaux],
                "elements": [brief(i, enr) for i in its],
            })
        # Ajouts manuels qui doivent avoir leur propre article (id perso-<id>)
        by_id = {i["id"]: i for i in items}
        fb = cfg.get("fallback", {"slug": "en-vrac", "name": "En vrac"})
        lots = defaultdict(list)
        for i in dedies:
            lots[i.get("lot") or i["id"]].append(i)
        for lot, its in lots.items():
            tete = its[0]
            aid = f"perso-{tete['id']}"
            if not any(i["id"] in new_ids for i in its) and not any(r.get("article_id") == aid for r in retouches):
                continue
            slug = next((x for x in cl.get(tete["id"], []) if x in noms or x == fb["slug"]), fb["slug"])
            couverts |= {i["id"] for i in its}
            a_ecrire.append({
                "article_id": aid, "rubrique": slug, "rubrique_nom": noms.get(slug, fb["name"]),
                "jour": jour_iso(tete["ts"]), "jour_en_lettres": jour_lettres(tete["ts"]), "n_total": len(its),
                "article_existant": articles.get(aid), "ids_nouveaux": [i["id"] for i in its if i["id"] in new_ids],
                "elements": [brief(i, enr) for i in its],
            })
        # Retouches demandées par le propriétaire : on réécrit l'article avec sa consigne
        for r in retouches:
            a = articles.get(r.get("article_id"))
            if not a:
                continue
            g = next((x for x in a_ecrire if x["article_id"] == a["id"]), None)
            if g is None:
                m = re.match(r"^(.+)-(\d{4}-\d{2}-\d{2})$", a["id"])
                its = groupes.get((m.group(1), m.group(2)), []) if m else []
                its = its or [by_id[x] for x in a.get("items", []) if x in by_id]
                g = {"article_id": a["id"], "rubrique": a.get("category", ""), "rubrique_nom": noms.get(a.get("category", ""), ""),
                     "jour": m.group(2) if m else "", "jour_en_lettres": jour_lettres(a["date"]) if a.get("date") else "",
                     "n_total": len(its), "article_existant": a, "ids_nouveaux": [], "elements": [brief(i, enr) for i in its]}
                a_ecrire.append(g)
            g.setdefault("consignes_proprietaire", []).append(r["consigne"])
        isoles = [i for i in new if i["id"] not in couverts]
        brevables = [i for i in isoles if brevable(i)]
        todo["seuil"] = SEUIL
        todo["groupes_a_ecrire"] = a_ecrire
        todo["isoles"] = [brief(i, enr) for i in isoles]
        todo["isoles_a_resumer_en_breve"] = [i["id"] for i in brevables]
        n = len(a_ecrire) + len(brevables)
    else:
        sys.exit("mode inconnu : " + mode)

    contexte = []
    if new:
        first = min(i["ts"] for i in new)
        ids_new = {i["id"] for i in new}
        before = [i for i in items if i["ts"] < first and i["kind"] == "text" and i["id"] not in held and i["id"] not in ids_new]
        contexte = [brief(i) for i in before[-CONTEXTE:]]
    todo["contexte_avant"] = contexte
    todo["articles_existants"] = [{"id": a["id"], "title": a["title"], "category": a.get("category"), "date": a.get("date"),
                                   "items": a.get("items", [])} for a in enr.get("articles", [])]
    todo["jusqu_a_ts"] = max((i["ts"] for i in new), default=enr.get("narrated_until", 0))
    work.mkdir(parents=True, exist_ok=True)
    (work / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n": n, "riches": 0}))


if __name__ == "__main__":
    main()
