#!/usr/bin/env bash
set -euo pipefail
cd /opt/repository-steward
if ! id steward >/dev/null 2>&1; then useradd --system --home /opt/repository-steward --shell /usr/sbin/nologin steward; fi
if [ -e /etc/nginx/sites-available/repository-steward ]; then
  echo "Project Nginx site exists; refusing to overwrite an active deployment." >&2
  exit 1
fi
mkdir -p /var/www/steward-acme
cat > /etc/nginx/sites-available/repository-steward <<'NGINX'
server {
    listen 80;
    server_name 101.132.153.33;
    location ^~ /.well-known/acme-challenge/ { root /var/www/steward-acme; }
    location / { return 404; }
}
NGINX
ln -sfn /etc/nginx/sites-available/repository-steward /etc/nginx/sites-enabled/repository-steward
nginx -t
systemctl reload nginx
if [ ! -f .env ]; then python3 scripts/init_deployment.py --domain 101.132.153.33; fi
python3 - <<'PY'
from pathlib import Path
p=Path('.env')
s=p.read_text().replace('DATA_DIR=/data','DATA_DIR=/opt/repository-steward/data').replace('MIHOMO_CONFIG_PATH=/data/proxy/config.yaml','MIHOMO_CONFIG_PATH=/opt/repository-steward/data/proxy/config.yaml')
s=s.replace('MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090','MIHOMO_CONTROLLER_URL=')
p.write_text(s)
PY
chown root:steward .env
chmod 640 .env
chown -R steward:steward data
chmod 700 data data/proxy
