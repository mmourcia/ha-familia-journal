FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Paris LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 nginx ffmpeg openssl curl ca-certificates tzdata nodejs npm \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf

# Tailscale : un appareil dédié au journal, dont Funnel n'expose QUE le site (jamais Home Assistant)
RUN curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
 && apt-get update && apt-get install -y --no-install-recommends tailscale \
 && rm -rf /var/lib/apt/lists/*

# Claude Code (se connecte avec le jeton de l'abonnement, sans clé d'API)
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

COPY app /app
COPY run.sh scheduler.py /
RUN chmod +x /run.sh /app/edition.sh /app/deploy/make-htpasswd.sh

WORKDIR /app
CMD ["/run.sh"]
