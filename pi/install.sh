#!/usr/bin/env bash
# BigMacATAK Pi — Idempotent installer for Raspberry Pi / Debian arm64
# Usage: ./install.sh
# Env vars: BIGMAC_USER, BIGMAC_DATA, BIGMAC_VENV, BIGMAC_SKIP_ADSB, BIGMAC_SKIP_MEDIAMTX
set -euo pipefail

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

BIGMAC_USER="${BIGMAC_USER:-$(whoami)}"
BIGMAC_HOME=$(eval echo "~${BIGMAC_USER}")
BIGMAC_DATA="${BIGMAC_DATA:-${BIGMAC_HOME}/ots}"
BIGMAC_VENV="${BIGMAC_VENV:-${BIGMAC_HOME}/.opentakserver_venv}"
BIGMAC_SKIP_ADSB="${BIGMAC_SKIP_ADSB:-0}"
BIGMAC_SKIP_MEDIAMTX="${BIGMAC_SKIP_MEDIAMTX:-0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Versions
MEDIAMTX_VERSION="1.17.1"
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

# Check Debian version
DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
if [[ "${DEBIAN_VERSION}" -lt 12 ]]; then
    err "Debian 12+ required. Detected: $(cat /etc/debian_version)"
    exit 1
fi
if [[ "${DEBIAN_VERSION}" -ge 13 ]]; then
    log "  Debian 13+ detected — gevent may show Python 3.13 assertion warnings (non-fatal)"
fi

log "BigMacATAK Pi Installer"
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
    libnginx-mod-stream    # Required for TCP/UDP stream proxying
    # Python
    python3
    python3-venv
    python3-pip
    python3-dev
    # Build tools (for pip packages with C extensions + readsb)
    build-essential
    libpq-dev
    libffi-dev
    libssl-dev
    libzstd-dev            # Required for readsb
    libncurses-dev         # Required for readsb interactive mode
    # RTL-SDR (for readsb/ADS-B)
    librtlsdr-dev
    rtl-sdr
    # Utilities
    git
    curl
    jq
    unzip
    usbutils
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

# Create role and database if they don't exist
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

# Configure pg_hba.conf for OTS access
# IMPORTANT: The ots-specific trust rule must come BEFORE any scram-sha-256 rules
PG_HBA=$(sudo -u postgres psql -tAc "SHOW hba_file")

# Add local peer auth for ots if not present
if ! sudo grep -q "^local.*ots.*ots.*peer" "$PG_HBA" 2>/dev/null; then
    sudo sed -i '/^local.*all.*all.*peer/i local   ots             ots                                     peer' "$PG_HBA"
    log "  ✓ Added local peer auth for ots"
fi

# Add TCP trust auth for ots@127.0.0.1 BEFORE any scram-sha-256 rules
if ! sudo grep -q "^host.*ots.*ots.*127.0.0.1.*trust" "$PG_HBA" 2>/dev/null; then
    # Insert before the first 'host all all' or 'host all all 127.0.0.1' scram-sha-256 line
    if sudo grep -q "^host.*all.*all.*127.0.0.1.*scram-sha-256" "$PG_HBA"; then
        sudo sed -i '/^host.*all.*all.*127.0.0.1.*scram-sha-256/i host    ots             ots             127.0.0.1/32            trust' "$PG_HBA"
    else
        # Just append if no scram-sha-256 rule exists
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

# Enable MQTT plugin
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

# Upgrade pip
"${BIGMAC_VENV}/bin/pip" install --upgrade pip setuptools wheel -q

# Install OTS and bridge dependencies
INSTALLED_OTS=$("${BIGMAC_VENV}/bin/pip" show OpenTAKServer 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "")

if [[ "${INSTALLED_OTS}" != "${OTS_VERSION}" ]]; then
    log "  Installing OpenTAKServer ${OTS_VERSION}..."
    "${BIGMAC_VENV}/bin/pip" install "OpenTAKServer==${OTS_VERSION}" -q
    log "  ✓ Installed OpenTAKServer ${OTS_VERSION}"
