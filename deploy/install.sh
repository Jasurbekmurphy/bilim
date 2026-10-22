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

# .env dagi kalitni qo'shadi yoki yangilaydi
set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

if [ ! -f .env ]; then
  # head avval o'qiydi: aks holda pipefail tufayli tr SIGPIPE bilan yiqilib, skript jim to'xtaydi
  ADMIN_PASSWORD="$(head -c 512 /dev/urandom | LC_ALL=C tr -dc 'a-km-np-z2-9' | cut -c1-12)"
  echo "ADMIN_PASSWORD=$ADMIN_PASSWORD" > .env
  chmod 600 .env
fi
if [ -n "$DOMAIN" ]; then set_env SITE_ADDRESS "$DOMAIN"; fi
grep -q '^SITE_ADDRESS=' .env || set_env SITE_ADDRESS ":80"
grep -q '^WEB_PORT=' .env || set_env WEB_PORT 8100
ADDR="$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)"
WEB_PORT="$(grep '^WEB_PORT=' .env | cut -d= -f2)"

# Serverda boshqa saytlarga xizmat qilayotgan nginx bo'lsa, unga tegmaymiz va platformani u orqali ulaymiz.
# Aks holda 80/443 portlarni o'z Caddy konteynerimiz egallaydi.
if command -v nginx >/dev/null 2>&1 && ss -ltnp | grep -E ':80[[:space:]]' | grep nginx >/dev/null; then
  MODE=nginx
  set_env COMPOSE_PROFILES ""
else
  MODE=caddy
  set_env COMPOSE_PROFILES caddy
  if command -v ufw >/dev/null 2>&1 && ufw status | grep "Status: active" >/dev/null; then
    ufw allow 80/tcp && ufw allow 443/tcp
  fi
fi

echo ">> Ishga tushirilmoqda (birinchi marta 5-10 daqiqa olishi mumkin)..."
if [ "$MODE" = nginx ]; then
  docker compose --profile caddy rm -sf caddy >/dev/null 2>&1 || true
fi
docker compose up -d --build

if [ "$MODE" = nginx ] && [ "$ADDR" != ":80" ]; then
  echo ">> nginx sozlanmoqda ($ADDR -> 127.0.0.1:$WEB_PORT)..."
  if [ -d /etc/nginx/sites-available ]; then
    CONF=/etc/nginx/sites-available/bilim.conf
    ln -sf "$CONF" /etc/nginx/sites-enabled/bilim.conf
  else
    CONF=/etc/nginx/conf.d/bilim.conf
  fi
  # certbot qo'shgan HTTPS qismini qayta yozib yubormaslik uchun fayl faqat bir marta yaratiladi
  if [ ! -f "$CONF" ] || ! grep -q "server_name $ADDR;" "$CONF"; then
    cat > "$CONF" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $ADDR;
    client_max_body_size 120M;

    location / {
        proxy_pass http://127.0.0.1:$WEB_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
NGINX
  fi
  if ! nginx -t; then
    echo "nginx sozlamasida xato, bilim.conf olib tashlandi; boshqa saytlarga ta'sir qilmadi." >&2
    rm -f "$CONF" /etc/nginx/sites-enabled/bilim.conf
    exit 1
  fi
  systemctl reload nginx

  if ! command -v certbot >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq certbot python3-certbot-nginx
  fi
  if ! certbot --nginx -d "$ADDR" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
    echo "!! HTTPS sertifikati olinmadi. Domen DNS'i server IP'siga yo'naltirilganini tekshirib, skriptni qayta ishga tushiring."
  fi
fi

mkdir -p "$BACKUP_DIR"
cat > /etc/cron.daily/bilim-backup <<CRON
#!/bin/sh
# Har kuni baza va fayllarning zaxira nusxasi; oxirgi 14 kun saqlanadi
tar -czf "$BACKUP_DIR/bilim-\$(date +%F).tar.gz" -C "$APP_DIR" data
find "$BACKUP_DIR" -name 'bilim-*.tar.gz' -mtime +14 -delete
CRON
chmod +x /etc/cron.daily/bilim-backup

echo
if [ "$MODE" = nginx ] && [ "$ADDR" = ":80" ]; then
  echo "Platforma ishga tushdi (127.0.0.1:$WEB_PORT), lekin 80-port boshqa saytlaringiz nginx'ida."
  echo "Uni ochish uchun domen yoki subdomen kerak. DNS'da A yozuvini shu server IP'siga yo'naltirib, so'ng:"
  echo "  bash $APP_DIR/deploy/install.sh bilim.sizningdomen.uz"
elif [ "$ADDR" = ":80" ]; then
  echo "Tayyor! Sayt: http://$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
else
  echo "Tayyor! Sayt: https://$ADDR"
fi
echo "Admin login: admin   boshlang'ich parol: $(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2)"
echo "(Birinchi kirishda parol o'zgartiriladi; keyin bu parol endi ishlamaydi.)"
echo "Zaxira nusxalar har kuni $BACKUP_DIR papkasiga yoziladi."
