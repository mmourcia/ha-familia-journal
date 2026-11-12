#!/bin/sh
# Une édition : collecte -> passe « classer » -> passe « raconter » -> validation -> construction -> notification.
# Chaque étape peut échouer sans empêcher les suivantes : le site se reconstruit toujours.
#
# Claude ne voit que le NOUVEAU (prepare.py) et n'écrit qu'un patch (patch.json) que merge.py fusionne.
#
# Variables : DATA_DIR (défaut data/real), OUT_DIR (défaut out), SKIP_CLAUDE=1 pour ne pas appeler Claude,
#             CLAUDE_MODEL_CLASSER (défaut haiku), CLAUDE_MODEL (rédaction, défaut sonnet),
#             ARTICLE_MIN (éléments nouveaux minimum pour lancer la rédaction, défaut 4),
#             NOTIFY_SERVICE, SITE_URL.
set -u
cd "$(dirname "$0")"
D="${DATA_DIR:-data/real}"
OUT="${OUT_DIR:-out}"
mkdir -p "$D/media"
[ -f "$D/items.json" ] || echo "[]" > "$D/items.json"
[ -f "$D/enrich.json" ] || echo "{}" > "$D/enrich.json"
ABS="$(cd "$D" && pwd)"
STATUS="ok"

echo "== collecte"
python3 collect.py || STATUS="collecte en échec"

cp "$ABS/enrich.json" "$ABS/enrich.prev.json"
cp "$ABS/items.json" "$ABS/items.bak"
: > "$ABS/last-run.log"

# passe <classer|raconter> <modèle> <tours max> <éléments nouveaux minimum>
passe() {
  mode="$1"; model="$2"; turns="$3"; min="$4"
  W="$(mktemp -d)"
  if ! python3 prepare.py "$mode" "$ABS" "$W" > "$W/prep.json"; then
    STATUS="préparation de la passe $mode en échec"; rm -rf "$W"; return
  fi
  n="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['n'])" "$W/prep.json")"
  r="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('riches', 0))" "$W/prep.json")"
  if [ "$n" -lt "$min" ] && [ "$r" -lt 1 ]; then
    echo "$mode : $n élément(s) à traiter (minimum $min, aucun élément riche), passe ignorée"
    rm -rf "$W"; return
  fi
  echo "$mode : $n élément(s) à traiter, modèle $model"
  # Dossier de travail temporaire : « data/real » y pointe vers les données. Claude ne peut que lire et écrire des
  # fichiers (ni commandes, ni réseau) et n'écrit que patch.json.
  mkdir -p "$W/data"
  ln -s "$ABS" "$W/data/real"
  cp CLAUDE.md "$W/"
  cp "$ABS/categories.json" "$W/"
  [ -f "$ABS/famille.md" ] && cp "$ABS/famille.md" "$W/"
  cp "prompts/$mode.md" "$W/prompt.md"
  cp "$ABS/enrich.json" "$W/enrich.before"
  ( cd "$W" && timeout 900 claude -p "$(cat prompt.md)" \
      --model "$model" \
      --allowedTools "Read" "Write" \
      --add-dir "$ABS" \
      --permission-mode acceptEdits --max-turns "$turns" \
      --output-format stream-json --verbose \
      > "$ABS/last-run-$mode.jsonl" 2> "$ABS/last-run-$mode.err" ) || STATUS="passe $mode en échec"
  {
    echo "=== passe $mode ($model)"
    python3 summarize_run.py "$ABS/last-run-$mode.jsonl"
    [ -s "$ABS/last-run-$mode.err" ] && cat "$ABS/last-run-$mode.err"
  } >> "$ABS/last-run.log" 2>&1
  # Claude ne doit modifier ni enrich.json ni items.json : seul le programme de fusion le fait
  cmp -s "$ABS/enrich.json" "$W/enrich.before" || { cp "$W/enrich.before" "$ABS/enrich.json"; STATUS="enrich.json modifié par Claude, restauré"; }
  cmp -s "$ABS/items.json" "$ABS/items.bak" || { cp "$ABS/items.bak" "$ABS/items.json"; STATUS="items.json modifié par Claude, restauré"; }
  if [ -f "$W/patch.json" ]; then
    if python3 merge.py "$mode" "$ABS" "$W/patch.json" "$W/todo.json" && python3 validate.py "$ABS"; then
      :
    else
      cp "$W/enrich.before" "$ABS/enrich.json"
      STATUS="passe $mode : résultat invalide, ignoré"
    fi
  else
    echo "$mode : aucun patch.json écrit"
    STATUS="passe $mode sans résultat"
  fi
  rm -rf "$W"
}

echo "== rédaction"
if [ "${SKIP_CLAUDE:-0}" != "1" ] && { [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || [ "${FORCE_CLAUDE:-0}" = "1" ]; }; then
  passe classer "${CLAUDE_MODEL_CLASSER:-haiku}" 40 1
  # n = groupes (rubrique, jour) à écrire + éléments isolés pouvant recevoir une brève ; le seuil d'un article
  # (ARTICLE_SEUIL, défaut 3 éléments) est appliqué par prepare.py
  passe raconter "${CLAUDE_MODEL:-sonnet}" 40 "${ARTICLE_MIN:-1}"
else
  echo "Claude non appelé (jeton absent ou SKIP_CLAUDE=1)"
  STATUS="sans rédaction"
fi

# Dans le dossier de données, rien d'autre que les fichiers prévus
for f in "$ABS"/* "$ABS"/.[!.]*; do
  [ -e "$f" ] || continue
  case "$(basename "$f")" in
    items.json|items.bak|state.json|reactions.jsonl|categories.json|famille.md|enrich.json|enrich.prev.json|build-report.json|media|last-run*) ;;
    *) rm -rf "$f" ;;
  esac
done
if ! python3 validate.py "$ABS"; then
  cp "$ABS/enrich.prev.json" "$ABS/enrich.json"
  STATUS="enrich.json invalide, version précédente conservée"
fi

echo "== site"
rm -rf "$OUT.new"
if python3 build.py --data "$ABS" --out "$OUT.new"; then
  rm -rf "$OUT.old"
  [ -d "$OUT" ] && mv "$OUT" "$OUT.old"
  mv "$OUT.new" "$OUT"
else
  STATUS="construction du site en échec"
fi

echo "== notification"
if [ -n "${NOTIFY_SERVICE:-}" ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  python3 - "$ABS" "$STATUS" <<'PY'
import json, os, sys, urllib.request
d, status = sys.argv[1], sys.argv[2]
try:
    r = json.load(open(f"{d}/build-report.json"))
except Exception:
    r = {}
msg = f"{r.get('visibles', 0)} éléments en ligne, {r.get('articles', 0)} articles"
if r.get("en_attente_de_revue"):
    msg += f", {len(r['en_attente_de_revue'])} en attente"
if r.get("mis_de_cote"):
    msg += f", {len(r['mis_de_cote'])} mis de côté"
if status != "ok":
    msg += f" · ⚠ {status}"
if os.environ.get("SITE_URL"):
    msg += f"\n{os.environ['SITE_URL']}"
body = json.dumps({"title": "Journal : édition prête" if status == "ok" else "Journal : à vérifier", "message": msg}).encode()
req = urllib.request.Request(
    f"http://supervisor/core/api/services/notify/{os.environ['NOTIFY_SERVICE']}", data=body,
    headers={"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}", "Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=20)
except Exception as exc:
    print("notification impossible :", exc)
PY
fi
echo "== fin : $STATUS"