else
    log "  ✓ OpenTAKServer ${OTS_VERSION} already installed"
fi

# Bridge dependencies
"${BIGMAC_VENV}/bin/pip" install paho-mqtt pika meshtastic PyYAML -q
log "  ✓ Bridge dependencies installed"

# Install adsbcot if not skipping ADS-B
if [[ "${BIGMAC_SKIP_ADSB}" != "1" ]]; then
    "${BIGMAC_VENV}/bin/pip" install adsbcot -q
    log "  ✓ adsbcot installed"
fi

# ---------------------------------------------------------------------------
#  Step 5: OTS Data Directory
# ---------------------------------------------------------------------------

log "Step 5: Setting up OTS data directory..."

mkdir -p "${BIGMAC_DATA}"/{uploads,logs,ca}

# Copy OTS config if not present
if [[ ! -f "${BIGMAC_DATA}/config.yml" ]]; then
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        -e "s|__BIGMAC_USER__|${BIGMAC_USER}|g" \
        "${SCRIPT_DIR}/config/ots-config.yml" > "${BIGMAC_DATA}/config.yml"
    log "  ✓ Created OTS config at ${BIGMAC_DATA}/config.yml"
else
    log "  ✓ OTS config already exists"
fi

# Copy bridge scripts
cp -n "${SCRIPT_DIR}/bridges/meshcore_bridge.py" "${BIGMAC_DATA}/meshcore_bridge.py" 2>/dev/null || true
cp -n "${SCRIPT_DIR}/bridges/meshtastic_bridge.py" "${BIGMAC_DATA}/meshtastic_bridge.py" 2>/dev/null || true
log "  ✓ Bridge scripts in place"

# ---------------------------------------------------------------------------
#  Step 6: MediaMTX (optional)
# ---------------------------------------------------------------------------

if [[ "${BIGMAC_SKIP_MEDIAMTX}" != "1" ]]; then
    log "Step 6: Installing MediaMTX..."

    if [[ ! -f /usr/local/bin/mediamtx ]]; then
        # Note: v1.17.1 uses "arm64" not "arm64v8" in filename
        MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
        TMP_MTX=$(mktemp -d)
        log "  Downloading MediaMTX ${MEDIAMTX_VERSION}..."
        curl -sL "${MEDIAMTX_URL}" | tar xz -C "${TMP_MTX}"
        sudo mv "${TMP_MTX}/mediamtx" /usr/local/bin/mediamtx
        sudo chmod +x /usr/local/bin/mediamtx
        rm -rf "${TMP_MTX}"
        log "  ✓ MediaMTX ${MEDIAMTX_VERSION} installed"
    else
        log "  ✓ MediaMTX already installed"
    fi

    # MediaMTX config directory
    sudo mkdir -p /etc/mediamtx
    if [[ ! -f /etc/mediamtx/mediamtx.yml ]]; then
        sudo cp "${SCRIPT_DIR}/config/mediamtx.yml" /etc/mediamtx/mediamtx.yml 2>/dev/null || {
            # Generate minimal config if template not provided
            sudo tee /etc/mediamtx/mediamtx.yml > /dev/null <<'MTXEOF'
# MediaMTX minimal config for BigMacATAK
logLevel: info
api: yes
apiAddress: :9997
rtsp: yes
rtspAddress: :8554
rtmp: yes
rtmpAddress: :1935
hls: yes
hlsAddress: :8888
webrtc: yes
webrtcAddress: :8889
MTXEOF
        }
        log "  ✓ MediaMTX config created"
    fi
else
    log "Step 6: Skipping MediaMTX (BIGMAC_SKIP_MEDIAMTX=1)"
fi

# ---------------------------------------------------------------------------
#  Step 7: readsb (optional, for ADS-B)
# ---------------------------------------------------------------------------

