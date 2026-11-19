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
import hashlib
import html as _html
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
DATA = Path(os.environ.get("DATA_DIR", "data/real"))
JEU = Path(os.environ.get("JEU_DIR") or (DATA.resolve().parent / "jeu"))
SESSION = os.environ.get("WAHA_SESSION", "default")
HEURE_OUVERTURE = 7  # une étape validée s'ouvre le lendemain à cette heure (rythme « jour »)

RE_DEUX_POINTS = re.compile(r"^\s*(start|jeu)\s*:\s*(.*?)\s*$", re.I | re.S)
RE_LIBRE = re.compile(r"^\s*jeu\b[\s:]*(\S.*?)\s*$", re.I | re.S)
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


def evt(texte):
    """Journal du jeu pour le panneau : un prénom et ce qui s'est passé, jamais le texte d'une réponse."""
    f = JEU / "evenements.jsonl"
    try:
        lignes = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        f.write_text("\n".join(lignes[-79:] + [json.dumps({"t": int(time.time()), "m": texte}, ensure_ascii=False)]) + "\n", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- configuration et état
def config():
    return rd(JEU / "config.json", None)


def etat():
    st = {"arme": False, "demarre": 0, "rythme": "jour", "test": False, "reactions": True, "delai": [5, 20],
          "prives": False, "delai_prive": [20, 60], "prives_envoyes": {}, "prives_erreurs": {},
          "joueurs": {}, "vus": {}, "traites": []}
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
    evt(f"Étape {i + 1}/{len(cfg_j['etapes'])} ouverte pour {cfg_j['prenom']}")
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


def deux_groupes():
    d, j = os.environ.get("JEU_DEPART_ID", ""), os.environ.get("JEU_GROUP_ID", "")
    return bool(d and j and d != j)


def role(groupe):
    """« depart » : groupe d'accueil (on y tape Start, il compte l'activité). « jeu » : groupe où se tapent les codes."""
    d = os.environ.get("JEU_DEPART_ID", "")
    return "depart" if deux_groupes() and groupe == d else "jeu"


def traite(msg, now=None):
    """Un message du groupe. Renvoie l'emoji à poser en réaction (ou None) ; le message n'est jamais republié."""
    now = now or datetime.now(TZ)
    rebuild = None
    r = role(msg.get("groupe"))
    with verrou():
        st, cfg = etat(), config()
        ts = int(msg.get("ts") or now.timestamp())
        parsed = parse(msg.get("body"))
        if not parsed and r == "jeu" and deux_groupes():
            # Dans le groupe du jeu, « jeu cage » ou « jeu los angeles » (sans les deux-points) comptent aussi
            m = RE_LIBRE.match(msg.get("body") or "")
            if m:
                parsed = ("jeu", m.group(1))
        # qui parle (pour associer les numéros aux joueurs depuis le panneau)
        if msg.get("ids"):
            cle = msg["ids"][0]
            st["vus"][cle] = {"nom": msg.get("nom") or "", "ids": msg["ids"], "dernier": ts}
        if not parsed:
            if r == "depart" or not deux_groupes():  # l'activité se compte dans le groupe d'accueil (celui de la famille)
                note_activite(msg, ts)
            sauve(st)
            return None
        kind, texte = parsed
        if deux_groupes() and ((r == "depart" and kind == "jeu") or (r == "jeu" and kind == "start")):
            sauve(st)
            return None  # mauvais groupe : pas de réponse (le message reste exclu du journal)
        if msg.get("id") in st["traites"]:
            return None
        st["traites"].append(msg.get("id"))
        emoji = "\U0001F914"  # 🤔
        if not cfg or not st["arme"]:
            emoji = "\U0001F4A4"  # 💤 : pas encore
            if kind == "start":
                msg["reponse"] = "Recommence un peu plus tard, le jeu n'est pas encore actif."
        elif kind == "start":
            if st["demarre"]:
                emoji = "\U0001F44D"  # 👍 déjà lancé
            elif norm(texte) == norm(cfg["mot_depart"]):
                st["demarre"] = int(now.timestamp())
                _tick(st, cfg, now)
                emoji, rebuild = "\U0001F513", True  # 🔓
                msg["demarrage"] = True
                evt("Le jeu a démarré" + (f" (Start de {msg['nom']})" if msg.get("nom") else ""))
                msg["reponse"] = "Bravo, le jeu va pouvoir commencer ! \U0001F389"
        elif not st["demarre"]:
            emoji = "\U0001F4A4"
        else:
            ids_msg = set(msg.get("ids") or [])
            # le joueur dont le téléphone est associé à l'expéditeur passe en premier (parcours identiques, test)
            for j in sorted(cfg["joueurs"], key=lambda x: 0 if set(st["joueurs"][x["slug"]]["expediteurs"]) & ids_msg else 1):
                ps = st["joueurs"][j["slug"]]
                i = len(ps["valides"])
                if i >= len(j["etapes"]) or not ps["ouvert"]:
                    continue
                if _reponse_ok(j["etapes"][i], ps, texte, st, now) and _expediteur_ok(st, ps, msg):
                    ps.setdefault("essais", []).append({"t": ts, "e": i, "x": str(texte)[:80], "ok": True, "nom": msg.get("nom") or ""})
                    ps["essais"] = ps["essais"][-60:]
                    ps["valides"].append(int(now.timestamp()))
                    evt(f"{j['prenom']} a validé l'étape {len(ps['valides'])}/{len(j['etapes'])}")
                    ps["ouvert"], ps["attendu"] = 0, None
                    fini = len(ps["valides"]) >= len(j["etapes"])
                    emoji = "\U0001F3C6" if fini else "\U0001F513"  # 🏆 / 🔓
                    msg["reponse"] = (f"\U0001F3C6 Bravo {j['prenom']}, ton parcours est terminé ! Recharge ta page : tu y découvriras le mot qui aidera toute la tribu à trouver la phrase code du trésor." if fini
                                      else f"\U0001F513 Bien joué {j['prenom']} ! Recharge ta page pour découvrir l'énigme suivante.")
                    _tick(st, cfg, now)  # rythme immédiat : l'étape suivante s'ouvre tout de suite
                    rebuild = False
                    break
            else:
                evt("Essai raté de " + (msg.get("nom") or "quelqu'un"))
                # Attribué au joueur dont le téléphone est associé ; sinon rangé à part (visible seulement dans le panneau)
                cand = [j for j in cfg["joueurs"] if set(st["joueurs"][j["slug"]]["expediteurs"]) & set(msg.get("ids") or [])]
                essai = {"t": ts, "x": str(texte)[:80], "ok": False, "nom": msg.get("nom") or ""}
                if cand:
                    ps2 = st["joueurs"][cand[0]["slug"]]
                    essai["e"] = len(ps2["valides"])
                    ps2.setdefault("essais", []).append(essai)
                    ps2["essais"] = ps2["essais"][-60:]
                else:
                    st.setdefault("essais_libres", []).append(essai)
                    st["essais_libres"] = st["essais_libres"][-40:]
        sauve(st)
    if rebuild is not None:
        rebuild_site(complet=bool(rebuild))
    return emoji


# ---------------------------------------------------------------- WAHA
def from_waha(d, groupes):
    """Événement « message.any » de WAHA -> dictionnaire simple, ou None (autre groupe, autre événement)."""
    if d.get("event") not in ("message", "message.any"):
        return None
    p = d.get("payload") or {}
    if isinstance(groupes, str):
        groupes = {groupes}
    if groupes and p.get("from") not in groupes:
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
            "ids": ids, "nom": nom, "fromMe": bool(p.get("fromMe")), "groupe": p.get("from") or ""}


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


