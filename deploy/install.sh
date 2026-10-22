#!/usr/bin/env bash
# Ubuntu/Debian serverga o'rnatish yoki yangilash.
#   Domensiz:  sudo bash install.sh
#   Domen bilan: sudo bash install.sh bilim.texnikum.uz
set -euo pipefail
trap 'echo "XATO: install.sh $LINENO-qatorda to'"'"'xtadi" >&2' ERR

REPO_URL="https://github.com/Jasurbekmurphy/bilim.git"
APP_DIR="/opt/bilim"
BACKUP_DIR="/opt/bilim-backups"
DOMAIN="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Skriptni root sifatida ishga tushiring: sudo bash install.sh" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo ">> Docker o'rnatilmoqda..."
  curl -fsSL https://get.docker.com | sh
fi
command -v git >/dev/null 2>&1 || { apt-get update && apt-get install -y git; }

if [ -d "$APP_DIR/.git" ]; then
  echo ">> Kod yangilanmoqda..."
  git -C "$APP_DIR" pull --ff-only
else
  echo ">> Kod yuklab olinmoqda..."
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  # head avval o'qiydi: aks holda pipefail tufayli tr SIGPIPE bilan yiqilib, skript jim to'xtaydi
  ADMIN_PASSWORD="$(head -c 512 /dev/urandom | LC_ALL=C tr -dc 'a-km-np-z2-9' | cut -c1-12)"
  {
    echo "ADMIN_PASSWORD=$ADMIN_PASSWORD"
    echo "SITE_ADDRESS=${DOMAIN:-:80}"
  } > .env
  chmod 600 .env
  FIRST_INSTALL=1
elif [ -n "$DOMAIN" ]; then
  sed -i "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$DOMAIN|" .env
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep "Status: active" >/dev/null; then
  ufw allow 80/tcp && ufw allow 443/tcp
fi

echo ">> Ishga tushirilmoqda (birinchi marta 5-10 daqiqa olishi mumkin)..."
docker compose up -d --build

mkdir -p "$BACKUP_DIR"
cat > /etc/cron.daily/bilim-backup <<CRON
#!/bin/sh
# Har kuni baza va fayllarning zaxira nusxasi; oxirgi 14 kun saqlanadi
tar -czf "$BACKUP_DIR/bilim-\$(date +%F).tar.gz" -C "$APP_DIR" data
find "$BACKUP_DIR" -name 'bilim-*.tar.gz' -mtime +14 -delete
CRON
chmod +x /etc/cron.daily/bilim-backup

ADDR="$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)"
if [ "$ADDR" = ":80" ]; then
  URL="http://$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
else
  URL="https://$ADDR"
fi
echo
echo "Tayyor! Sayt: $URL"
if [ "${FIRST_INSTALL:-}" = 1 ]; then
  echo "Admin login: admin   parol: $ADMIN_PASSWORD   (birinchi kirishda o'zgartiriladi)"
  echo "Bu parolni yozib oling, u $APP_DIR/.env faylida ham saqlanadi."
fi
echo "Zaxira nusxalar har kuni $BACKUP_DIR papkasiga yoziladi."
