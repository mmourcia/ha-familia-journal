#!/usr/bin/env python3
"""Génère le site « La Familia » à partir de données JSON.

Usage : python3 build.py --data data/demo --out out

Dossier de données :
  categories.json  site + liste des catégories (modifiable à la main)
  items.json       tout ce que HA a collecté (messages, photos, vidéos)
  enrich.json      ce que Claude ajoute : classement, articles, légendes, éléments mis de côté
  media/           photos et vidéos référencées par items.json

Principe : items.json suffit pour produire un site complet. Si enrich.json est vide ou absent,
tout apparaît dans « En vrac », rien n'est perdu.
"""
import argparse
import hashlib
import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
JOURS_L = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
MOIS_C = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
e = html.escape


def rd(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def dt(ts):
    return datetime.fromtimestamp(ts, TZ)


def fmt_day(ts):
    d = dt(ts)
    return f"{JOURS_L[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def fmt_short(ts):
    d = dt(ts)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS_C[d.month - 1]} · {d:%H:%M}"


def fmt_date_c(ts):
    d = dt(ts)
    return f"{JOURS[d.weekday()].capitalize()} {d.day} {MOIS_C[d.month - 1]}"


def av(name):
    return "c%d" % (int(hashlib.md5(name.encode()).hexdigest(), 16) % 5 + 1)


def para(text):
    """Texte brut -> HTML (échappé, retours à la ligne conservés)."""
    return e(text).replace("\n", "<br>")


def level(words):
    minutes = max(1, round(words / 200))
    if minutes <= 1:
        return "v", minutes
    if minutes <= 3:
        return "b", minutes
    if minutes <= 5:
        return "r", minutes
    return "n", minutes


LEVEL_NAME = {"v": "verte", "b": "bleue", "r": "rouge", "n": "noire"}