if [[ "${BIGMAC_SKIP_ADSB}" != "1" ]]; then
    log "Step 7: Setting up readsb..."

    if ! command -v readsb &>/dev/null && [[ ! -f /usr/local/bin/readsb ]]; then
        # Build from source (most reliable for arm64)
        log "  Building readsb from source..."
        READSB_TMP=$(mktemp -d)
        git clone --depth 1 https://github.com/wiedehopf/readsb.git "${READSB_TMP}/readsb"
        cd "${READSB_TMP}/readsb"
        make -j$(nproc) RTLSDR=yes
        sudo cp readsb /usr/local/bin/readsb
        sudo chmod +x /usr/local/bin/readsb
        cd "${SCRIPT_DIR}"
        rm -rf "${READSB_TMP}"
        log "  ✓ readsb built and installed"
    else
        log "  ✓ readsb already installed"
    fi

    # Blacklist DVB kernel modules so RTL-SDR works in SDR mode
    if [[ ! -f /etc/modprobe.d/rtlsdr-blacklist.conf ]]; then
        sudo tee /etc/modprobe.d/rtlsdr-blacklist.conf > /dev/null <<'BLACKLIST'
# Blacklist DVB modules to allow RTL-SDR for ADS-B
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist rtl8xxxu
BLACKLIST
        log "  ✓ Created RTL-SDR blacklist"
    fi

    # readsb output directory
    sudo mkdir -p /run/readsb
    sudo chown "${BIGMAC_USER}:${BIGMAC_USER}" /run/readsb

    # adsbcot config
    mkdir -p "${BIGMAC_DATA}/adsbcot"
    if [[ ! -f "${BIGMAC_DATA}/adsbcot/config.ini" ]]; then
        sed \
            -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
            "${SCRIPT_DIR}/config/adsbcot/config.ini" > "${BIGMAC_DATA}/adsbcot/config.ini"
        log "  ✓ adsbcot config created"
    fi

    # Add user to dialout group for serial access
    sudo usermod -aG dialout "${BIGMAC_USER}" 2>/dev/null || true
else
    log "Step 7: Skipping ADS-B (BIGMAC_SKIP_ADSB=1)"
fi

# ---------------------------------------------------------------------------
#  Step 8: OpenTAKServer Web UI
# ---------------------------------------------------------------------------

log "Step 8: Installing OpenTAKServer Web UI..."

OTS_UI_DIR="/var/www/opentakserver"
sudo mkdir -p "${OTS_UI_DIR}"

# Check if UI is already installed
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
#  Step 9: nginx Configuration
# ---------------------------------------------------------------------------

log "Step 9: Configuring nginx..."

NGINX_CONF_DIR="/etc/nginx"
NGINX_SITES_AVAILABLE="${NGINX_CONF_DIR}/sites-available"
NGINX_SITES_ENABLED="${NGINX_CONF_DIR}/sites-enabled"
NGINX_STREAMS="${NGINX_CONF_DIR}/streams"

sudo mkdir -p "${NGINX_SITES_AVAILABLE}" "${NGINX_SITES_ENABLED}" "${NGINX_STREAMS}"

# Ensure nginx.conf includes our config directories
if ! sudo grep -q "include.*streams" "${NGINX_CONF_DIR}/nginx.conf" 2>/dev/null; then
    # Add stream block if not present
    if ! sudo grep -q "^stream" "${NGINX_CONF_DIR}/nginx.conf"; then
        echo -e "\nstream {\n    include ${NGINX_STREAMS}/*;\n}" | sudo tee -a "${NGINX_CONF_DIR}/nginx.conf" > /dev/null
        log "  ✓ Added stream block to nginx.conf"
    fi
fi

# Ensure proxy_params exists
if [[ ! -f "${NGINX_CONF_DIR}/proxy_params" ]]; then
    sudo tee "${NGINX_CONF_DIR}/proxy_params" > /dev/null <<'PROXYEOF'
proxy_set_header Host $http_host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
PROXYEOF
    log "  ✓ Created proxy_params"
fi

