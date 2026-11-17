#!/usr/bin/env python3
"""Jeu de piste du journal : un parcours par jeune, des étapes qui s'ouvrent une à une, validées par un code posté
dans le groupe WhatsApp avec un préfixe (« Start <mot> » pour lancer le jeu, « jeu: <code> » pour répondre).

Rien de secret n'est dans le dépôt : les parcours (énoncés, codes) sont chargés depuis le panneau d'administration
et vivent dans JEU_DIR (par défaut <données>/../jeu), à côté du journal.

  config.json    parcours (mot de départ, joueurs, étapes, codes)   <- jamais publié
  state.json     avancement, réglages (armé, rythme, test), expéditeurs vus
  activite.jsonl un message = un jour + un prénom (jamais le texte) : sert aux questions « sur le groupe »
  debug.jsonl    forme (noms de champs) des derniers événements WAHA

Usage en ligne de commande : jeu.py tick   (ouvre les étapes dont l'heure est venue)
Variables : DATA_DIR, JEU_DIR, OUT_DIR, WAHA_URL, WAHA_KEY, WAHA_SESSION, OWNER_NAME, JEU_GROUP_ID."""
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
DATA = Path(os.environ.get("DATA_DIR", "data/real"))
JEU = Path(os.environ.get("JEU_DIR") or (DATA.resolve().parent / "jeu"))
HEURE_OUVERTURE = 7  # une étape validée s'ouvre le lendemain à cette heure (rythme « jour »)

RE_DEUX_POINTS = re.compile(r"^\s*(start|jeu)\s*:\s*(.*?)\s*$", re.I | re.S)
RE_MOT = re.compile(r"^\s*(start|jeu)\s+(\S+)\s*$", re.I)


# ---------------------------------------------------------------- outils
def norm(s):
    """Texte comparable : sans accents, sans casse, sans espaces ni ponctuation. Les emoji sont conservés."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    out = []
    for c in s:
        if unicodedata.combining(c) or ord(c) in (0xFE0F, 0x200D):
            continue
        if c.isalnum() or ord(c) > 0x2000:
            out.append(c.lower())
    return "".join(out)


def parse(body):
    """« Start motdepasse », « jeu: abricot »… -> ("start"|"jeu", texte). Sinon None."""
    m = RE_DEUX_POINTS.match(body or "") or RE_MOT.match(body or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def est_message_jeu(body):
    """Vrai pour tout message du jeu : la collecte du journal doit l'ignorer, juste ou faux."""
    return parse(body) is not None