class Site:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        cfg = rd(self.dir / "categories.json", {})
        self.meta = {
            "title": "La Familia", "tagline": "Journal du séjour", "place": "", "edition_label": "Édition du",
            **cfg.get("site", {}),
        }
        self.cats = cfg.get("categories", [])
        self.fallback = cfg.get("fallback", {"slug": "en-vrac", "name": "En vrac", "description": "Ce qui n'a pas encore trouvé sa rubrique."})
        self.items = rd(self.dir / "items.json", [])
        self.enrich = rd(self.dir / "enrich.json", {})
        self.held = set(self.enrich.get("hold", {}).keys())
        self.captions = self.enrich.get("captions", {})
        # Tableaux, captures et documents : affichés en entier, jamais rognés
        self.fit = set(self.enrich.get("fit", []))
        # Éléments marqués importants : alimentent la une et les temps forts du jour
        self.important = set(self.enrich.get("importants", []))
        self.articles = sorted(self.enrich.get("articles", []), key=lambda a: a.get("date", 0), reverse=True)
        self.by_id = {i["id"]: i for i in self.items}
        slugs = {c["slug"] for c in self.cats}
        cls = self.enrich.get("classification", {})
        self.reviewed = set(self.enrich.get("reviewed", []))
        self.visible = []
        self.held_items = []
        self.pending = []
        self.unclassified = []
        self.cat_of = {}
        for it in sorted(self.items, key=lambda x: x["ts"], reverse=True):
            if it.get("hold") or it["id"] in self.held:
                self.held_items.append(it["id"])
                continue
            # Une photo ou une vidéo n'est publiée qu'après avoir été examinée (liste « reviewed »).
            if it["kind"] in ("photo", "video") and it["id"] not in self.reviewed:
                self.pending.append(it["id"])
                continue
            mine = [s for s in cls.get(it["id"], []) if s in slugs]
            if not mine:
                mine = [self.fallback["slug"]]
                self.unclassified.append(it["id"])
            self.cat_of[it["id"]] = mine
            self.visible.append(it)
        if self.unclassified:
            self.cats_all = self.cats + [self.fallback]
        else:
            self.cats_all = list(self.cats)
        self.cat_by_slug = {c["slug"]: c for c in self.cats_all}

    def latest_day(self):
        return dt(self.visible[0]["ts"]).date() if self.visible else None

    def highlights(self):
        """Faits marquants, dans cet ordre : ce qu'un membre a marqué 📰 (36 h), puis ce que Claude juge important
        aujourd'hui, puis les photos et vidéos du jour. Le premier va à la une."""
        if not self.visible:
            return []
        newest, day = self.visible[0]["ts"], self.latest_day()
        marques = [i for i in self.visible if i.get("une") and i["ts"] >= newest - 36 * 3600]
        today = [i for i in self.visible if dt(i["ts"]).date() == day and not i.get("une")]
        importants = [i for i in today if i["id"] in self.important]
        medias = [i for i in today if i["id"] not in self.important and i["kind"] in ("photo", "video")]
        out, vus = [], set()
        for i in marques + importants + medias:
            if i["id"] not in vus:
                vus.add(i["id"])
                out.append(i)
        return out[:6]

    def hero_item(self, p):
        """La une quand elle montre un fait marquant : photo, vidéo ou message."""
        cap = self.captions.get(p["id"]) or ""
        txt = (p.get("text") or "").strip()
        kick = ("Choix de la famille" if p.get("une") else "À la une")
        who = f'<div class="by label">{e(p["author"])} · {e(fmt_short(p["ts"]))}</div>'
        kicker = f'<div class="kicker"><i class="dot r"></i><span class="label">{kick}</span></div>'
        if p["kind"] == "photo":
            fit = " fit" if self.is_fit(p) else ""
            media = (f'<a class="photo real hero{fit}" href="{e(p["media"])}" data-full>'
                     f'<img src="{e(p["media"])}" alt="{e(cap or txt[:90])}"></a>')
        elif p["kind"] == "video":
            poster = f' poster="{e(p["poster"])}"' if p.get("poster") else ""
            media = (f'<div class="hero-video"><video controls preload="metadata" playsinline{poster}>'
                     f'<source src="{e(p["media"])}"></video></div>')
        else:
            return (f'<article>{kicker}<blockquote class="herotext"><p>{para(txt)}</p></blockquote>{who}</article>')
        titre = cap or (txt if len(txt) <= 90 else fmt_day(p["ts"]).capitalize())
        suite = txt if txt and txt != titre else ""
        return (f'<article>{media}{kicker}<h2>{e(titre)}</h2>'
                f'{f"<p class=chapo>{para(suite)}</p>" if suite else ""}{who}</article>')

    def pick_featured(self):
        """La une : l'article le plus récent. Si plusieurs sont du même jour, le choix de Claude l'emporte."""
        if not self.articles:
            return None
        newest = dt(self.articles[0]["date"]).date()
        same = [a for a in self.articles if dt(a["date"]).date() == newest]
        # Une brève (moins de 120 mots) ne passe en une que s'il n'y a aucun récit ce jour-là
        recits = [a for a in same if self.article_words(a) >= 120]
        pool = recits or same
        for a in pool:
            if a["id"] == self.enrich.get("featured"):
                return a
        return max(pool, key=lambda a: (self.article_words(a), len(a.get("items", []))))

    def is_fit(self, it):
        return bool(it.get("fit")) or it["id"] in self.fit

    # --- sélection ---
    def in_cat(self, slug):
        return [i for i in self.visible if slug in self.cat_of[i["id"]]]

    def photos(self, items=None):
        return [i for i in (items if items is not None else self.visible) if i["kind"] == "photo"]

    def videos(self, items=None):
        return [i for i in (items if items is not None else self.visible) if i["kind"] == "video"]

    def texts(self, items=None):
        return [i for i in (items if items is not None else self.visible) if i["kind"] == "text"]

    def article_words(self, a):
        return sum(len(p.split()) for p in a.get("paragraphs", [])) + len(a.get("chapo", "").split())

    # --- composants ---
    def texte_dessous(self, it, vu):
        """Le message écrit avec la photo ou la vidéo : toujours visible en entier, jamais remplacé par la légende."""
        txt = (it.get("text") or "").strip()
        return txt if txt and txt != vu else ""

    def photo_tile(self, it, base):
        cap = self.captions.get(it["id"]) or ""
        txt = (it.get("text") or "").strip()
        # Sur l'image : la légende, sinon un message court. Le message complet est écrit sous l'image.
        vu = cap or (txt if len(txt) <= 90 else "")
        who = f"{it['author']} · {fmt_short(it['ts'])}"
        fit = " fit" if self.is_fit(it) else ""
        tile = (
            f'<a class="photo real{fit}" href="{base}{e(it["media"])}" data-full>'
            f'<img loading="lazy" src="{base}{e(it["media"])}" alt="{e(cap or txt[:90] or "Photo de " + it["author"])}">'
            f'<div class="cap">{e(who)}{" · " + e(vu) if vu else ""}</div></a>'
        )
        below = self.texte_dessous(it, vu)
        return f'<div class="pcard">{tile}<p class="ptext">{para(below)}</p></div>' if below else tile

    def video_card(self, it, base):
        cap = self.captions.get(it["id"]) or ""
        poster = f' poster="{base}{e(it["poster"])}"' if it.get("poster") else ""
        below = self.texte_dessous(it, cap)
        return (
            f'<figure class="vid"><video controls preload="metadata" playsinline{poster}>'
            f'<source src="{base}{e(it["media"])}"></video>'
            f'<figcaption><b>{e(it["author"])}</b> · {e(fmt_short(it["ts"]))}{" · " + e(cap) if cap else ""}</figcaption>'
            f'{f"<p class=ptext>{para(below)}</p>" if below else ""}</figure>'
        )

    def msg(self, it):
        return (
            f'<div class="msg"><span class="av {av(it["author"])}">{e(it["author"][:1].upper())}</span>'
            f'<div><b>{e(it["author"])}</b><time>{e(fmt_short(it["ts"]))}</time><p>{para(it["text"])}</p></div></div>'
        )

    def cat_chip(self, slug, base):
        c = self.cat_by_slug.get(slug)
        if not c:
            return ""
        return f'<a class="chip" href="{base}cat/{e(slug)}.html">{e(c["name"])}</a>'

    def article_card(self, a, base, big=False):
        w = self.article_words(a)
        lv, mn = level(w)
        cover = self.by_id.get(a.get("cover") or "")
        if cover and cover["kind"] == "photo":
            fit = " fit" if self.is_fit(cover) else ""
            pic = f'<div class="photo real{fit}"><img loading="lazy" src="{base}{e(cover["media"])}" alt=""></div>'
        else:
            pic = '<div class="photo"></div>'
        cat = self.cat_by_slug.get(a.get("category", ""))
        return (
            f'<article class="acard{" big" if big else ""}"><a href="{base}article/{e(a["id"])}.html" class="acard-in">{pic}'
            f'<div class="kicker"><i class="dot {lv}"></i><span class="label">{e(cat["name"]) + " · " if cat else ""}{mn} min</span></div>'
            f'<h3>{e(a["title"])}</h3><p>{e(a.get("chapo", ""))}</p></a></article>'
        )

    # --- mise en page ---
    def layout(self, title, body, base, active, compact=True):
        m = self.meta
        words = m["title"].split()
        h1 = (" ".join(words[:-1]) + " " if len(words) > 1 else "") + f"<span>{e(words[-1])}</span>"
        links = [f'<a class="{"on" if active == "home" else ""}" href="{base}index.html">À la une</a>']
        for c in self.cats_all:
            links.append(f'<a class="{"on" if active == c["slug"] else ""}" href="{base}cat/{e(c["slug"])}.html">{e(c["name"])}</a>')
        links.append(f'<a class="{"on" if active == "fil" else ""}" href="{base}fil.html">Le fil</a>')
        ridges = (
            '<div class="ridges" aria-hidden="true"><svg viewBox="0 0 1200 190" preserveAspectRatio="none">'
            '<path d="M0 120 L90 70 L150 100 L260 30 L340 90 L420 55 L520 110 L640 40 L740 95 L840 60 L960 115 L1060 45 L1200 105 L1200 190 L0 190Z" fill="var(--ridge-1)"/>'
            '<path d="M0 150 L110 105 L200 135 L300 85 L420 140 L540 95 L660 145 L780 100 L900 150 L1020 110 L1120 140 L1200 120 L1200 190 L0 190Z" fill="var(--ridge-2)"/>'
            '<path d="M0 175 L140 140 L260 165 L400 130 L560 170 L700 138 L860 172 L1000 142 L1200 170 L1200 190 L0 190Z" fill="var(--ridge-3)"/>'
            '<path d="M0 190 L0 184 L220 178 L520 186 L840 176 L1200 184 L1200 190Z" fill="var(--snow)"/></svg></div>'
        )
        sub = ""
        if not compact:
            latest = self.visible[0]["ts"] if self.visible else None
            when = f"{m['edition_label']} {fmt_day(latest)}" if latest else "Première édition à venir"
            sub = (
                f'<div class="sub"><span><b>{e(when)}</b></span>'
                '<span class="sign"><i class="dot v"></i>1 min</span><span class="sign"><i class="dot b"></i>3 min</span>'
                '<span class="sign"><i class="dot r"></i>5 min</span><span class="label">Durée de lecture, comme sur les pistes</span></div>'
            )
        label = " · ".join(x for x in [m["tagline"], m.get("place", "")] if x)
        return (
            '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex, nofollow">'
            f'<title>{e(title)}</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap">'
            f'<link rel="stylesheet" href="{base}assets/style.css"></head><body>'
            f'<header class="mast band{" compact" if compact else ""}"><div class="wrap mast-in"><div class="bar">'
            f'<span class="label">{e(label)}</span><button class="toggle" id="theme" type="button" aria-label="Changer de thème">Jour / nuit</button></div>'
            f'<a class="home" href="{base}index.html"><h1>{h1}</h1></a>{sub}</div>{ridges}</header>'
            f'<nav class="plan band" aria-label="Rubriques"><div class="wrap">{"".join(links)}</div></nav>'
            f'<main>{body}</main>'
            f'<footer class="band"><div class="wrap"><div><div class="big">{e(m["title"])}</div>'
            '<p>Un journal tenu à plusieurs, mis à jour deux fois par jour avec ce qui circule dans le groupe.</p></div>'
            '<div><span class="label">Accès réservé à la famille</span></div></div></footer>'
            f'<div class="lb" id="lb" hidden><button type="button" aria-label="Fermer">×</button><img alt=""></div>'
            f'<script src="{base}assets/app.js"></script></body></html>'
        )

    # --- pages ---
    def page_home(self):
        featured = self.pick_featured()
        fil = self.texts()[:6]
        hl = self.highlights()  # faits marquants : la une montre des moments, les articles viennent dessous
        hero_article = bool(featured) and not hl
        if hl:
            une = self.hero_item(hl[0])
        elif featured:
            lv, mn = level(self.article_words(featured))
            cat = self.cat_by_slug.get(featured.get("category", ""))
            cover = self.by_id.get(featured.get("cover") or "")
            if cover and cover["kind"] == "photo":
                fit = " fit" if self.is_fit(cover) else ""
                pic = f'<div class="photo real hero{fit}"><img src="{cover["media"]}" alt=""></div>'
            else:
                pic = '<div class="photo hero"><span class="soon">Photo à venir</span></div>'
            une = (
                f'<article><a class="plain" href="article/{e(featured["id"])}.html">{pic}'
                f'<div class="kicker"><i class="dot {lv}"></i><span class="label">{e(cat["name"]) + " · " if cat else ""}{mn} min</span></div>'
                f'<h2>{e(featured["title"])}</h2><p class="chapo">{e(featured.get("chapo", ""))}</p></a>'
                f'<div class="by label">{e(fmt_date_c(featured["date"]))}</div></article>'
            )
        elif [i for i in self.highlights() if i["kind"] == "photo"]:
            # Pas encore d'article : la une est la photo marquante de la journée
            p = [i for i in self.highlights() if i["kind"] == "photo"][0]
            cap = self.captions.get(p["id"]) or p.get("text") or "Les temps forts de la journée."
            une = (
                f'<article><div class="photo real hero{" fit" if self.is_fit(p) else ""}"><img src="{e(p["media"])}" alt="{e(cap)}"></div>'
                f'<div class="kicker"><i class="dot v"></i><span class="label">À la une aujourd\'hui</span></div>'
                f'<h2>{e(fmt_day(p["ts"]).capitalize())}</h2><p class="chapo">{e(cap)}</p>'
                f'<div class="by label">{e(p["author"])} · {e(fmt_short(p["ts"]))}</div></article>'
            )
        else:
            une = (
                '<article><div class="photo hero"><span class="soon">Photo à venir</span></div>'
                '<div class="kicker"><i class="dot v"></i><span class="label">Bientôt</span></div>'
                '<h2>Le journal démarre bientôt</h2><p class="chapo">Les premières photos, vidéos et messages de la famille apparaîtront ici dès la première édition.</p></article>'
            )
        fil_html = "".join(self.msg(i) for i in fil) or '<p class="empty">Aucun message pour le moment.</p>'
        body = [f'<section class="band" id="une"><div class="wrap une">{une}<aside class="fil" aria-label="Dans le fil"><h3>Dans le fil</h3>{fil_html}'
                '<a class="more" href="fil.html">Tout le fil →</a></aside></div></section>']
        hl = hl[1:]  # le premier fait marquant est déjà à la une
        if hl:
            media = [i for i in hl if i["kind"] == "photo"]
            vids = [i for i in hl if i["kind"] == "video"]
            msgs = [i for i in hl if i["kind"] == "text"][:4]
            parts = []
            if media:
                parts.append(f'<div class="gal">{"".join(self.photo_tile(p, "") for p in media)}</div>')
            if vids:
                parts.append(f'<div class="vids">{"".join(self.video_card(v, "") for v in vids)}</div>')
            if msgs:
                parts.append('<div class="quotes">' + "".join(
                    f'<blockquote class="q"><p>{para(t["text"])}</p><footer>{e(t["author"])} · {e(fmt_short(t["ts"]))}</footer></blockquote>'
                    for t in msgs) + '</div>')
            body.append(
                f'<section class="s band" id="temps-forts"><div class="wrap"><div class="sec-h"><h2>Faits marquants</h2>'
                f'<span class="label">{e(fmt_day(hl[0]["ts"]))}</span></div>{"".join(parts)}</div></section>'
            )
        breves = [(i, self.enrich["breves"][i["id"]]) for i in self.visible if i["id"] in self.enrich.get("breves", {})][:10]
        if breves:
            rows = []
            for i, txt in breves:
                thumb = (f'<a href="{e(i["media"])}" data-full><img class="bth" loading="lazy" src="{e(i["media"])}" alt=""></a>'
                         if i["kind"] == "photo" else "")
                rows.append(f'<li class="brv">{thumb}<div><p>{e(txt)}</p>'
                            f'<span class="label">{e(i["author"])} · {e(fmt_short(i["ts"]))}</span></div></li>')
            body.append(f'<section class="s band" id="en-bref"><div class="wrap"><div class="sec-h"><h2>En bref</h2></div><ul class="brvs">{"".join(rows)}</ul></div></section>')
        others = [a for a in self.articles if not (hero_article and a["id"] == featured["id"])][:6]
        if others:
            cards = "".join(self.article_card(a, "") for a in others)
            body.append(f'<section class="s band" id="journees"><div class="wrap"><div class="sec-h"><h2>Les journées</h2><span class="label">Un sujet commun, plusieurs voix</span></div><div class="acards">{cards}</div></div></section>')
        tiles = []
        for c in self.cats_all:
            its = self.in_cat(c["slug"])
            ph = self.photos(its)
            if ph:
                pic = f'<div class="photo real{" fit" if self.is_fit(ph[0]) else ""}"><img loading="lazy" src="{e(ph[0]["media"])}" alt=""></div>'
            else:
                pic = '<div class="photo"></div>'
            counts = f'{len(ph)} photo{"s" if len(ph) > 1 else ""} · {len(self.videos(its))} vidéo{"s" if len(self.videos(its)) > 1 else ""} · {len(self.texts(its))} message{"s" if len(self.texts(its)) > 1 else ""}'
            tiles.append(
                f'<a class="rub" href="cat/{e(c["slug"])}.html">{pic}<h3>{e(c["name"])}</h3>'
                f'<p>{e(c.get("description", ""))}</p><span class="label">{counts}</span></a>'
            )
        body.append(f'<section class="s band" id="rubriques"><div class="wrap"><div class="sec-h"><h2>Les rubriques</h2></div><div class="rubs">{"".join(tiles)}</div></div></section>')
        ph = self.photos()[:6]
        if ph:
            body.append(
                '<section class="s band" id="photos"><div class="wrap"><div class="sec-h"><h2>Dernières photos</h2>'
                f'<span class="label">{len(self.photos())} au total</span></div><div class="gal">{"".join(self.photo_tile(p, "") for p in ph)}</div></div></section>'
            )
        vd = self.videos()[:2]
        if vd:
            body.append(
                f'<section class="s band" id="videos"><div class="wrap"><div class="sec-h"><h2>Dernières vidéos</h2></div><div class="vids">{"".join(self.video_card(v, "") for v in vd)}</div></div></section>'
            )
        return self.layout(self.meta["title"] + " · Journal du séjour", "".join(body), "", "home", compact=False)

    def page_cat(self, c):
        its = self.in_cat(c["slug"])
        arts = [a for a in self.articles if a.get("category") == c["slug"]]
        parts = [
            f'<section class="band"><div class="wrap head"><span class="label">Rubrique</span><h2>{e(c["name"])}</h2>'
            f'<p class="chapo">{e(c.get("description", ""))}</p></div></section>'
        ]
        if arts:
            parts.append(f'<section class="s band"><div class="wrap"><div class="sec-h"><h2>Histoires</h2></div><div class="acards">{"".join(self.article_card(a, "../") for a in arts)}</div></div></section>')
        ph, vd, tx = self.photos(its), self.videos(its), self.texts(its)
        if ph:
            parts.append(f'<section class="s band"><div class="wrap"><div class="sec-h"><h2>Photos</h2><span class="label">{len(ph)}</span></div><div class="gal">{"".join(self.photo_tile(p, "../") for p in ph)}</div></div></section>')
        if vd:
            parts.append(f'<section class="s band"><div class="wrap"><div class="sec-h"><h2>Vidéos</h2><span class="label">{len(vd)}</span></div><div class="vids">{"".join(self.video_card(v, "../") for v in vd)}</div></div></section>')
        if tx:
            parts.append(f'<section class="s band"><div class="wrap"><div class="sec-h"><h2>Dans le fil</h2><span class="label">{len(tx)}</span></div><div class="feed">{"".join(self.msg(t) for t in tx)}</div></div></section>')
        if not (ph or vd or tx or arts):
            parts.append('<section class="s band"><div class="wrap"><p class="empty">Rien dans cette rubrique pour le moment.</p></div></section>')
        return self.layout(f'{c["name"]} · {self.meta["title"]}', "".join(parts), "../", c["slug"])

    def page_article(self, a):
        lv, mn = level(self.article_words(a))
        cat = self.cat_by_slug.get(a.get("category", ""))
        figs = []
        for iid in a.get("items", []):
            it = self.by_id.get(iid)
            if not it or it.get("hold") or iid in self.held:
                continue
            cap = self.captions.get(iid) or it.get("text") or ""
            if it["kind"] == "photo":
                figs.append(f'<figure class="fig"><a href="../{e(it["media"])}" data-full><img src="../{e(it["media"])}" alt="{e(cap)}"></a><figcaption>{e(cap)}</figcaption></figure>')
            elif it["kind"] == "video":
                figs.append(self.video_card(it, "../"))
        paras = "".join(f"<p>{para(p)}</p>" for p in a.get("paragraphs", []))
        body = (
            '<article class="band art"><div class="wrap"><div class="narrow">'
            f'<div class="kicker"><i class="dot {lv}"></i><span class="label">Piste {LEVEL_NAME[lv]} · {mn} min{" · " + e(cat["name"]) if cat else ""}</span></div>'
            f'<h2>{e(a["title"])}</h2><p class="chapo">{e(a.get("chapo", ""))}</p>'
            f'<div class="by label">{e(a.get("author", "La rédaction"))} · {e(fmt_date_c(a["date"]))}</div>'
            f'<div class="body-text">{paras}</div>{"".join(figs)}'
            '<p><a class="more" href="../index.html">← Retour à la une</a></p></div></div></article>'
        )
        return self.layout(f'{a["title"]} · {self.meta["title"]}', body, "../", a.get("category", ""))

    def page_fil(self):
        days = {}
        for it in self.visible:
            days.setdefault(dt(it["ts"]).date(), []).append(it)
        parts = ['<section class="band"><div class="wrap head"><span class="label">Tout ce qui circule</span><h2>Le fil</h2></div></section>']
        for d in sorted(days, reverse=True):
            its = days[d]
            ts = its[0]["ts"]
            rows = []
            for it in its:
                if it["kind"] == "text":
                    rows.append(self.msg(it))
                elif it["kind"] == "photo":
                    rows.append(f'<div class="msg"><span class="av {av(it["author"])}">{e(it["author"][:1].upper())}</span><div><b>{e(it["author"])}</b><time>{e(fmt_short(it["ts"]))}</time>'
                                f'<div class="inline">{self.photo_tile(it, "")}</div></div></div>')
                else:
                    rows.append(f'<div class="msg"><span class="av {av(it["author"])}">{e(it["author"][:1].upper())}</span><div>{self.video_card(it, "")}</div></div>')
            parts.append(f'<section class="s band"><div class="wrap"><div class="sec-h"><h2>{e(fmt_day(ts))}</h2><span class="label">{len(its)}</span></div><div class="feed">{"".join(rows)}</div></div></section>')
        if not days:
            parts.append('<section class="s band"><div class="wrap"><p class="empty">Aucun message pour le moment.</p></div></section>')
        return self.layout(f'Le fil · {self.meta["title"]}', "".join(parts), "", "fil")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    here = Path(__file__).parent
    s = Site(args.data)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "cat").mkdir(parents=True)
    (out / "article").mkdir()
    shutil.copytree(here / "static", out / "assets")
    media = Path(args.data) / "media"
    if media.exists():
        shutil.copytree(media, out / "media")
    (out / "index.html").write_text(s.page_home(), encoding="utf-8")
    (out / "fil.html").write_text(s.page_fil(), encoding="utf-8")
    for c in s.cats_all:
        (out / "cat" / f'{c["slug"]}.html').write_text(s.page_cat(c), encoding="utf-8")
    for a in s.articles:
        (out / "article" / f'{a["id"]}.html').write_text(s.page_article(a), encoding="utf-8")
    report = {
        "items": len(s.items), "visibles": len(s.visible), "articles": len(s.articles),
        "mis_de_cote": s.held_items, "en_attente_de_revue": s.pending, "sans_categorie": s.unclassified,
    }
    (Path(args.data) / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
