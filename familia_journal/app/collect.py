#!/usr/bin/env python3
"""Collecte les nouveaux messages, photos et vidéos d'un groupe WhatsApp via WAHA.

Variables d'environnement :
  WAHA_KEY      clé d'API de WAHA (obligatoire)
  GROUP_ID      identifiant du groupe, ex. 1203...@g.us (obligatoire)
  WAHA_URL      défaut http://f245669a-waha:3000 (nom interne de l'app WAHA dans HA)
  WAHA_SESSION  défaut default
  OWNER_NAME    prénom affiché pour les messages envoyés depuis le compte lié à WAHA
  DATA_DIR      défaut data/real
  COLLECT_SINCE date ISO (AAAA-MM-JJ) : on ignore ce qui est plus ancien (début du séjour)
  COLLECT_LIMIT nombre maximal de messages relus à chaque passage (défaut 200)

Écrit : items.json (cumulatif), media/, state.json.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WAHA_URL = os.environ.get("WAHA_URL", "http://f245669a-waha:3000").rstrip("/")
KEY = os.environ["WAHA_KEY"]
SESSION = os.environ.get("WAHA_SESSION", "default")
GROUP = os.environ["GROUP_ID"]
OWNER = os.environ.get("OWNER_NAME", "Prénom")
DATA = Path(os.environ.get("DATA_DIR", "data/real"))
LIMIT = int(os.environ.get("COLLECT_LIMIT", "200"))
SINCE = os.environ.get("COLLECT_SINCE", "")
TZ = ZoneInfo("Europe/Paris")


def http(url, timeout=90):
    req = urllib.request.Request(url, headers={"X-Api-Key": KEY})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def api(path):
    return json.loads(http(f"{WAHA_URL}/api/{SESSION}/{path}"))


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", str(path)]).strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def author_of(m):
    if m.get("fromMe"):
        return OWNER
    info = ((m.get("_data") or {}).get("Info") or {}) if isinstance(m.get("_data"), dict) else {}
    name = (info.get("PushName") or "").strip()
    return name.split()[0] if name else "?"


CODE_UNE = "\U0001F4F0"  # 📰 : « mets ça à la une »
UNE_SCOPE = os.environ.get("UNE_SCOPE", "owner")  # owner : seul le compte lié à WAHA ; tous : tout le groupe


def retire_code(texte):
    """Retire le code 📰 d'un texte. Renvoie (texte nettoyé, code présent)."""
    if CODE_UNE not in (texte or ""):
        return texte, False
    propre = texte.replace(CODE_UNE, "").replace("️", "")
    propre = re.sub(r"[ \t]+\n", "\n", propre)
    propre = re.sub(r"[ \t]{2,}", " ", propre).strip()
    return propre, True


def mid_of(m):
    """Identifiant court du message WhatsApp (celui que cite une réaction)."""
    parts = str(m.get("id", "")).split("_")
    return parts[2] if len(parts) >= 3 else ""


def reaction_of(m):
    """Si le message est une réaction WhatsApp : (identifiant court du message visé, emoji). Sinon None.
    On cherche aux endroits où WAHA peut la placer ; aucun ne doit faire échouer la collecte."""
    d = m.get("_data") if isinstance(m.get("_data"), dict) else {}
    msg = d.get("Message") if isinstance(d.get("Message"), dict) else {}
    r = msg.get("reactionMessage") or msg.get("ReactionMessage")
    if isinstance(r, dict):
        key = r.get("key") or r.get("Key") or {}
        return str(key.get("ID") or key.get("id") or ""), str(r.get("text") or r.get("Text") or "")
    r = m.get("reaction")
    if isinstance(r, dict):
        full = str(r.get("messageId") or r.get("message_id") or "")
        parts = full.split("_")
        return (parts[2] if len(parts) >= 3 else full), str(r.get("text") or r.get("emoji") or "")
    return None


def media_url(md):
    path = urllib.parse.urlparse(md["url"]).path
    return f"{WAHA_URL}{path}"


def save_photo(data, dest):
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as t:
        t.write(data)
        tmp = Path(t.name)
    try:
        w, h = probe(tmp)
        if max(w, h) > 1800:
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-vf", "scale='min(1600,iw)':-2", "-q:v", "3", str(dest)])
            w, h = probe(dest)
        else:
            dest.write_bytes(data)
        return w, h
    finally:
        tmp.unlink(missing_ok=True)


def save_video(data, dest, poster):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as t:
        t.write(data)
        tmp = Path(t.name)
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-vf", "scale=-2:'min(720,ih)'",
             "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
             "-movflags", "+faststart", str(dest)])
        try:
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(dest), "-frames:v", "1",
                 "-vf", "scale=640:-2", str(poster)])
        except subprocess.CalledProcessError:
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dest), "-frames:v", "1",
                 "-vf", "scale=640:-2", str(poster)])
    finally:
        tmp.unlink(missing_ok=True)