_VERROU_REACTION = threading.Lock()


def sur_evenement(d, groupes=None):
    """Appelé par le récepteur WAHA : traite l'événement dans un fil à part pour répondre tout de suite à WAHA."""
    try:
        if d.get("event") not in ("message", "message.any"):
            return
        if groupes is None:
            groupes = {os.environ.get("JEU_GROUP_ID", ""), os.environ.get("JEU_DEPART_ID", "")} - {""}
        if not groupes:
            return
        note_forme(d)
        msg = from_waha(d, groupes)
        if not msg:
            return

        def travail():
            try:
                emoji = traite(msg)
                if emoji or msg.get("reponse"):
                    # Discret : une réaction à la fois, après un délai au hasard (jamais instantanée, jamais régulière),
                    # et coupable en un clic depuis le panneau (le jeu marche alors sans rien envoyer à WhatsApp).
                    st = rd(JEU / "state.json", {})
                    if st.get("reactions", True):
                        a, b = (st.get("delai") or [5, 20])[:2]
                        with _VERROU_REACTION:
                            time.sleep(random.uniform(a, max(a, b)))
                            reagir(msg["id"], emoji)
                            if msg.get("reponse") and msg.get("groupe"):
                                time.sleep(random.uniform(2, 5))
                                try:
                                    waha("POST", "/api/startTyping", {"session": SESSION, "chatId": msg["groupe"]})
                                    time.sleep(random.uniform(2, 4))
                                    waha("POST", "/api/stopTyping", {"session": SESSION, "chatId": msg["groupe"]})
                                except Exception:
                                    pass
                                waha("POST", "/api/sendText", {"session": SESSION, "chatId": msg["groupe"], "text": msg["reponse"]})
                if msg.get("demarrage"):
                    envoyer_prives(msg)
            except Exception as exc:
                print("jeu : message ignoré", repr(exc), file=sys.stderr, flush=True)
        threading.Thread(target=travail, daemon=True).start()
    except Exception as exc:
        print("jeu : événement ignoré", repr(exc), file=sys.stderr, flush=True)



