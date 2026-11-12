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
