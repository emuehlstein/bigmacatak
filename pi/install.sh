#!/usr/bin/env bash
# BigMacATAK Pi — Upstream-only OTS installer for Raspberry Pi / Debian arm64
# Installs vanilla OpenTAK Server with no MeshCore, Meshtastic, ADS-B, or MediaMTX.
# Usage: ./install.sh
# Env vars: BIGMAC_USER, BIGMAC_DATA, BIGMAC_VENV
set -euo pipefail

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

BIGMAC_USER="${BIGMAC_USER:-$(whoami)}"
BIGMAC_HOME=$(eval echo "~${BIGMAC_USER}")
BIGMAC_DATA="${BIGMAC_DATA:-${BIGMAC_HOME}/ots}"
BIGMAC_VENV="${BIGMAC_VENV:-${BIGMAC_HOME}/.opentakserver_venv}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Versions
OTS_VERSION="1.7.10"
OTS_UI_VERSION="1.7.4"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[BigMac]${NC} $*"; }
warn() { echo -e "${YELLOW}[BigMac]${NC} $*"; }
err()  { echo -e "${RED}[BigMac]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
#  Preflight
# ---------------------------------------------------------------------------

if [[ "$(uname -m)" != "aarch64" ]]; then
    warn "This script is designed for arm64/aarch64. Detected: $(uname -m)"
    warn "Continuing anyway, but some binaries may not work."
fi

if [[ ! -f /etc/debian_version ]]; then
    err "This script requires Debian/Raspbian. Detected: $(cat /etc/os-release 2>/dev/null | head -1)"
    exit 1
fi

DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
if [[ "${DEBIAN_VERSION}" -lt 12 ]]; then
    err "Debian 12+ required. Detected: $(cat /etc/debian_version)"
    exit 1
fi
if [[ "${DEBIAN_VERSION}" -ge 13 ]]; then
    log "  Debian 13+ detected — gevent may show Python 3.13 assertion warnings (non-fatal)"
fi

log "BigMacATAK Pi Installer (upstream-only)"
log "  User:  ${BIGMAC_USER}"
log "  Data:  ${BIGMAC_DATA}"
log "  Venv:  ${BIGMAC_VENV}"
log ""

# ---------------------------------------------------------------------------
#  Step 1: System Packages
# ---------------------------------------------------------------------------

log "Step 1: Installing system packages..."

sudo apt-get update -qq

PACKAGES=(
    # Core
    postgresql
    rabbitmq-server
    nginx
    libnginx-mod-stream
    # Python
    python3
    python3-venv
    python3-pip
    python3-dev
    # Build tools (for pip packages with C extensions)
    build-essential
    libpq-dev
    libffi-dev
    libssl-dev
    # Utilities
    git
    curl
    jq
    unzip
)

sudo apt-get install -y -qq "${PACKAGES[@]}" 2>/dev/null || {
    warn "Some packages may not be available. Installing what we can..."
    for pkg in "${PACKAGES[@]}"; do
        sudo apt-get install -y -qq "$pkg" 2>/dev/null || warn "  Skipped: $pkg"
    done
}

log "  ✓ System packages installed"

# ---------------------------------------------------------------------------
#  Step 2: PostgreSQL Setup
# ---------------------------------------------------------------------------

log "Step 2: Configuring PostgreSQL..."

sudo systemctl enable --now postgresql

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='ots'" | grep -q 1; then
    sudo -u postgres createuser ots
    log "  ✓ Created PostgreSQL role: ots"
else
    log "  ✓ PostgreSQL role 'ots' already exists"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='ots'" | grep -q 1; then
    sudo -u postgres createdb -O ots ots
    log "  ✓ Created PostgreSQL database: ots"
else
    log "  ✓ PostgreSQL database 'ots' already exists"
fi

PG_HBA=$(sudo -u postgres psql -tAc "SHOW hba_file")

if ! sudo grep -q "^local.*ots.*ots.*peer" "$PG_HBA" 2>/dev/null; then
    sudo sed -i '/^local.*all.*all.*peer/i local   ots             ots                                     peer' "$PG_HBA"
    log "  ✓ Added local peer auth for ots"
fi

if ! sudo grep -q "^host.*ots.*ots.*127.0.0.1.*trust" "$PG_HBA" 2>/dev/null; then
    if sudo grep -q "^host.*all.*all.*127.0.0.1.*scram-sha-256" "$PG_HBA"; then
        sudo sed -i '/^host.*all.*all.*127.0.0.1.*scram-sha-256/i host    ots             ots             127.0.0.1/32            trust' "$PG_HBA"
    else
        echo "host    ots             ots             127.0.0.1/32            trust" | sudo tee -a "$PG_HBA" > /dev/null
    fi
    log "  ✓ Added TCP trust auth for ots@127.0.0.1"
fi

sudo systemctl reload postgresql
log "  ✓ PostgreSQL configured"

# ---------------------------------------------------------------------------
#  Step 3: RabbitMQ Setup
# ---------------------------------------------------------------------------

log "Step 3: Configuring RabbitMQ..."

sudo systemctl enable --now rabbitmq-server

if ! sudo rabbitmq-plugins list -e | grep -q rabbitmq_mqtt; then
    sudo rabbitmq-plugins enable rabbitmq_mqtt
    log "  ✓ Enabled RabbitMQ MQTT plugin"
else
    log "  ✓ RabbitMQ MQTT plugin already enabled"
fi

# ---------------------------------------------------------------------------
#  Step 4: Python Virtualenv + OTS
# ---------------------------------------------------------------------------

log "Step 4: Setting up Python virtualenv and OpenTAK Server..."

if [[ ! -d "${BIGMAC_VENV}" ]]; then
    python3 -m venv "${BIGMAC_VENV}"
    log "  ✓ Created virtualenv at ${BIGMAC_VENV}"
else
    log "  ✓ Virtualenv already exists at ${BIGMAC_VENV}"
fi

"${BIGMAC_VENV}/bin/pip" install --upgrade pip setuptools wheel -q

INSTALLED_OTS=$("${BIGMAC_VENV}/bin/pip" show OpenTAKServer 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "")

if [[ "${INSTALLED_OTS}" != "${OTS_VERSION}" ]]; then
    log "  Installing OpenTAKServer ${OTS_VERSION}..."
    "${BIGMAC_VENV}/bin/pip" install "OpenTAKServer==${OTS_VERSION}" -q
    log "  ✓ Installed OpenTAKServer ${OTS_VERSION}"
else
    log "  ✓ OpenTAKServer ${OTS_VERSION} already installed"
fi

# ---------------------------------------------------------------------------
#  Step 5: OTS Data Directory
# ---------------------------------------------------------------------------

log "Step 5: Setting up OTS data directory..."

mkdir -p "${BIGMAC_DATA}"/{uploads,logs,ca,tmp}

if [[ ! -f "${BIGMAC_DATA}/config.yml" ]]; then
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        -e "s|__BIGMAC_USER__|${BIGMAC_USER}|g" \
        "${SCRIPT_DIR}/config/ots-config.yml" > "${BIGMAC_DATA}/config.yml"
    log "  ✓ Created OTS config at ${BIGMAC_DATA}/config.yml"
else
    log "  ✓ OTS config already exists"
fi

# ---------------------------------------------------------------------------
#  Step 6: OpenTAKServer Web UI
# ---------------------------------------------------------------------------

log "Step 6: Installing OpenTAKServer Web UI..."

OTS_UI_DIR="/var/www/opentakserver"
sudo mkdir -p "${OTS_UI_DIR}"

if [[ ! -f "${OTS_UI_DIR}/index.html" ]]; then
    log "  Downloading OpenTAKServer-UI v${OTS_UI_VERSION}..."
    OTS_UI_URL="https://github.com/brian7704/OpenTAKServer-UI/releases/download/v${OTS_UI_VERSION}/OpenTAKServer-UI-v${OTS_UI_VERSION}.zip"
    TMP_UI=$(mktemp -d)
    curl -sL -o "${TMP_UI}/ots-ui.zip" "${OTS_UI_URL}"
    sudo unzip -o -q "${TMP_UI}/ots-ui.zip" -d /var/www/
    rm -rf "${TMP_UI}"
    sudo chown -R www-data:www-data "${OTS_UI_DIR}"
    log "  ✓ OpenTAKServer-UI v${OTS_UI_VERSION} installed"
else
    log "  ✓ OpenTAKServer-UI already installed"
fi

# ---------------------------------------------------------------------------
#  Step 7: nginx Configuration
# ---------------------------------------------------------------------------

log "Step 7: Configuring nginx..."

NGINX_CONF_DIR="/etc/nginx"
NGINX_SITES_AVAILABLE="${NGINX_CONF_DIR}/sites-available"
NGINX_SITES_ENABLED="${NGINX_CONF_DIR}/sites-enabled"
NGINX_STREAMS="${NGINX_CONF_DIR}/streams"

sudo mkdir -p "${NGINX_SITES_AVAILABLE}" "${NGINX_SITES_ENABLED}" "${NGINX_STREAMS}"

if ! sudo grep -q "include.*streams" "${NGINX_CONF_DIR}/nginx.conf" 2>/dev/null; then
    if ! sudo grep -q "^stream" "${NGINX_CONF_DIR}/nginx.conf"; then
        echo -e "\nstream {\n    include ${NGINX_STREAMS}/*;\n}" | sudo tee -a "${NGINX_CONF_DIR}/nginx.conf" > /dev/null
        log "  ✓ Added stream block to nginx.conf"
    fi
fi

if [[ ! -f "${NGINX_CONF_DIR}/proxy_params" ]]; then
    sudo tee "${NGINX_CONF_DIR}/proxy_params" > /dev/null <<'PROXYEOF'
proxy_set_header Host $http_host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
PROXYEOF
    log "  ✓ Created proxy_params"
fi

for tmpl in "${SCRIPT_DIR}"/config/nginx/servers/*; do
    name=$(basename "$tmpl")
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        -e "s|__BIGMAC_USER__|${BIGMAC_USER}|g" \
        "$tmpl" | sudo tee "${NGINX_SITES_AVAILABLE}/${name}" > /dev/null
    sudo ln -sf "${NGINX_SITES_AVAILABLE}/${name}" "${NGINX_SITES_ENABLED}/${name}"
done

for tmpl in "${SCRIPT_DIR}"/config/nginx/streams/*; do
    name=$(basename "$tmpl")
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        "$tmpl" | sudo tee "${NGINX_STREAMS}/${name}" > /dev/null
done

sudo rm -f "${NGINX_SITES_ENABLED}/default"

if sudo nginx -t 2>/dev/null; then
    sudo systemctl enable --now nginx
    sudo systemctl reload nginx
    log "  ✓ nginx configured and running"
else
    warn "  ⚠ nginx config test failed — check configs manually"
    sudo nginx -t 2>&1 || true
fi

# ---------------------------------------------------------------------------
#  Step 8: systemd Services
# ---------------------------------------------------------------------------

log "Step 8: Installing systemd services..."

for unit in "${SCRIPT_DIR}"/systemd/*.service; do
    name=$(basename "$unit")
    sed \
        -e "s|__BIGMAC_USER__|${BIGMAC_USER}|g" \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        -e "s|__BIGMAC_VENV__|${BIGMAC_VENV}|g" \
        -e "s|__BIGMAC_HOME__|${BIGMAC_HOME}|g" \
        "$unit" | sudo tee "/etc/systemd/system/${name}" > /dev/null
done

sudo systemctl daemon-reload

CORE_SERVICES=(opentakserver eud-handler-tcp eud-handler-ssl cot-parser)
for svc in "${CORE_SERVICES[@]}"; do
    sudo systemctl enable "${svc}.service"
    log "  ✓ Enabled ${svc}"
done

# ---------------------------------------------------------------------------
#  Step 9: Patch OTS for large file support (>2 GB data packages)
# ---------------------------------------------------------------------------

log "Step 9: Patching OTS for large file support..."

DP_MODEL="${BIGMAC_VENV}/lib/python*/site-packages/opentakserver/models/DataPackage.py"
DP_MODEL=$(ls ${DP_MODEL} 2>/dev/null | head -1)

if [[ -n "${DP_MODEL}" ]]; then
    if grep -q 'mapped_column(Integer)' "${DP_MODEL}" 2>/dev/null; then
        if ! grep -q 'BigInteger' "${DP_MODEL}"; then
            sed -i 's/from sqlalchemy import/from sqlalchemy import BigInteger,/' "${DP_MODEL}"
        fi
        sed -i '/size.*mapped_column/s/mapped_column(Integer)/mapped_column(BigInteger)/' "${DP_MODEL}"
        log "  ✓ Patched DataPackage model (size: Integer → BigInteger)"
    else
        log "  ✓ DataPackage model already patched or uses BigInteger"
    fi
else
    warn "  ⚠ Could not find DataPackage.py — skip BigInteger patch"
fi

"${BIGMAC_VENV}/bin/pip" install psycopg2-binary -q 2>/dev/null || true

# ---------------------------------------------------------------------------
#  Step 10: Start Core Services
# ---------------------------------------------------------------------------

log "Step 10: Starting core services..."

sudo systemctl start opentakserver || {
    warn "  ⚠ OTS failed to start — check: journalctl -u opentakserver"
}

log "  Waiting for OTS to initialize..."
sleep 10

for svc in eud-handler-tcp eud-handler-ssl cot-parser; do
    sudo systemctl start "${svc}" || warn "  ⚠ ${svc} failed to start"
done

log "  ✓ Core services started"

# Migrate DB column after OTS creates tables (first run)
if [[ -n "${DP_MODEL}" ]]; then
    log "  Migrating data_packages.size to bigint..."
    sudo -u postgres psql -d ots -c 'ALTER TABLE data_packages ALTER COLUMN size TYPE bigint;' 2>/dev/null && \
        log "  ✓ data_packages.size migrated to bigint" || \
        log "  ✓ data_packages.size already bigint or table not yet created"
fi

# ---------------------------------------------------------------------------
#  Summary
# ---------------------------------------------------------------------------

PI_IP=$(hostname -I | awk '{print $1}')

echo ""
log "════════════════════════════════════════════════════════════"
log "  BigMacATAK Pi installation complete! (upstream-only)"
log "════════════════════════════════════════════════════════════"
log ""
log "  Web UI:       http://${PI_IP}:8080"
log "  HTTPS:        https://${PI_IP}:443"
log "  Marti API:    https://${PI_IP}:8443"
log "  Cert Enroll:  https://${PI_IP}:8446"
log ""
log "  TAK TCP:      ${PI_IP}:8088"
log "  TAK SSL:      ${PI_IP}:8089"
log ""
log "  Default login: administrator / password"
log "  (Change this immediately!)"
log ""
log "  OTS Data:     ${BIGMAC_DATA}"
log "  OTS Config:   ${BIGMAC_DATA}/config.yml"
log "  CA Certs:     ${BIGMAC_DATA}/ca/"
log "  Venv:         ${BIGMAC_VENV}"
log ""
log "  Services:"
log "    systemctl status opentakserver"
log "    systemctl status eud-handler-tcp"
log "    systemctl status eud-handler-ssl"
log "    systemctl status cot-parser"
log ""
log "  Logs: journalctl -u <service-name> -f"
log ""