def save_upload(data, dest):
    """Photo envoyée depuis le panneau d'administration : convertie en JPEG de 1600 px au plus (HEIC, PNG…)."""
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
        t.write(data)
        tmp = Path(t.name)
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-frames:v", "1",
             "-vf", "scale='min(1600,iw)':-2", "-q:v", "3", str(dest)])
        return probe(dest)
    finally:
        tmp.unlink(missing_ok=True)


def ingere_soumissions(items):
    """Publications ajoutées à la main depuis le panneau (dossier soumissions/) : chaque fichier (photo ou vidéo) ou
    le texte seul devient un élément, avec la consigne du propriétaire. Les éléments d'une même publication portent le
    même « lot ». Une fois prises en compte, les soumissions passent dans soumissions/faits/."""
    d = DATA / "soumissions"
    if not d.is_dir():
        return 0
    (d / "faits").mkdir(exist_ok=True)
    n = 0
    for f in sorted(d.glob("*.json")):
        try:
            sub = json.loads(f.read_text(encoding="utf-8"))
            sid = "m" + re.sub(r"\W", "", str(sub.get("id") or f.stem))[:20]
            fichiers = [x for x in (sub.get("fichiers") or []) if (d / str(x.get("f", ""))).is_file()]
            texte = str(sub.get("texte") or "").strip()
            if any(i["id"] == sid for i in items) or (not texte and not fichiers):
                pass
            else:
                ts = int(sub.get("ts") or time.time())
                base = {"ts": ts, "author": str(sub.get("auteur") or "La rédaction").strip()[:40], "manuel": True, "lot": sid}
                if sub.get("article"):
                    base["article_dedie"] = True
                stamp = f"{datetime.fromtimestamp(ts, TZ):%Y%m%d-%H%M}"
                nouveaux = []
                for k, x in enumerate(fichiers or [None]):
                    iid = sid if k == 0 else f"{sid}-{k}"
                    item = {**base, "id": iid, "text": texte if k == 0 else ""}
                    if x is None:
                        item["kind"] = "text"
                    elif x.get("type") == "video":
                        rel, poster = f"media/{stamp}_{iid}.mp4", f"media/{stamp}_{iid}.jpg"
                        save_video((d / x["f"]).read_bytes(), DATA / rel, DATA / poster)
                        item.update(kind="video", media=rel, poster=poster, flags=[])
                    else:
                        rel = f"media/{stamp}_{iid}.jpg"
                        w, h = save_upload((d / x["f"]).read_bytes(), DATA / rel)
                        item.update(kind="photo", media=rel, flags=["tall_screen"] if h / max(w, 1) >= 1.9 else [])
                    if k == 0:
                        if sub.get("consigne"):
                            item["consigne"] = str(sub["consigne"]).strip()[:1000]
                        if sub.get("une"):
                            item["une"], item["une_par"] = True, OWNER
                    nouveaux.append(item)
                items.extend(nouveaux)
                n += len(nouveaux)
            f.replace(d / "faits" / f.name)
            for x in sub.get("fichiers") or []:
                (d / str(x.get("f", ""))).replace(d / "faits" / str(x.get("f", ""))) if (d / str(x.get("f", ""))).is_file() else None
        except Exception as exc:
            print(f"soumission {f.name} ignorée : {exc!r}", file=sys.stderr)
            f.replace(d / "faits" / (f.name + ".erreur"))
    return n


