#!/usr/bin/env python3
"""Fusionne le résultat d'une passe (patch.json) dans enrich.json, sans jamais toucher à ce qui existe déjà.

Usage : python3 merge.py <classer|raconter> <dossier_de_données> <patch.json> <todo.json>

Claude n'écrit jamais enrich.json : il écrit seulement un patch, et ce programme décide de ce qui entre.
"""
import json
import re
import sys
from pathlib import Path

# 16:20, 16h20, 14 h 39 : une heure précise (on tolère « 19h » seul, rare et souvent dans une citation)
HEURE = re.compile(r"\b\d{1,2}\s?(?::|h|\sh\s)\s?\d{2}\b")


def sans_citations(texte):
    """Retire les citations (« … » et "…") : une heure y est légitime."""
    return re.sub(r"«[^»]*»|\"[^\"]*\"", " ", texte)


def main():
    mode, data, patch_p, todo_p = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    patch = json.loads(Path(patch_p).read_text(encoding="utf-8"))
    todo = json.loads(Path(todo_p).read_text(encoding="utf-8"))
    if not isinstance(patch, dict):
        sys.exit("patch.json doit être un objet")
    ep = data / "enrich.json"
    enr = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else {}
    ids = {i["id"] for i in json.loads((data / "items.json").read_text(encoding="utf-8"))}
    done = 0

    if mode == "classer":
        for key in ("classification", "captions", "hold"):
            part = patch.get(key, {})
            if isinstance(part, dict):
                valid = {k: v for k, v in part.items() if k in ids}
                enr.setdefault(key, {}).update(valid)
                done += len(valid)
        rev = [i for i in patch.get("reviewed", []) if i in ids]
        enr["reviewed"] = sorted(set(enr.get("reviewed", [])) | set(rev))
        fit = [i for i in patch.get("fit", []) if i in ids]
        enr["fit"] = sorted(set(enr.get("fit", [])) | set(fit))
        imp = [i for i in patch.get("importants", []) if i in ids]
        enr["importants"] = sorted(set(enr.get("importants", [])) | set(imp))
        # Rattacher de nouvelles photos ou vidéos à un article déjà écrit, sans toucher à son texte
        held = set(enr.get("hold", {}))
        kinds = {i["id"]: i["kind"] for i in json.loads((data / "items.json").read_text(encoding="utf-8"))}
        arts = {a["id"]: a for a in enr.get("articles", [])}
        for aid, media in (patch.get("rattachements") or {}).items():
            a = arts.get(aid)
            if not a or not isinstance(media, list):
                continue
            for mid in media:
                if kinds.get(mid) in ("photo", "video") and mid not in held and mid not in a.setdefault("items", []):
                    a["items"].append(mid)
                    done += 1
                    if not a.get("cover") and kinds[mid] == "photo":
                        a["cover"] = mid
        enr["articles"] = list(arts.values())
    elif mode == "raconter":
        arts = {a["id"]: a for a in enr.get("articles", [])}
        refused = 0
        for a in patch.get("articles", []):
            if not (isinstance(a, dict) and a.get("id")):
                continue
            # Garde-fou de style : pas d'heure précise (hors citations) dans un article
            if HEURE.search(sans_citations(" ".join([a.get("title", ""), a.get("chapo", "")] + [p for p in a.get("paragraphs", []) if isinstance(p, str)]))):
                print(f"article refusé (heure précise dans le texte) : {a['id']}")
                refused += 1
                continue
            arts[a["id"]] = a  # un article existant n'est remplacé que si Claude le renvoie avec le même id
            done += 1
        enr["articles"] = list(arts.values())
        if patch.get("featured") and patch["featured"] in arts:
            enr["featured"] = patch["featured"]
        # Brèves : quelques mots (25 au plus), rattachées à un élément ; une brève trop longue est ignorée
        for iid, txt in (patch.get("breves") or {}).items():
            if iid in ids and isinstance(txt, str) and 0 < len(txt.split()) <= 25 and not HEURE.search(sans_citations(txt)):
                enr.setdefault("breves", {})[iid] = txt.strip()
                done += 1
        # Si un article a été refusé, on n'avance pas : ces éléments seront repris à la prochaine passe
        if not refused:
            enr["narrated_until"] = todo.get("jusqu_a_ts", enr.get("narrated_until", 0))
    else:
        sys.exit("mode inconnu : " + mode)

    tmp = ep.with_suffix(".tmp")
    tmp.write_text(json.dumps(enr, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ep)
    print(f"fusion {mode} : {done} entrée(s)")


if __name__ == "__main__":
    main()
