# BigMacATAK Pi

**Complete TAK server deployment for Raspberry Pi** — OpenTAK Server + MeshCore/Meshtastic bridges + ADS-B tracking + video streaming, all in one idempotent installer.

## What's Included

| Component | Description | Ports |
|-----------|-------------|-------|
| **OpenTAK Server** | TAK server backend (v1.7.10) | 8081 (API), 8088 (TCP), 8089 (SSL) |
| **OpenTAK Server UI** | Web dashboard (v1.7.4) | 8080 (HTTP), 443 (HTTPS) |
| **nginx** | Reverse proxy + SSL termination | 80, 443, 8080, 8443, 8446 |
| **RabbitMQ** | Message broker with MQTT | 1883 (MQTT), 5672 (AMQP), 8883 (SSL) |
| **PostgreSQL** | Database backend | 5432 |
| **MediaMTX** | RTSP/RTMP/WebRTC video server | 8554 (RTSP), 1935 (RTMP), 8889 (WebRTC) |
| **readsb** | ADS-B decoder for RTL-SDR | — |
| **adsbcot** | ADS-B → CoT bridge | — |
| **MeshCore Bridge** | MeshCore MQTT → OTS CoT | — |
| **Meshtastic Bridge** | Meshtastic serial → OTS CoT | — |

## Requirements

- **Raspberry Pi 4/5** (4GB+ RAM recommended)
- **Debian 13 (Trixie) / Raspberry Pi OS** — arm64 (Debian 12/Bookworm also works)
- **Storage**: 16GB+ SD card or SSD (SSD strongly recommended)
- **Optional**: RTL-SDR dongle for ADS-B
- **Optional**: MeshCore companion radio
- **Optional**: Meshtastic USB node

## Quick Start

```bash
# Clone the repo
git clone https://github.com/youruser/bigmacatak-pi.git
cd bigmacatak-pi

# Run the installer
./install.sh
```

The installer is **idempotent** — you can run it multiple times safely.

## Post-Installation

### Access the Web UI

```
http://<pi-ip>:8080
```

**Default credentials:**
- Username: `administrator`
- Password: `password`

⚠️ **Change this immediately!**

### Connect ATAK/iTAK

1. Open the Web UI → click "ATAK QR Code" or "iTAK QR Code"
2. Scan with your phone/tablet
3. The client will auto-configure with the server's CA certificate

**Or manually:**
- Server: `<pi-ip>`
- TCP Port: `8088` (unencrypted)
- SSL Port: `8089` (encrypted, needs cert)
- Certificate: Download truststore from Web UI

### Enable ADS-B Tracking

1. Plug in RTL-SDR dongle
2. Reboot (if DVB modules were loaded)
3. Start services:

```bash
sudo systemctl start readsb adsbcot
```

Aircraft will appear in ATAK as CoT tracks.

### Enable MeshCore Bridge

The MeshCore bridge reads from MQTT (localhost:1883) where the MeshCore Home Assistant integration publishes node positions.

```bash
# Ensure your HA MeshCore integration is publishing to localhost:1883
sudo systemctl start meshcore-bridge
```

### Enable Meshtastic Bridge

1. Plug in Meshtastic USB node
2. Find the serial port:
   ```bash
   ls /dev/ttyUSB* /dev/ttyACM*
   ```
3. Update config if needed:
   ```yaml
   # ~/ots/config.yml
   OTS_MESHTASTIC_SERIAL_PORT: "/dev/ttyUSB0"
   ```
4. Start the bridge:
   ```bash
   sudo systemctl start meshtastic-bridge
   ```

## Configuration

### Environment Variables

Set these before running `install.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `BIGMAC_USER` | Current user | User to run services as |
| `BIGMAC_DATA` | `~/ots` | OTS data directory |
| `BIGMAC_VENV` | `~/.opentakserver_venv` | Python virtualenv |
| `BIGMAC_SKIP_ADSB` | `0` | Set to `1` to skip ADS-B components |
| `BIGMAC_SKIP_MEDIAMTX` | `0` | Set to `1` to skip MediaMTX |

### Main Config File

```
~/ots/config.yml
```

Edit this for OTS-specific settings (secret key, DB URL, etc.).

### Service Configs

| Service | Config Location |
|---------|----------------|
| OTS | `~/ots/config.yml` |
| adsbcot | `~/ots/adsbcot/config.ini` |
| MediaMTX | `/etc/mediamtx/mediamtx.yml` |
| nginx | `/etc/nginx/sites-available/ots_*` |

## Services

All services are managed via systemd:

```bash
# Check status
sudo systemctl status opentakserver
sudo systemctl status eud-handler-tcp
sudo systemctl status eud-handler-ssl
sudo systemctl status cot-parser
sudo systemctl status meshcore-bridge
sudo systemctl status meshtastic-bridge
sudo systemctl status readsb
sudo systemctl status adsbcot
sudo systemctl status mediamtx