def applique_reactions(items):
    """Rejoue reactions.jsonl (reçu de WAHA par reaction_hook.py) : la dernière réaction d'une personne sur un message
    fait foi. 📰 = « à la une », une autre réaction ou aucune la retire. Sans effet sur un 📰 écrit dans un texte."""
    f = DATA / "reactions.jsonl"
    if not f.exists():
        return
    dernier = {}
    for ligne in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ligne)
        except ValueError:
            continue
        parts = str(r.get("mid", "")).split("_")
        mid = parts[2] if len(parts) >= 3 else ""
        auteur = OWNER if r.get("moi") else "membre"
        if mid and (UNE_SCOPE == "tous" or auteur == OWNER):
            dernier[(mid, auteur)] = r.get("emoji") or ""
    for (mid, auteur), emoji in dernier.items():
        for it in items:
            if it.get("mid") != mid:
                continue
            if CODE_UNE in emoji:
                it["une"], it["une_par"] = True, auteur
            elif it.get("une_par") == auteur:
                it.pop("une", None)
                it.pop("une_par", None)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "media").mkdir(exist_ok=True)
    items_path, state_path = DATA / "items.json", DATA / "state.json"
    items = json.loads(items_path.read_text(encoding="utf-8")) if items_path.exists() else []
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    manuels = ingere_soumissions(items)
    known = {i["id"] for i in items} | set(state.get("skipped", []))
    cutoff = int(datetime.fromisoformat(SINCE).replace(tzinfo=TZ).timestamp()) if SINCE else 0
    cutoff = max(cutoff, state.get("last_ts", 0) - 6 * 3600)

    # 1. liste des messages (sans télécharger les médias)
    msgs, offset = [], 0
    chat = urllib.parse.quote(GROUP, safe="@")
    while len(msgs) < LIMIT:
        page = api(f"chats/{chat}/messages?limit=100&offset={offset}&downloadMedia=false")
        if not page:
            break
        msgs.extend(page)
        offset += 100
        if min(m.get("timestamp", 0) for m in page) < cutoff or len(page) < 100:
            break

    new, skipped, added = 0, [], []
    for m in sorted(msgs, key=lambda x: x.get("timestamp", 0)):
        ts = m.get("timestamp", 0)
        if ts < cutoff:
            continue
        sid = hashlib.sha1(str(m["id"]).encode()).hexdigest()[:12]
        if sid in known:
            # éléments collectés avant l'ajout de « mid » : on complète (sert à rattacher les réactions)
            for p in items:
                if p["id"] == sid and not p.get("mid"):
                    p["mid"] = mid_of(m)
            continue
        # Réaction à un message : 📰 sur une photo ou un message = « à la une » ; retirer la réaction l'annule
        r = reaction_of(m)
        if r is not None:
            cible_id, emoji = r
            auteur = author_of(m)
            if UNE_SCOPE == "tous" or auteur == OWNER:
                cible = next((p for p in items if p.get("mid") and p["mid"] == cible_id), None)
                if cible is not None:
                    if CODE_UNE in emoji:
                        cible["une"], cible["une_par"] = True, auteur
                    elif not emoji and cible.get("une_par") == auteur:
                        cible.pop("une", None)
            skipped.append(sid)
            continue
        body = (m.get("body") or "").strip()
        md = m.get("media") if isinstance(m.get("media"), dict) else {}
        mime = str(md.get("mimetype") or "")
        base = {"id": sid, "ts": ts, "author": author_of(m), "mid": mid_of(m)}
        stamp = datetime.fromtimestamp(ts, TZ).strftime("%Y%m%d-%H%M")
        if m.get("hasMedia") and (mime.startswith("image/") or mime.startswith("video/")):
            full = api(f"chats/{chat}/messages/{urllib.parse.quote(str(m['id']), safe='@')}?downloadMedia=true")
            fmd = full.get("media") if isinstance(full.get("media"), dict) else {}
            if not fmd.get("url"):
                skipped.append(sid)
                continue
            data = http(media_url(fmd))
            if mime.startswith("image/"):
                rel = f"media/{stamp}_{sid}.jpg"
                w, h = save_photo(data, DATA / rel)
                flags = ["tall_screen"] if h / max(w, 1) >= 1.9 else []
                item = {**base, "kind": "photo", "text": body, "media": rel, "flags": flags}
            else:
                rel, poster = f"media/{stamp}_{sid}.mp4", f"media/{stamp}_{sid}.jpg"
                save_video(data, DATA / rel, DATA / poster)
                item = {**base, "kind": "video", "text": body, "media": rel, "poster": poster, "flags": []}
        elif body and not m.get("hasMedia"):
            item = {**base, "kind": "text", "text": body}
        else:
            skipped.append(sid)  # vocal, sticker, document, message système…
            continue
        # Code « à la une » : l'emoji 📰 dans une légende ou un message
        propre, code = retire_code(item.get("text", ""))
        if code:
            item["text"] = propre
            autorise = UNE_SCOPE == "tous" or item["author"] == OWNER
            if item["kind"] == "text" and not propre:
                # Un message qui ne contient que le code s'applique à l'élément précédent du même auteur (10 minutes)
                if autorise:
                    prev = next((p for p in reversed(items) if p["author"] == item["author"] and 0 <= ts - p["ts"] <= 600), None)
                    if prev is not None:
                        prev["une"] = True
                skipped.append(sid)  # le message-code lui-même n'est pas publié
                continue
            if autorise:
                item["une"] = True
        items.append(item)
        added.append(item["kind"])
        new += 1
    new += manuels

    items.sort(key=lambda i: i["ts"])
    applique_reactions(items)
    # Date en toutes lettres, calculée ici : la rédaction ne doit jamais deviner un jour de la semaine
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"]
    for i in items:
        d = datetime.fromtimestamp(i["ts"], TZ)
        h = d.hour + d.minute / 60
        moment = ("dans la nuit" if h < 6 else "le matin" if h < 12 else "à l'heure du déjeuner" if h < 14
                  else "dans l'après-midi" if h < 17.5 else "en fin d'après-midi" if h < 19
                  else "en soirée" if h < 23 else "dans la nuit")
        i["when"] = f"{jours[d.weekday()]} {d.day} {mois[d.month - 1]}, {moment}"
    tmp = items_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(items_path)
    state["last_ts"] = max([i["ts"] for i in items] + [state.get("last_ts", 0)])
    state["skipped"] = sorted(set(state.get("skipped", [])) | set(skipped))
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(json.dumps({"nouveaux": new, "textes": added.count("text"), "photos": added.count("photo"),
                      "videos": added.count("video"), "ignores": len(skipped), "total": len(items)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # la collecte ne doit jamais empêcher la suite
        print(f"collecte en échec : {exc!r}", file=sys.stderr)
        sys.exit(1)
