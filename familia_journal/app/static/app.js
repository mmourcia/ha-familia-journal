(function () {
  var root = document.documentElement, btn = document.getElementById("theme");
  try { var saved = localStorage.getItem("familia-theme"); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  if (btn) btn.addEventListener("click", function () {
    var cur = root.getAttribute("data-theme");
    var dark = cur === "dark" || (!cur && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("familia-theme", next); } catch (e) {}
  });

  var pb = document.getElementById("plan-btn"), pn = document.getElementById("plan");
  if (pb && pn) pb.addEventListener("click", function () {
    var o = pn.classList.toggle("open");
    pb.setAttribute("aria-expanded", o ? "true" : "false");
  });

  var dds = document.querySelectorAll(".plan-dd");
  function fermer(sauf) { dds.forEach(function (d) { if (d !== sauf) { d.classList.remove("open"); d.querySelector(".plan-dd-b").setAttribute("aria-expanded", "false"); } }); }
  dds.forEach(function (d) {
    var b = d.querySelector(".plan-dd-b");
    b.addEventListener("click", function () {
      var o = !d.classList.contains("open");
      fermer(d); d.classList.toggle("open", o); b.setAttribute("aria-expanded", o ? "true" : "false");
    });
  });
  document.addEventListener("click", function (ev) { if (!ev.target.closest(".plan-dd")) fermer(null); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { var ouvert = document.querySelector(".plan-dd.open .plan-dd-b"); fermer(null); if (ouvert) ouvert.focus(); }
  });

  var lb = document.getElementById("lb");
  if (!lb) return;
  var big = lb.querySelector("img");
  function close() { lb.hidden = true; big.removeAttribute("src"); }
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a[data-full]");
    if (a) { ev.preventDefault(); big.src = a.getAttribute("href"); lb.hidden = false; return; }
    if (ev.target === lb || ev.target.closest("#lb button")) close();
  });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && !lb.hidden) close(); });
})();
