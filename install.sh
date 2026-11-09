#!/usr/bin/env bash
# pve-dash installer - run as root ON your Proxmox host.
#
#   Install:    bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffrey-lucero/pve-dash/main/install.sh)"
#   Uninstall:  bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffrey-lucero/pve-dash/main/install.sh)" -- --uninstall
#
# Optional env vars: PVEDASH_PORT (default 8088), PVEDASH_BIND (default 0.0.0.0)
set -euo pipefail

REPO_RAW="${PVEDASH_REPO_RAW:-https://raw.githubusercontent.com/jeffrey-lucero/pve-dash/main}"
INSTALL_DIR="/opt/pve-dash"
CONF_DIR="/etc/pve-dash"
SERVICE="pve-dash"
PORT="${PVEDASH_PORT:-8088}"
BIND="${PVEDASH_BIND:-0.0.0.0}"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (on the Proxmox host)."

if [ "${1:-}" = "--uninstall" ] || [ "${2:-}" = "--uninstall" ]; then
  say "Removing ${SERVICE}"
  systemctl disable --now "${SERVICE}" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
  rm -rf "${INSTALL_DIR}"
  say "Removed. Config kept in ${CONF_DIR} (delete it manually if you want)."
  exit 0
fi

command -v pvesh >/dev/null 2>&1 || die "pvesh not found - this must run on a Proxmox VE host, not inside a container."
command -v python3 >/dev/null 2>&1 || { say "Installing python3"; apt-get update -qq && apt-get install -y -qq python3; }

command -v smartctl >/dev/null 2>&1 || { say "Installing smartmontools (for drive temperatures)"; apt-get update -qq && apt-get install -y -qq smartmontools; }

mkdir -p "${INSTALL_DIR}" "${CONF_DIR}"

say "Downloading pve-dash.py"
curl -fsSL "${REPO_RAW}/pve-dash.py" -o "${INSTALL_DIR}/pve-dash.py"
chmod 755 "${INSTALL_DIR}/pve-dash.py"
python3 -m py_compile "${INSTALL_DIR}/pve-dash.py" || die "Downloaded file failed Python syntax check."

if [ ! -f "${CONF_DIR}/config.json" ]; then
  say "Creating default config at ${CONF_DIR}/config.json"
  cat > "${CONF_DIR}/config.json" <<'EOF'
{
  "title": "Homelab",
  "refresh_seconds": 2,
  "hide": [],
  "default_ports": {},
  "overrides": {}
}
EOF
fi

# Optional: PVEDASH_REFRESH=<seconds> sets the live-refresh speed (also on re-install)
if [ -n "${PVEDASH_REFRESH:-}" ]; then
  say "Setting refresh to ${PVEDASH_REFRESH}s"
  python3 - "${CONF_DIR}/config.json" "${PVEDASH_REFRESH}" <<'PY'
import json, sys
path, val = sys.argv[1], float(sys.argv[2])
with open(path) as f:
    cfg = json.load(f)
cfg["refresh_seconds"] = val
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
PY
fi

say "Creating systemd service"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=pve-dash Proxmox homelab dashboard
After=network-online.target pve-cluster.service
Wants=network-online.target

[Service]
Type=simple
Environment=PVEDASH_PORT=${PORT}
Environment=PVEDASH_BIND=${BIND}
Environment=PVEDASH_CONFIG=${CONF_DIR}/config.json
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/pve-dash.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE}"
sleep 1

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done! Open:  http://${IP:-<proxmox-ip>}:${PORT}"
echo "   Config:  ${CONF_DIR}/config.json   (restart: systemctl restart ${SERVICE})"
echo "   Tip:     add tags like 'port-8080' or 'https' to a container in Proxmox to set its click-through link."
