(function () {
  var D = JSON.parse(document.getElementById("ph-data").textContent);
  var slotsEl = document.getElementById("ph-slots"), bacEl = document.getElementById("ph-bac"), aideEl = document.getElementById("ph-aide");
  var msgEl = document.getElementById("ph-msg"), compteEl = document.getElementById("ph-compte");
  var cv = document.getElementById("ph-fw"), qf = document.getElementById("ph-q");
  var J = new URLSearchParams(location.search).get("j") || "";
  var S = null, sel = null, busy = false, codeVu = false;

  function appel(extra) {
    var b = { demo: !!D.demo, j: J };
    for (var k in extra) b[k] = extra[k];
    return fetch("gagne", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(function (r) { return r.json(); });
  }
  function maj(r) {
    if (!r || !r.ok) return;
    var avant = S && S.gagne;
    S = r; rend();
    if (r.gagne && !avant) victoire(r.gagne);
  }
  function poll() {
    if (document.hidden || busy || drag) return;
    appel({ etat: 1 }).then(maj).catch(function () {});
  }

  function badge(txt, cls) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "ph-badge " + (cls || ""); b.textContent = txt;
    return b;
  }
  function rend() {
    if (!S) return;
    var mon = S.mon, tous = S.tous;
    slotsEl.innerHTML = ""; bacEl.innerHTML = "";
    for (var i = 0; i < S.n; i++) {
      var s = document.createElement("div"); s.dataset.i = i;
      var mot = S.grille[i];
      var mien = (mon && mon.pose === i) || (tous && tous.some(function (x) { return x.pose === i && J && x.j === J; }));
      s.className = "ph-slot" + (mot ? " plein" : "") + (mien ? " moi" : "");
      if (mot) { var b = badge(mot, mien || (D.demo && !J) ? "mien" : "autre"); b.dataset.pose = i; s.appendChild(b); } else s.textContent = (i + 1);
      slotsEl.appendChild(s);
    }
    compteEl.textContent = S.trouves + " mot" + (S.trouves > 1 ? "s" : "") + " trouvé" + (S.trouves > 1 ? "s" : "") + " sur " + S.n;
    var peut = !S.gagne;
    if (S.gagne) { aideEl.textContent = ""; msgEl.textContent = "🎉 C'est gagné !!"; msgEl.className = "ph-msg gagne"; }
    else {
      msgEl.className = "ph-msg";
      msgEl.textContent = S.plein ? "Les " + S.n + " mots sont posés, mais ce n'est pas encore la bonne phrase… discutez et corrigez !" : "";
      if (D.demo && !J) aideEl.textContent = "Test : touche un mot puis sa case.";
      else if (mon) aideEl.textContent = mon.pose === null ? "Ton mot : touche-le, puis touche la case où il va (ou touche directement la case)." : "Ton mot est posé. Touche-le pour le retirer ou le déplacer.";
      else aideEl.textContent = "Chacun ne pose que son propre mot : ouvre cette page depuis ta page de jeu une fois ton parcours fini.";
    }
    var liste = mon ? [{ j: J, mot: mon.mot, pose: mon.pose }] : (tous && !J ? tous : []);
    liste.forEach(function (x) {
      if (x.pose === null && peut) {
        var b = badge(x.mot, "mien" + (sel && sel.j === x.j ? " sel" : "")); b.dataset.j = x.j; bacEl.appendChild(b);
      }
    });
    if (!bacEl.children.length) bacEl.hidden = true; else bacEl.hidden = false;
  }

  function poser(i, j) {
    busy = true;
    appel({ poser: i, j: j || J }).then(function (r) { busy = false; sel = null; maj(r); }).catch(function () { busy = false; });
  }
  function retirer(j) {
    busy = true;
    appel({ retirer: 1, j: j || J }).then(function (r) { busy = false; sel = null; maj(r); }).catch(function () { busy = false; });
  }
  // Glisser-déposer : un de tes mots (dans le bac ou déjà posé) vers une case ; déposé hors des cases, un mot posé retourne au bac
  var drag = null, glisse = false;
  function jDe(b) {
    if (b.dataset.j !== undefined) return b.dataset.j;
    var i = +b.dataset.pose;
    return J || (S.tous && (S.tous.filter(function (x) { return x.pose === i; })[0] || {}).j) || "";
  }
  document.addEventListener("pointerdown", function (e) {
    if (!S || S.gagne) return;
    var b = e.target.closest && e.target.closest(".ph-badge.mien");
    if (!b) return;
    drag = { x: e.clientX, y: e.clientY, b: b, bouge: false, g: null, j: jDe(b), depuisSlot: b.dataset.pose !== undefined };
  });
  document.addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.bouge && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 8) {
      drag.bouge = true;
      drag.g = drag.b.cloneNode(true); drag.g.classList.add("ph-fantome"); document.body.appendChild(drag.g);
      drag.b.classList.add("leve");
    }
    if (drag.g) { drag.g.style.left = e.clientX + "px"; drag.g.style.top = e.clientY + "px"; e.preventDefault(); }
  }, { passive: false });
  function finGlisse(e, annule) {
    if (!drag) return;
    var d = drag; drag = null;
    if (d.g) d.g.remove();
    d.b.classList.remove("leve");
    if (!d.bouge) return;
    glisse = true; setTimeout(function () { glisse = false; }, 0);
    if (annule) return;
    var cible = document.elementFromPoint(e.clientX, e.clientY);
    var slot = cible && cible.closest(".ph-slot"), bac = cible && cible.closest(".ph-bac");
    if (slot) poser(+slot.dataset.i, d.j);
    else if (d.depuisSlot && !slot) retirer(d.j);
  }
  document.addEventListener("pointerup", function (e) { finGlisse(e, false); });
  document.addEventListener("pointercancel", function (e) { finGlisse(e, true); });

  document.addEventListener("click", function (e) {
    if (glisse || !S || S.gagne) return;
    var b = e.target.closest && e.target.closest(".ph-badge");
    var slot = e.target.closest && e.target.closest(".ph-slot");
    if (b && b.dataset.j !== undefined) { sel = (sel && sel.j === b.dataset.j) ? null : { j: b.dataset.j }; rend(); return; }
    if (b && b.dataset.pose !== undefined && b.classList.contains("mien")) {
      var i = +b.dataset.pose;
      var j = J || (S.tous && (S.tous.filter(function (x) { return x.pose === i; })[0] || {}).j);
      retirer(j); return;
    }
    if (slot && !b) {
      var i2 = +slot.dataset.i;
      if (sel) poser(i2, sel.j);
      else if (S.mon && S.mon.pose === null) poser(i2, J);
    }
  });

  function victoire(quand) {
    var cle = "ph-feu-" + (D.demo ? "d" : "r"), vu = "";
    try { vu = localStorage.getItem(cle) || ""; } catch (e) {}
    if (String(vu) !== String(quand)) { try { localStorage.setItem(cle, String(quand)); } catch (e) {} feu(); }
    qf.hidden = !!codeVu;
    try { var memo = !D.demo && localStorage.getItem("ph-code"); if (memo) code(memo); } catch (e) {}
  }
  qf.addEventListener("submit", function (e) {
    e.preventDefault();
    var m = document.getElementById("ph-q-m"), rep = document.getElementById("ph-q-r").value;
    m.textContent = "…";
    appel({ reponse: rep })
      .then(function (r) {
        if (r && r.ok && r.code) { m.textContent = "Mot de passe accepté. 🤫"; qf.hidden = true; code(r.code); }
        else if (r && r.ok) { m.textContent = "Mot de passe accepté, mais aucun code n'est encore réglé : prévenez l'organisateur."; }
        else { m.textContent = "Ce n'est pas la bonne réponse… vous avez bien regardé OSS 117 ?"; }
      })
      .catch(function () { m.textContent = "La vérification n'a pas marché, réessaie."; });
  });
  function code(c) {
    if (!c) return;
    codeVu = true; qf.hidden = true;
    document.getElementById("ph-code-v").textContent = c;
    document.getElementById("ph-code").hidden = false;
    try { if (!D.demo) localStorage.setItem("ph-code", c); } catch (e) {}
  }

  function feu() {
    if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var c = cv, x = c.getContext("2d"), W, H, ps = [], t0 = performance.now(), dernier = 0;
    c.className = "actif";
    function taille() { W = c.width = innerWidth; H = c.height = innerHeight; }
    taille(); addEventListener("resize", taille);
    var couleurs = ["#ff5a4f", "#ffd24a", "#5ac8fa", "#6be28b", "#c58bff", "#ffffff"];
    function gerbe() {
      var cx = W * (0.15 + Math.random() * 0.7), cy = H * (0.12 + Math.random() * 0.4), col = couleurs[(Math.random() * couleurs.length) | 0];
      for (var i = 0; i < 70; i++) {
        var a = Math.random() * 6.283, v = 1.5 + Math.random() * 4.5;
        ps.push({ x: cx, y: cy, vx: Math.cos(a) * v, vy: Math.sin(a) * v, vie: 1, col: col });
      }
    }
    (function tour(now) {
      var dt = now - t0;
      x.clearRect(0, 0, W, H);
      if (dt < 9000 && now - dernier > 450) { gerbe(); dernier = now; }
      ps = ps.filter(function (p) { return p.vie > 0.02; });
      ps.forEach(function (p) {
        p.x += p.vx; p.y += p.vy; p.vy += 0.06; p.vx *= 0.99; p.vie *= 0.975;
        x.globalAlpha = p.vie; x.fillStyle = p.col; x.beginPath(); x.arc(p.x, p.y, 2.4, 0, 6.283); x.fill();
      });
      x.globalAlpha = 1;
      if (dt < 11000 || ps.length) requestAnimationFrame(tour); else { c.className = ""; x.clearRect(0, 0, W, H); }
    })(performance.now());
  }
  S = null; poll(); setInterval(poll, 3000);
})();