# ---------------------------------------------------------------- la phrase du trésor (page commune)
def pieces(cfg):
    """[(numéro, mot, slug)] triés par numéro, d'après le champ « n°3 : MOT » de chaque joueur."""
    out = []
    for j in (cfg or {}).get("joueurs", []):
        m = re.match(r"\s*n\u00b0\s*(\d+)\s*:\s*(.+?)\s*$", str(j.get("piece", "")))
        if m:
            out.append((int(m.group(1)), m.group(2), j["slug"]))
    return sorted(out)


def jeton(cfg, slug):
    """Identifiant opaque d'une pièce dans la page : il ne révèle ni le numéro ni le joueur."""
    return hashlib.sha1(f"{slug}|{cfg.get('mot_depart', '')}|piece".encode()).hexdigest()[:8]


def envoyer_gagne():
    """« C'est gagné !! » dans le groupe d'accueil (celui de la famille)."""
    g = os.environ.get("JEU_DEPART_ID") or os.environ.get("JEU_GROUP_ID", "")
    if not g:
        return
    try:
        waha("POST", "/api/startTyping", {"session": SESSION, "chatId": g})
        time.sleep(random.uniform(2, 4))
        waha("POST", "/api/stopTyping", {"session": SESSION, "chatId": g})
    except Exception:
        pass
    try:
        waha("POST", "/api/sendText", {"session": SESSION, "chatId": g, "text": "C'est gagné !! \U0001F389"})
    except Exception as exc:
        print("jeu : message de victoire impossible", repr(exc), file=sys.stderr, flush=True)


def action_gagne(body):
    """Appelée par la page de la phrase : vérifie l'ordre des mots et, si c'est bon, annonce la victoire (une seule fois)."""
    cfg, st = config(), etat()
    pcs = pieces(cfg)
    if not cfg or not pcs:
        return {"ok": False}
    demo = bool(body.get("demo")) and bool(st.get("test"))
    permis = {}
    for k, mot, slug in pcs:
        j = next(x for x in cfg["joueurs"] if x["slug"] == slug)
        if demo or len(st["joueurs"][slug]["valides"]) >= len(j["etapes"]):
            permis[jeton(cfg, slug)] = mot
    mots = body.get("mots")
    if not isinstance(mots, list) or len(mots) != len(pcs) or len(set(map(str, mots))) != len(mots):
        return {"ok": False}
    suite = [permis.get(str(t)) for t in mots]
    attendu_ = [norm(m) for _, m, _ in pcs]
    if None in suite or [norm(x) for x in suite] != attendu_:
        return {"ok": False}
    envoyer = False
    with verrou():
        st = etat()
        now = int(time.time())
        if not st.get("gagne"):
            st["gagne"] = now
            evt("Phrase du trésor reconstituée : c'est gagné !")
        if not st.get("gagne_msg") or (st.get("test") and now - st["gagne_msg"] > 30):
            st["gagne_msg"] = now
            envoyer = bool(st.get("reactions", True))
        sauve(st)
    if envoyer:
        threading.Thread(target=envoyer_gagne, daemon=True).start()
    rebuild_site(complet=False)
    code = "1 2 3 4 (exemple)" if demo else re.sub(r"[^0-9A-Za-z ]", "", os.environ.get("JEU_CODE_MALLE", ""))[:12]
    return {"ok": True, "code": code}

