#!/bin/sh
# Crée le fichier de mot de passe partagé du site.
# Usage : ./make-htpasswd.sh famille > htpasswd
# Le mot de passe est demandé au clavier : il n'apparaît ni dans l'historique ni à l'écran.
set -eu
user="${1:?Usage: $0 <identifiant>}"
printf "Mot de passe : " >&2
stty -echo
read -r pass
stty echo
printf "\n" >&2
printf "%s:%s\n" "$user" "$(printf "%s" "$pass" | openssl passwd -apr1 -stdin)"