def rd(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def wr(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def verrou():
    """Un seul processus à la fois modifie l'avancement (le récepteur WAHA, le planificateur, le panneau)."""
    JEU.mkdir(parents=True, exist_ok=True)
    with open(JEU / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- configuration et état
def config():
    return rd(JEU / "config.json", None)


def etat():
    st = {"arme": False, "demarre": 0, "rythme": "jour", "test": False, "joueurs": {}, "vus": {}, "traites": []}
    st.update(rd(JEU / "state.json", {}))
    cfg = config() or {}
    for j in cfg.get("joueurs", []):
        st["joueurs"].setdefault(j["slug"], {"valides": [], "ouvert": 0, "attendu": None, "expediteurs": []})
    return st


def sauve(st):
    st["traites"] = st["traites"][-300:]
    wr(JEU / "state.json", st)


def valider_config(cfg):
    """Contrôle d'une configuration chargée depuis le panneau. Renvoie un message d'erreur ou ''."""
    if not isinstance(cfg, dict) or not norm(cfg.get("mot_depart")):
        return "mot_depart manquant"
    slugs = set()
    for j in cfg.get("joueurs") or []:
        if not j.get("prenom") or not re.match(r"^[a-z0-9-]{6,40}$", str(j.get("slug", ""))):
            return f"joueur invalide (prénom ou slug) : {j.get('prenom')}"
        if j["slug"] in slugs:
            return "slug en double : " + j["slug"]
        slugs.add(j["slug"])
        for i, s in enumerate(j.get("etapes") or []):
            if not s.get("titre") or not (s.get("enonce") or s.get("html")):
                return f"{j['prenom']} étape {i + 1} : titre ou énoncé manquant"
            if not s.get("codes") and s.get("dynamique") not in ("auteurs", "bavard"):
                return f"{j['prenom']} étape {i + 1} : aucun code"
    if not slugs:
        return "aucun joueur"
    return ""


# ---------------------------------------------------------------- activité du groupe (questions dynamiques)
def note_activite(msg, ts):
    if not msg.get("nom"):
        return
    ligne = {"ts": ts, "d": datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d"), "nom": msg["nom"]}
    with open(JEU / "activite.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def activite(jour):
    rows = []
    try:
        for line in (JEU / "activite.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("d") == jour:
                rows.append(r)
    except Exception:
        pass
    return rows


def attendu(dyn, jour):
    """Réponse à une question sur le groupe, figée à l'ouverture de l'étape. Texte, ou liste de réponses possibles."""
    rows = activite(jour)
    if dyn == "auteurs":
        return str(len({norm(r["nom"]) for r in rows}))
    if dyn == "bavard":
        c = Counter(r["nom"] for r in rows)
        if not c:
            return []
        top = max(c.values())
        return [n for n, v in c.items() if v == top]
    return None


def jour_ref(st, now):
    d = now.date() - timedelta(days=1) if st.get("rythme", "jour") == "jour" else now.date()
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- avancement
def _ouvrir(st, cfg_j, now):
    ps = st["joueurs"][cfg_j["slug"]]
    i = len(ps["valides"])
    etape = cfg_j["etapes"][i]
    ps["ouvert"] = int(now.timestamp())
    ps["attendu"] = attendu(etape.get("dynamique"), jour_ref(st, now)) if etape.get("dynamique") else None


def _tick(st, cfg, now):
    """Ouvre ce qui doit l'être. Renvoie True si quelque chose a changé."""
    if not st["demarre"] or not cfg:
        return False
    changed = False
    for j in cfg["joueurs"]:
        ps = st["joueurs"][j["slug"]]
        i = len(ps["valides"])
        if i >= len(j["etapes"]) or ps["ouvert"]:
            continue
        if i == 0 or st.get("rythme") == "immediat":
            ouvre = True
        else:
            h = int(cfg.get("heure_ouverture", HEURE_OUVERTURE))
            d = datetime.fromtimestamp(ps["valides"][-1], TZ)
            ouvre = now >= (d + timedelta(days=1)).replace(hour=h, minute=0, second=0, microsecond=0)
        if ouvre:
            _ouvrir(st, j, now)
            changed = True
    return changed


def tick(now=None):
    now = now or datetime.now(TZ)
    with verrou():
        st, cfg = etat(), config()
        changed = _tick(st, cfg, now)
        if changed:
            sauve(st)
    if changed:
        rebuild_site(complet=False)
    return changed


def demarrer(now=None, force=False):
    now = now or datetime.now(TZ)
    with verrou():
        st, cfg = etat(), config()
        if not cfg or (st["demarre"] and not force):
            return False
        st["demarre"], st["arme"] = int(now.timestamp()), True
        _tick(st, cfg, now)
        sauve(st)
    rebuild_site(complet=True)
    return True


def _expediteur_ok(st, ps, msg):
    """Si le panneau a associé des numéros à ce joueur, seul l'un d'eux peut valider (hors mode test)."""
    if st.get("test") or not ps.get("expediteurs"):
        return True
    return bool(set(ps["expediteurs"]) & set(msg.get("ids") or []))


def _reponse_ok(etape, ps, texte, st, now):
    p = norm(texte)
    if not p:
        return False
    if etape.get("dynamique"):
        # Rythme « jour » : réponse figée à l'ouverture (la veille est terminée). Mode immédiat (test) : calculée au moment de répondre.
        att = attendu(etape["dynamique"], jour_ref(st, now)) if st.get("rythme") == "immediat" else ps.get("attendu")
        att = att if isinstance(att, list) else ([att] if att is not None else [])
        return any(norm(a) == p for a in att)
    return any(norm(c) == p for c in etape.get("codes", []))


def traite(msg, now=None):
    """Un message du groupe. Renvoie l'emoji à poser en réaction (ou None) ; le message n'est jamais republié."""
    now = now or datetime.now(TZ)
    rebuild = None
    with verrou():
        st, cfg = etat(), config()
        ts = int(msg.get("ts") or now.timestamp())
        parsed = parse(msg.get("body"))
        # qui parle (pour associer les numéros aux joueurs depuis le panneau)
        if msg.get("ids"):
            cle = msg["ids"][0]
            st["vus"][cle] = {"nom": msg.get("nom") or "", "ids": msg["ids"], "dernier": ts}
        if not parsed:
            note_activite(msg, ts)
            sauve(st)
            return None
        if msg.get("id") in st["traites"]:
            return None
        st["traites"].append(msg.get("id"))
        kind, texte = parsed
        emoji = "\U0001F914"  # 🤔
        if not cfg or not st["arme"]:
            emoji = "\U0001F4A4"  # 💤 : pas encore
        elif kind == "start":
            if st["demarre"]:
                emoji = "\U0001F44D"  # 👍 déjà lancé
            elif norm(texte) == norm(cfg["mot_depart"]):
                st["demarre"] = int(now.timestamp())
                _tick(st, cfg, now)
                emoji, rebuild = "\U0001F513", True  # 🔓
        elif not st["demarre"]:
            emoji = "\U0001F4A4"
        else:
            for j in cfg["joueurs"]:
                ps = st["joueurs"][j["slug"]]
                i = len(ps["valides"])
                if i >= len(j["etapes"]) or not ps["ouvert"]:
                    continue
                if _reponse_ok(j["etapes"][i], ps, texte, st, now) and _expediteur_ok(st, ps, msg):
                    ps["valides"].append(int(now.timestamp()))
                    ps["ouvert"], ps["attendu"] = 0, None
                    fini = len(ps["valides"]) >= len(j["etapes"])
                    emoji = "\U0001F3C6" if fini else "\U0001F513"  # 🏆 / 🔓
                    _tick(st, cfg, now)  # rythme immédiat : l'étape suivante s'ouvre tout de suite
                    rebuild = False
                    break
        sauve(st)
    if rebuild is not None:
        rebuild_site(complet=bool(rebuild))
    return emoji


# ---------------------------------------------------------------- WAHA
def from_waha(d, groupe):
    """Événement « message.any » de WAHA -> dictionnaire simple, ou None (autre groupe, autre événement)."""
    if d.get("event") not in ("message", "message.any"):
        return None
    p = d.get("payload") or {}
    if groupe and p.get("from") != groupe:
        return None
    info = ((p.get("_data") or {}).get("Info") or {}) if isinstance(p.get("_data"), dict) else {}
    ids = []
    for v in (p.get("participant"), info.get("Sender"), info.get("SenderAlt"), p.get("author")):
        v = re.split(r"[:@]", str(v or ""))[0]
        if v and v not in ids:
            ids.append(v)
    if p.get("fromMe"):
        ids = ["moi"]
        nom = os.environ.get("OWNER_NAME", "")
    else:
        nom = (info.get("PushName") or "").strip().split(" ")[0]
    return {"id": p.get("id") or "", "ts": p.get("timestamp") or int(time.time()), "body": p.get("body") or "",
            "ids": ids, "nom": nom, "fromMe": bool(p.get("fromMe"))}


def note_forme(d):
    """Garde les noms de champs des derniers événements (pas leur contenu) pour comprendre le format de WAHA."""
    p = d.get("payload") or {}
    info = ((p.get("_data") or {}).get("Info") or {}) if isinstance(p.get("_data"), dict) else {}
    ligne = {"evt": d.get("event"), "cles": sorted(p.keys()), "info": sorted(info.keys()), "from_g": str(p.get("from", "")).endswith("@g.us")}
    f = JEU / "debug.jsonl"
    try:
        lignes = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        f.write_text("\n".join(lignes[-19:] + [json.dumps(ligne)]) + "\n", encoding="utf-8")
    except Exception:
        pass


def reagir(message_id, emoji):
    if not message_id or not emoji:
        return
    req = urllib.request.Request(
        os.environ.get("WAHA_URL", "http://f245669a-waha:3000").rstrip("/") + "/api/reaction", method="PUT",
        data=json.dumps({"session": os.environ.get("WAHA_SESSION", "default"), "messageId": message_id, "reaction": emoji}).encode(),
        headers={"X-Api-Key": os.environ.get("WAHA_KEY", ""), "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:
        print("réaction impossible :", repr(exc), file=sys.stderr, flush=True)


def sur_evenement(d, groupe):
    """Appelé par le récepteur WAHA : traite l'événement dans un fil à part pour répondre tout de suite à WAHA."""
    try:
        if not groupe or d.get("event") not in ("message", "message.any"):
            return
        note_forme(d)
        msg = from_waha(d, groupe)
        if not msg:
            return

        def travail():
            try:
                emoji = traite(msg)
                if emoji:
                    reagir(msg["id"], emoji)
            except Exception as exc:
                print("jeu : message ignoré", repr(exc), file=sys.stderr, flush=True)
        threading.Thread(target=travail, daemon=True).start()
    except Exception as exc:
        print("jeu : événement ignoré", repr(exc), file=sys.stderr, flush=True)


# ---------------------------------------------------------------- site
def rebuild_site(complet=False):
    """Complet : tout le site. Sinon : seulement les pages du jeu (rapide, sans recopier les médias)."""
    import shutil
    here = Path(__file__).parent
    out = Path(os.environ.get("OUT_DIR", "/data/out"))
    try:
        if complet:
            tmp = out.with_name(out.name + ".jeu")
            shutil.rmtree(tmp, ignore_errors=True)
            r = subprocess.run([sys.executable, str(here / "build.py"), "--data", str(DATA), "--out", str(tmp)], capture_output=True, timeout=300)
            if r.returncode == 0:
                old = out.with_name(out.name + ".old")
                shutil.rmtree(old, ignore_errors=True)
                if out.exists():
                    out.rename(old)
                tmp.rename(out)
            else:
                print("site non reconstruit :", r.stderr.decode()[-300:], file=sys.stderr, flush=True)
        else:
            out.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, str(here / "build.py"), "--data", str(DATA), "--out", str(out), "--jeu-seulement"], capture_output=True, timeout=120)
    except Exception as exc:
        print("site non reconstruit :", repr(exc), file=sys.stderr, flush=True)


# ---------------------------------------------------------------- panneau d'administration
def vue_admin():
    st, cfg = etat(), config()
    joueurs = []
    for j in (cfg or {}).get("joueurs", []):
        ps = st["joueurs"][j["slug"]]
        joueurs.append({"prenom": j["prenom"], "slug": j["slug"], "etapes": len(j["etapes"]), "valides": len(ps["valides"]),
                        "ouvert": bool(ps["ouvert"]), "expediteurs": ps["expediteurs"]})
    vus = sorted(({"cle": k, **v} for k, v in st["vus"].items()), key=lambda x: -x.get("dernier", 0))[:30]
    return {"configure": bool(cfg), "arme": st["arme"], "demarre": st["demarre"], "rythme": st["rythme"], "test": st["test"],
            "joueurs": joueurs, "vus": vus}


def action_admin(path, body):
    if path == "api/jeu":
        return vue_admin()
    if path == "api/jeu/charger":
        cfg = body.get("config")
        err = valider_config(cfg)
        if err:
            return {"ok": False, "erreur": err}
        with verrou():
            wr(JEU / "config.json", cfg)
            sauve(etat())
        rebuild_site(complet=True)
        return {"ok": True, "joueurs": len(cfg["joueurs"])}
    if path == "api/jeu/regler":
        with verrou():
            st = etat()
            for k in ("arme", "test"):
                if k in body:
                    st[k] = bool(body[k])
            if body.get("rythme") in ("jour", "immediat"):
                st["rythme"] = body["rythme"]
            sauve(st)
        return {"ok": True}
    if path == "api/jeu/demarrer":
        return {"ok": demarrer(force=bool(body.get("force")))}
    if path == "api/jeu/associer":
        with verrou():
            st = etat()
            ps = st["joueurs"].get(body.get("slug"))
            if ps is None:
                return {"ok": False, "erreur": "joueur inconnu"}
            cle = body.get("cle")
            ps["expediteurs"] = list(st["vus"].get(cle, {}).get("ids", [])) if cle else []
            sauve(st)
        return {"ok": True}
    if path == "api/jeu/debloquer":
        with verrou():
            st, cfg = etat(), config()
            j = next((x for x in (cfg or {}).get("joueurs", []) if x["slug"] == body.get("slug")), None)
            ps = st["joueurs"].get(body.get("slug"))
            if not j or ps is None or not st["demarre"] or ps["ouvert"] or len(ps["valides"]) >= len(j["etapes"]):
                return {"ok": False, "erreur": "rien à débloquer"}
            _ouvrir(st, j, datetime.now(TZ))
            sauve(st)
        rebuild_site(complet=False)
        return {"ok": True}
    if path == "api/jeu/reinitialiser":
        if body.get("confirmation") != "REINITIALISER":
            return {"ok": False, "erreur": "confirmation manquante"}
        with verrou():
            stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            for nom in ("state.json", "activite.jsonl", "debug.jsonl"):
                f = JEU / nom
                if f.exists():
                    f.rename(JEU / f"{nom}.{stamp}.bak")
        rebuild_site(complet=True)
        return {"ok": True}
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "tick":
        print("changé" if tick() else "rien")
    else:
        print(__doc__)