# ---------------------------------------------------------------- messages privés au démarrage
TEXTE_ACCUEIL = ("\U0001F389 Le jeu a commencé ! Voici ton parcours, rien que pour toi : {page}\n"
                 "Pour répondre à tes énigmes, rejoins le groupe de la chasse : {groupe}\n"
                 "Écris jeu: devant chaque réponse. Bonne chance !")
TEXTE_RELAIS = ("\U0001F389 Le jeu a commencé ! Voici le parcours de {prenom} : {page}\n"
                "Pour que {prenom} puisse répondre, il faut rejoindre le groupe de la chasse : {groupe} "
                "et écrire jeu: devant chaque réponse. Merci de l'aider !")


def waha(method, path, body=None, timeout=30):
    req = urllib.request.Request(
        os.environ.get("WAHA_URL", "http://f245669a-waha:3000").rstrip("/") + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": os.environ.get("WAHA_KEY", ""), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return raw


def lien_groupe():
    """Lien d'invitation du groupe du jeu (WAHA le donne si le compte lié en est administrateur)."""
    g = os.environ.get("JEU_GROUP_ID", "")
    r = waha("GET", f"/api/{SESSION}/groups/{urllib.parse.quote(g, safe='@')}/invite-code")
    if isinstance(r, dict):
        r = next((v for v in r.values() if isinstance(v, str) and v), "")
    r = str(r or "").strip().strip('"')
    if not r:
        raise RuntimeError("lien du groupe introuvable")
    return r if r.startswith("http") else "https://chat.whatsapp.com/" + r


def lien_page(slug):
    base = rd(DATA / "admin.json", {}).get("public_url") or os.environ.get("SITE_URL", "")
    if not base:
        raise RuntimeError("adresse publique du site inconnue (à renseigner dans l'onglet État)")
    return base.rstrip("/") + f"/jeux/{slug}.html"


def chat_prive(ids):
    """Un numéro de téléphone parmi les identifiants vus (les identifiants LID, trop longs, ne servent pas à écrire)."""
    for i in ids or []:
        if re.fullmatch(r"\d{8,13}", str(i)):
            return f"{i}@c.us"
    return None


def texte_prive(cfg, j, page, groupe):
    modele = (cfg.get("message_depart_relais") if j.get("relais") else cfg.get("message_depart")) or (TEXTE_RELAIS if j.get("relais") else TEXTE_ACCUEIL)
    mdp = cfg.get("mot_de_passe") or ""
    login = cfg.get("identifiant") or ""
    t = (modele.replace("{prenom}", j["prenom"]).replace("{page}", page).replace("{groupe}", groupe)
         .replace("{mdp}", mdp).replace("{login}", login))
    if mdp and "{mdp}" not in modele:
        t += "\n\U0001F511 Si le site te demande un accès : " + (f"identifiant {login}, " if login else "") + f"mot de passe {mdp}"
    return t


def _noter_prive(slug, ok, erreur=""):
    with verrou():
        st = etat()
        if ok:
            st["prives_envoyes"][slug] = int(time.time())
            st["prives_erreurs"].pop(slug, None)
        else:
            st["prives_erreurs"][slug] = erreur
        sauve(st)


def envoyer_prives(msg=None, slug=None, force=False):
    """Envoie à chaque joueur (ou à un seul) un message privé : lien de sa page et du groupe du jeu, espacés au hasard.
    Au démarrage, seulement si l'option est active et jamais deux fois au même joueur. Renvoie un résumé."""
    st, cfg = etat(), config()
    if not cfg or not (force or st.get("prives")):
        return {"ok": False, "erreur": "messages privés désactivés"}
    try:
        groupe = lien_groupe()
    except Exception as exc:
        for j in cfg["joueurs"]:
            if not slug or j["slug"] == slug:
                _noter_prive(j["slug"], False, "lien du groupe : " + repr(exc)[:120])
        return {"ok": False, "erreur": "lien du groupe : " + repr(exc)}
    auteur = set((msg or {}).get("ids") or [])
    joueurs = [j for j in cfg["joueurs"] if not slug or j["slug"] == slug]
    joueurs.sort(key=lambda j: 0 if auteur & set(st["joueurs"][j["slug"]]["expediteurs"]) else 1)  # l'auteur du Start d'abord
    resume = {"ok": True, "envoyes": [], "sans_numero": [], "erreurs": {}}
    premier = True
    for j in joueurs:
        st = etat()
        if not force and j["slug"] in st["prives_envoyes"]:
            continue
        chat = chat_prive(st["joueurs"][j["slug"]]["expediteurs"])
        if not chat:
            resume["sans_numero"].append(j["prenom"])
            _noter_prive(j["slug"], False, "aucun numéro associé")
            continue
        try:
            if not premier:
                a, b = (st.get("delai_prive") or [20, 60])[:2]
                time.sleep(random.uniform(a, max(a, b)))
            premier = False
            texte = texte_prive(cfg, j, lien_page(j["slug"]), groupe)
            try:  # « en train d'écrire… » quelques secondes, comme une personne
                waha("POST", "/api/startTyping", {"session": SESSION, "chatId": chat})
                time.sleep(random.uniform(3, 6))
                waha("POST", "/api/stopTyping", {"session": SESSION, "chatId": chat})
            except Exception:
                pass
            waha("POST", "/api/sendText", {"session": SESSION, "chatId": chat, "text": texte})
            _noter_prive(j["slug"], True)
            resume["envoyes"].append(j["prenom"])
        except Exception as exc:
            resume["erreurs"][j["prenom"]] = repr(exc)[:120]
            _noter_prive(j["slug"], False, repr(exc)[:120])
    return resume


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
    now = datetime.now(TZ)
    joueurs = []
    for j in (cfg or {}).get("joueurs", []):
        ps = st["joueurs"][j["slug"]]
        n, total = len(ps["valides"]), len(j["etapes"])
        etapes = []
        for k, e in enumerate(j["etapes"]):
            etat_e = "fait" if k < n else ("ouvert" if k == n and ps["ouvert"] else "verrou")
            corps = e.get("html") or "<p>" + _html.escape(e.get("enonce", "")).replace("\n", "<br>") + "</p>"
            if e.get("dynamique"):
                corps += "<p><i>(réponse calculée par le jeu)</i></p>"
            etapes.append({"titre": e["titre"], "etat": etat_e, "dynamique": e.get("dynamique") or "", "html": corps,
                           "codes": e.get("codes", []),
                           "essais": [{"t": x["t"], "x": x["x"], "ok": x["ok"], "nom": x.get("nom", "")} for x in ps.get("essais", []) if x.get("e") == k][::-1],
                           "validee": ps["valides"][k] if k < n else 0})
        reponse, prochaine = "", 0
        if n < total:
            e = j["etapes"][n]
            if e.get("dynamique"):
                att = attendu(e["dynamique"], jour_ref(st, now)) if st.get("rythme") == "immediat" or not ps["ouvert"] else ps.get("attendu")
                att = att if isinstance(att, list) else ([att] if att is not None else [])
                reponse = " ou ".join(str(a) for a in att) + " (calculée)"
            else:
                reponse = " ou ".join(e.get("codes", []))
            if not ps["ouvert"] and n and st["demarre"] and st.get("rythme") != "immediat":
                h = int(cfg.get("heure_ouverture", HEURE_OUVERTURE))
                d = datetime.fromtimestamp(ps["valides"][-1], TZ)
                prochaine = int((d + timedelta(days=1)).replace(hour=h, minute=0, second=0, microsecond=0).timestamp())
        joueurs.append({"prenom": j["prenom"], "slug": j["slug"], "etapes": total, "valides": n, "liste": etapes, "reponse": reponse,
                        "ouvert": bool(ps["ouvert"]), "expediteurs": ps["expediteurs"], "relais": bool(j.get("relais")),
                        "numero": bool(chat_prive(ps["expediteurs"])), "envoye": st["prives_envoyes"].get(j["slug"], 0),
                        "erreur": st["prives_erreurs"].get(j["slug"], ""), "derniere": ps["valides"][-1] if ps["valides"] else 0,
                        "prochaine": prochaine, "piece": j.get("piece", "")})
    vus = sorted(({"cle": k, **v} for k, v in st["vus"].items()), key=lambda x: -x.get("dernier", 0))[:30]
    evenements = []
    try:
        for ligne in (JEU / "evenements.jsonl").read_text(encoding="utf-8").splitlines()[-40:]:
            evenements.append(json.loads(ligne))
    except Exception:
        pass
    activite_jour = {}
    for r in activite(now.strftime("%Y-%m-%d")):
        activite_jour[r.get("nom", "?")] = activite_jour.get(r.get("nom", "?"), 0) + 1
    return {"configure": bool(cfg), "arme": st["arme"], "demarre": st["demarre"], "rythme": st["rythme"], "test": st["test"],
            "reactions": st["reactions"], "delai": st["delai"], "prives": st["prives"], "delai_prive": st["delai_prive"],
            "public_url": (rd(DATA / "admin.json", {}).get("public_url") or os.environ.get("SITE_URL", "")).rstrip("/") + "/" if (rd(DATA / "admin.json", {}).get("public_url") or os.environ.get("SITE_URL", "")) else "",
            "gagne": st.get("gagne", 0), "deux_groupes": deux_groupes(), "mot_depart": (cfg or {}).get("mot_depart", ""),
            "heure_ouverture": int((cfg or {}).get("heure_ouverture", HEURE_OUVERTURE)),
            "activite_jour": sorted(activite_jour.items(), key=lambda x: -x[1]),
            "joueurs": joueurs, "vus": vus, "evenements": evenements[::-1], "essais_libres": st.get("essais_libres", [])[::-1][:20]}


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
    if path == "api/jeu/reinit_joueur":
        slug = body.get("slug")
        with verrou():
            st, cfg = etat(), config()
            ps = st["joueurs"].get(slug)
            j = next((x for x in (cfg or {}).get("joueurs", []) if x["slug"] == slug), None)
            if not ps or not j:
                return {"ok": False, "erreur": "joueur inconnu"}
            ps.update({"valides": [], "ouvert": 0, "attendu": None})
            ps.pop("essais", None)
            st["prives_envoyes"].pop(slug, None)
            st["prives_erreurs"].pop(slug, None)
            evt(f"Parcours de {j['prenom']} remis à zéro")
            _tick(st, cfg, datetime.now(TZ))
            sauve(st)
        rebuild_site(complet=False)
        return {"ok": True}
    if path == "api/jeu/simuler":
        # Essai à blanc : traite un message comme s'il venait du groupe, sans rien envoyer à WhatsApp
        g = os.environ.get("JEU_GROUP_ID", "") if body.get("groupe", "jeu") == "jeu" else os.environ.get("JEU_DEPART_ID", "")
        msg = {"id": "sim_" + str(time.time_ns()), "ts": int(time.time()), "body": str(body.get("texte", "")), "ids": body.get("ids") or [],
               "nom": body.get("nom") or "Simulation", "fromMe": False, "groupe": g}
        emoji = traite(msg)
        return {"ok": True, "emoji": emoji, "reponse": msg.get("reponse")}
    if path == "api/jeu/etapes":
        # Remplace des étapes sans renvoyer toute la configuration : {"joueurs": {slug: [{"garder": 0}, {étape}, ...]}}
        cfg = config()
        if not cfg:
            return {"ok": False, "erreur": "aucun parcours chargé"}
        for slug, liste in (body.get("joueurs") or {}).items():
            j = next((x for x in cfg["joueurs"] if x["slug"] == slug), None)
            if not j:
                return {"ok": False, "erreur": "joueur inconnu : " + str(slug)}
            if isinstance(liste, dict) and liste.get("copier_de"):
                src = next((x for x in cfg["joueurs"] if x["slug"] == liste["copier_de"]), None)
                if not src:
                    return {"ok": False, "erreur": "parcours à copier introuvable"}
                j["etapes"] = json.loads(json.dumps(src["etapes"]))
                continue
            ancien = j["etapes"]
            try:
                j["etapes"] = [ancien[e["garder"]] if "garder" in e else e for e in liste]
            except Exception as exc:
                return {"ok": False, "erreur": "étape à garder introuvable : " + repr(exc)}
        err = valider_config(cfg)
        if err:
            return {"ok": False, "erreur": err}
        with verrou():
            wr(JEU / "config.json", cfg)
            sauve(etat())
        rebuild_site(complet=True)
        return {"ok": True, "joueurs": len(cfg["joueurs"])}
    if path == "api/jeu/retirer":
        # Retire un joueur du parcours (et de l'état), sans renvoyer toute la configuration
        cfg = config()
        slug = body.get("slug")
        if not cfg or not any(x["slug"] == slug for x in cfg["joueurs"]):
            return {"ok": False, "erreur": "joueur inconnu"}
        cfg["joueurs"] = [x for x in cfg["joueurs"] if x["slug"] != slug]
        err = valider_config(cfg)
        if err:
            return {"ok": False, "erreur": err}
        with verrou():
            wr(JEU / "config.json", cfg)
            st = etat()
            st["joueurs"].pop(slug, None)
            sauve(st)
        rebuild_site(complet=True)
        return {"ok": True, "joueurs": len(cfg["joueurs"])}
    if path == "api/jeu/ajouter":
        # Ajoute un joueur (test) avec le parcours d'un autre : {"prenom": "Agnès", "copier_de": slug}
        cfg = config()
        prenom = str(body.get("prenom") or "").strip()
        src = next((x for x in (cfg or {}).get("joueurs", []) if x["slug"] == body.get("copier_de")), None)
        if not cfg or not prenom or not src:
            return {"ok": False, "erreur": "prénom ou parcours à copier manquant"}
        base = re.sub(r"[^a-z0-9]", "", norm(prenom)) or "joueur"
        slug = base + "-" + hashlib.sha1(f"{prenom}|{cfg.get('mot_depart', '')}|{time.time_ns()}".encode()).hexdigest()[:6]
        j = json.loads(json.dumps(src))
        j.update({"prenom": prenom, "slug": slug, "piece": "(mot à définir)", "message_fin": f"Bravo {prenom}, tu as fini ton parcours."})
        j.pop("relais", None)
        cfg["joueurs"].append(j)
        err = valider_config(cfg)
        if err:
            return {"ok": False, "erreur": err}
        with verrou():
            wr(JEU / "config.json", cfg)
            sauve(etat())
        rebuild_site(complet=True)
        return {"ok": True, "slug": slug, "joueurs": len(cfg["joueurs"])}
    if path == "api/jeu/regler":
        with verrou():
            st = etat()
            for k in ("arme", "test", "reactions", "prives"):
                if k in body:
                    st[k] = bool(body[k])
            d = body.get("delai")
            if isinstance(d, list) and len(d) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 300 for x in d):
                st["delai"] = [min(d), max(d)]
            d = body.get("delai_prive")
            if isinstance(d, list) and len(d) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 600 for x in d):
                st["delai_prive"] = [min(d), max(d)]
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
            cle, nom = body.get("cle"), body.get("nom")
            if nom and not cle:  # par prénom : tous les identifiants vus sous ce nom
                ids = []
                for v in st["vus"].values():
                    if norm(v.get("nom")) == norm(nom):
                        ids += [i for i in v["ids"] if i not in ids]
                ps["expediteurs"] = ids
            else:
                ps["expediteurs"] = list(st["vus"].get(cle, {}).get("ids", [])) if cle else []
            sauve(st)
        return {"ok": True}
    if path == "api/jeu/renvoyer":  # message privé tout de suite, à un joueur (slug) ou à tous ceux qui ne l'ont pas reçu
        slug = body.get("slug") or None
        threading.Thread(target=lambda: envoyer_prives(slug=slug, force=bool(slug)) if slug else envoyer_prives(force=True), daemon=True).start()
        return {"ok": True, "lance": True}
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
            ancien = etat()
            stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            for nom in ("state.json", "activite.jsonl", "debug.jsonl", "evenements.jsonl"):
                f = JEU / nom
                if f.exists():
                    f.rename(JEU / f"{nom}.{stamp}.bak")
            # Le jeu repart de zéro (pas démarré, aucune étape) mais garde les réglages et les téléphones associés
            garde = {k: ancien[k] for k in ("arme", "rythme", "test", "reactions", "delai", "prives", "delai_prive", "vus") if k in ancien}
            st = etat()
            st.update(garde)
            for slug, ps in ancien["joueurs"].items():
                if slug in st["joueurs"]:
                    st["joueurs"][slug]["expediteurs"] = ps.get("expediteurs", [])
            sauve(st)
            evt("Jeu remis à zéro (réglages et téléphones conservés)")
        rebuild_site(complet=True)
        return {"ok": True}
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "tick":
        print("changé" if tick() else "rien")
    else:
        print(__doc__)
