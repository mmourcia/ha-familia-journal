(function(){
var cp=function(t){if(navigator.clipboard&&navigator.clipboard.writeText){return navigator.clipboard.writeText(t)}
return new Promise(function(ok,ko){var a=document.createElement("textarea");a.value=t;a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);a.select();
try{document.execCommand("copy")?ok():ko()}catch(x){ko(x)}document.body.removeChild(a)})};
document.querySelectorAll(".vx").forEach(function(c){
var t=c.getAttribute("data-envoi")||c.querySelector(".vx-b").innerText.trim();
var b=c.querySelector(".vx-copy"),sh=c.querySelector(".vx-share");
b.onclick=function(){cp(t).then(function(){b.textContent="✅ Copié !";b.classList.add("ok")},function(){b.textContent="Sélectionne et copie à la main"});
setTimeout(function(){b.textContent="📋 Copier";b.classList.remove("ok")},2200)};
if(navigator.share){sh.hidden=false;sh.onclick=function(){navigator.share({text:t}).catch(function(){})}}})})();