# Install nginx server configs
for tmpl in "${SCRIPT_DIR}"/config/nginx/servers/*; do
    name=$(basename "$tmpl")
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        -e "s|__BIGMAC_USER__|${BIGMAC_USER}|g" \
        "$tmpl" | sudo tee "${NGINX_SITES_AVAILABLE}/${name}" > /dev/null
    sudo ln -sf "${NGINX_SITES_AVAILABLE}/${name}" "${NGINX_SITES_ENABLED}/${name}"
done

# Install nginx stream configs
for tmpl in "${SCRIPT_DIR}"/config/nginx/streams/*; do
    name=$(basename "$tmpl")
    sed \
        -e "s|__BIGMAC_DATA__|${BIGMAC_DATA}|g" \
        "$tmpl" | sudo tee "${NGINX_STREAMS}/${name}" > /dev/null
done

# Remove default site if present
sudo rm -f "${NGINX_SITES_ENABLED}/default"

# Test nginx config
if sudo nginx -t 2>/dev/null; then
    sudo systemctl enable --now nginx
    sudo systemctl reload nginx
    log "  ✓ nginx configured and running"
else
    warn "  ⚠ nginx config test failed — check configs manually"
    sudo nginx -t 2>&1 || true
fi

# ---------------------------------------------------------------------------
#  Step 10: systemd Services
# ---------------------------------------------------------------------------

log "Step 10: Installing systemd services..."

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

# Enable core services
CORE_SERVICES=(opentakserver eud-handler-tcp eud-handler-ssl cot-parser)
for svc in "${CORE_SERVICES[@]}"; do
    sudo systemctl enable "${svc}.service"
    log "  ✓ Enabled ${svc}"
done

# Enable bridges (won't start until hardware is connected)
BRIDGE_SERVICES=(meshcore-bridge meshtastic-bridge)
for svc in "${BRIDGE_SERVICES[@]}"; do
    sudo systemctl enable "${svc}.service"
    log "  ✓ Enabled ${svc}"
done

# Enable ADS-B services if not skipped
if [[ "${BIGMAC_SKIP_ADSB}" != "1" ]]; then
    sudo systemctl enable readsb.service adsbcot.service
    log "  ✓ Enabled readsb + adsbcot"
fi

# Enable MediaMTX if not skipped
if [[ "${BIGMAC_SKIP_MEDIAMTX}" != "1" ]]; then
    sudo systemctl enable mediamtx.service 2>/dev/null || true
    log "  ✓ Enabled mediamtx"
fi

# ---------------------------------------------------------------------------
#  Step 11: Start Core Services
# ---------------------------------------------------------------------------

log "Step 11: Starting core services..."

# Start OTS (this will generate CA on first run)
sudo systemctl start opentakserver || {
    warn "  ⚠ OTS failed to start — check: journalctl -u opentakserver"
}

# Give OTS time to initialize and generate CA
log "  Waiting for OTS to initialize..."
sleep 10

# Start EUD handlers and CoT parser
for svc in eud-handler-tcp eud-handler-ssl cot-parser; do
    sudo systemctl start "${svc}" || warn "  ⚠ ${svc} failed to start"
done

log "  ✓ Core services started"

# ---------------------------------------------------------------------------
#  Summary
# ---------------------------------------------------------------------------

PI_IP=$(hostname -I | awk '{print $1}')

echo ""
log "════════════════════════════════════════════════════════════"
log "  BigMacATAK Pi installation complete!"
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
log "    systemctl status meshcore-bridge"
log "    systemctl status meshtastic-bridge"
if [[ "${BIGMAC_SKIP_ADSB}" != "1" ]]; then
    log "    systemctl status readsb"
    log "    systemctl status adsbcot"
fi
log ""
log "  Logs: journalctl -u <service-name> -f"
log ""

if [[ "${BIGMAC_SKIP_ADSB}" != "1" ]]; then
    log "  ADS-B: Plug in RTL-SDR dongle and run:"
    log "    sudo systemctl start readsb adsbcot"
    log "    (May need reboot if DVB modules were loaded)"
fi

log ""
log "  MeshCore: Ensure MQTT companion is publishing to localhost:1883, then:"
log "    sudo systemctl start meshcore-bridge"
log ""
log "  Meshtastic: Plug in USB node and run:"
log "    sudo systemctl start meshtastic-bridge"
log ""