# View logs
journalctl -u opentakserver -f
journalctl -u readsb -f

# Restart a service
sudo systemctl restart opentakserver
```

## Network Ports

| Port | Protocol | Service |
|------|----------|---------|
| 80 | TCP | HTTP → HTTPS redirect |
| 443 | TCP | HTTPS (nginx → OTS) |
| 1883 | TCP | MQTT (RabbitMQ) |
| 1935 | TCP | RTMP (MediaMTX) |
| 5672 | TCP | AMQP (RabbitMQ) |
| 8080 | TCP | HTTP Web UI |
| 8081 | TCP | OTS API (localhost only) |
| 8088 | TCP | TAK TCP (unencrypted) |
| 8089 | TCP | TAK SSL (encrypted) |
| 8322 | TCP | RTSP SSL (nginx) |
| 8443 | TCP | Marti API (HTTPS) |
| 8446 | TCP | Certificate enrollment |
| 8554 | TCP | RTSP (MediaMTX) |
| 8883 | TCP | MQTT SSL (nginx → RabbitMQ) |
| 8889 | TCP | WebRTC (MediaMTX) |

## File Structure

```
~/ots/
├── config.yml              # Main OTS config
├── uploads/                # Uploaded files (data packages, etc.)
├── logs/                   # OTS logs
├── ca/                     # Certificate Authority
│   ├── ca.pem              # CA certificate (share with clients)
│   ├── ca-do-not-share.key # CA private key (KEEP SECRET)
│   └── certs/              # Generated certificates
├── meshcore_bridge.py      # MeshCore MQTT→CoT bridge
├── meshtastic_bridge.py    # Meshtastic serial→CoT bridge
└── adsbcot/
    └── config.ini          # adsbcot configuration
```

## Troubleshooting

### OTS won't start

```bash
journalctl -u opentakserver -n 50
```

Common issues:
- PostgreSQL not running: `sudo systemctl start postgresql`
- Database auth: Check `/etc/postgresql/*/main/pg_hba.conf` has `trust` for ots user

### ADS-B not working

```bash
# Check if DVB modules are blocking RTL-SDR
lsmod | grep dvb

# If loaded, reboot or unload:
sudo rmmod dvb_usb_rtl28xxu
sudo systemctl restart readsb

# Test RTL-SDR directly
rtl_test -t
```

### nginx 500 errors

```bash
sudo nginx -t
sudo tail -f /var/log/nginx/error.log
```

### Serial port permission denied

```bash
# Add user to dialout group
sudo usermod -aG dialout $USER
# Log out and back in
```

## Updating

```bash
cd bigmacatak-pi
git pull
./install.sh
```

The installer will upgrade components as needed.

## Uninstalling

```bash
# Stop all services
sudo systemctl stop opentakserver eud-handler-tcp eud-handler-ssl cot-parser \
    meshcore-bridge meshtastic-bridge readsb adsbcot mediamtx

# Disable services
sudo systemctl disable opentakserver eud-handler-tcp eud-handler-ssl cot-parser \
    meshcore-bridge meshtastic-bridge readsb adsbcot mediamtx

# Remove service files
sudo rm /etc/systemd/system/{opentakserver,eud-handler-tcp,eud-handler-ssl,cot-parser,meshcore-bridge,meshtastic-bridge,readsb,adsbcot,mediamtx}.service
sudo systemctl daemon-reload

# Remove data (CAUTION: destroys all data!)
rm -rf ~/ots ~/.opentakserver_venv

# Remove web UI
sudo rm -rf /var/www/opentakserver

# Optionally remove PostgreSQL database
sudo -u postgres dropdb ots
sudo -u postgres dropuser ots
```

## Roadmap

| Feature | Status | Doc |
|---------|--------|-----|
| TAK Server Federation | 🗺️ Planned | [docs/federation.md](docs/federation.md) |
| Tailscale auto-config | 💭 Idea | — |
| Web-based setup wizard | 💭 Idea | — |
| Docker alternative | 💭 Idea | — |

## License

MIT

## Credits

- [OpenTAK Server](https://github.com/brian7704/OpenTAKServer) by brian7704
- [OpenTAK Server UI](https://github.com/brian7704/OpenTAKServer-UI) by brian7704
- [MediaMTX](https://github.com/bluenviron/mediamtx)
- [readsb](https://github.com/wiedehopf/readsb)
- [adsbcot](https://github.com/ampledata/adsbcot)
