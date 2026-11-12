#!/bin/sh
# Point d'entrée de l'app : lit les options, prépare le dossier de données, lance le serveur web puis le planificateur.
set -eu

# Les options deviennent des variables d'environnement (jamais écrites dans les journaux)
eval "$(python3 - <<'PY'
import json, shlex
o = json.load(open("/data/options.json"))
m = {
    "CLAUDE_CODE_OAUTH_TOKEN": "claude_token", "CLAUDE_MODEL": "claude_model", "CLAUDE_MODEL_CLASSER": "claude_model_classer", "WAHA_URL": "waha_url",
    "WAHA_KEY": "waha_key", "GROUP_ID": "group_id", "OWNER_NAME": "owner_name", "COLLECT_SINCE": "since",
    "NOTIFY_SERVICE": "notify_service", "SITE_URL": "site_url", "ARTICLE_SEUIL": "article_seuil",
    "UNE_SCOPE": "une_scope",
}
for env, key in m.items():
    print(f"export {env}={shlex.quote(str(o.get(key) or ''))}")
print(f"export SITE_USER={shlex.quote(str(o.get('site_user') or 'famille'))}")
print(f"export SITE_PASSWORD={shlex.quote(str(o.get('site_password') or ''))}")
PY
)"

# Le groupe peut être donné par son nom (« Les Bouvet 2.0 ») : on retrouve son identifiant avec la liste de WAHA
if ! GROUP_ID="$(python3 /app/resolve_group.py "$GROUP_ID")"; then
  echo "ERREUR : groupe introuvable (voir ci-dessus). Corrige group_id dans les options de l'app." >&2
  exit 1
fi
export GROUP_ID

BASE=/share/familia-journal
export DATA_DIR="$BASE/real" OUT_DIR=/data/out HOME=/data/home
mkdir -p "$DATA_DIR/media" "$HOME" /data/out "$BASE"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-}"

# Rubriques modifiables à la main depuis le partage /share
[ -f "$DATA_DIR/categories.json" ] || cp /app/data/real/categories.json "$DATA_DIR/categories.json"
[ -f "$DATA_DIR/famille.md" ] || cp /app/data/real/famille.md "$DATA_DIR/famille.md"
# Claude travaille depuis /app et lit « data/real » : on le fait pointer vers le dossier persistant
rm -rf /app/data/real
ln -s "$DATA_DIR" /app/data/real

# Accès protégé par mot de passe
if [ -z "$SITE_PASSWORD" ]; then
  echo "ERREUR : site_password est vide. Renseigne-le dans les options de l'app." >&2
  exit 1
fi
printf "%s:%s\n" "$SITE_USER" "$(printf "%s" "$SITE_PASSWORD" | openssl passwd -apr1 -stdin)" > /data/htpasswd
chmod 644 /data/htpasswd
unset SITE_PASSWORD

# Site visible dès le démarrage, même avant la première édition
[ -f /data/out/index.html ] || python3 /app/build.py --data "$DATA_DIR" --out /data/out || true

cp /app/deploy/nginx.conf /etc/nginx/conf.d/familia.conf
nginx -g "daemon on;"

exec python3 /scheduler.py
