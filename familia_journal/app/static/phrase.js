(function () {
  var D = JSON.parse(document.getElementById("ph-data").textContent);
  var slotsEl = document.getElementById("ph-slots"), bacEl = document.getElementById("ph-bac");
  var msgEl = document.getElementById("ph-msg"), compteEl = document.getElementById("ph-compte");
  var cv = document.getElementById("ph-fw");
  var N = D.n, slots = new Array(N).fill(null), sel = null, fini = false, envoye = "";
  var mot = {};
  D.mots.forEach(function (x) { mot[x.t] = x.m; });
  compteEl.textContent = D.mots.length + " mot" + (D.mots.length > 1 ? "s" : "") + " trouvé" + (D.mots.length > 1 ? "s" : "") + " sur " + N;
  if (D.gagne) msgEl.textContent = "🎉 C'est gagné ! La phrase a déjà été reconstituée.";

  function badge(t) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "ph-badge" + (sel === t ? " sel" : ""); b.dataset.t = t; b.textContent = mot[t];
    return b;
  }
  function rend() {
    slotsEl.innerHTML = ""; bacEl.innerHTML = "";
    for (var i = 0; i < N; i++) {
      var s = document.createElement("div"); s.className = "ph-slot" + (slots[i] ? " plein" : ""); s.dataset.i = i;
      if (slots[i]) s.appendChild(badge(slots[i])); else s.textContent = "·";
      slotsEl.appendChild(s);
    }
    D.mots.forEach(function (x) { if (slots.indexOf(x.t) < 0) bacEl.appendChild(badge(x.t)); });
    if (!D.mots.length) bacEl.innerHTML = '<span class="label">Aucun mot pour le moment : les parcours sont en cours.</span>';
  }
  function place(t, i) {
    var a = slots.indexOf(t); if (a >= 0) slots[a] = null;
    slots[i] = t; sel = null; rend(); verifie();
  }
  function retire(t) { var a = slots.indexOf(t); if (a >= 0) slots[a] = null; sel = null; rend(); }

  var drag = null;
  document.addEventListener("pointerdown", function (e) {
    var b = e.target.closest && e.target.closest(".ph-badge"); if (!b || fini) return;
    e.preventDefault();
    drag = { t: b.dataset.t, x: e.clientX, y: e.clientY, bouge: false, g: null, b: b };
  });
  document.addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.bouge && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 6) {
      drag.bouge = true;
      drag.g = drag.b.cloneNode(true); drag.g.classList.add("ph-fantome"); document.body.appendChild(drag.g);
      drag.b.classList.add("leve");
    }
    if (drag.g) { drag.g.style.left = e.clientX + "px"; drag.g.style.top = e.clientY + "px"; e.preventDefault(); }
  }, { passive: false });
  document.addEventListener("pointerup", function (e) {
    if (!drag) return;
    var d = drag; drag = null;
    if (d.g) d.g.remove();
    d.b.classList.remove("leve");
    if (d.bouge) {
      var cible = document.elementFromPoint(e.clientX, e.clientY);
      var slot = cible && cible.closest(".ph-slot"), bac = cible && cible.closest(".ph-bac");
      if (slot) place(d.t, +slot.dataset.i); else if (bac) retire(d.t); else rend();
      return;
    }
    // simple touche : on choisit un mot, puis une case ; un mot posé retourne dans le bac
    if (slots.indexOf(d.t) >= 0 && sel === null) { retire(d.t); return; }
    sel = (sel === d.t) ? null : d.t; rend();
  });
  slotsEl.addEventListener("click", function (e) {
    if (sel && !fini) { var s = e.target.closest(".ph-slot"); if (s && !e.target.closest(".ph-badge")) place(sel, +s.dataset.i); }
  });

  function verifie() {
    if (slots.indexOf(null) >= 0 || fini) { msgEl.textContent = D.gagne ? msgEl.textContent : ""; return; }
    var cle = slots.join(",");
    if (cle === envoye) return;
    envoye = cle;
    msgEl.textContent = "On vérifie…";
    fetch("gagne", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mots: slots, demo: !!D.demo }) })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r && r.ok) { fini = true; msgEl.textContent = "🎉 C'est gagné !!"; msgEl.className = "ph-msg gagne"; feu(); }
        else { msgEl.textContent = "Pas encore la bonne phrase… remettez les mots dans l'ordre !"; }
      })
      .catch(function () { msgEl.textContent = "La vérification n'a pas marché, réessaie dans un instant."; envoye = ""; });
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
  rend();
})();
