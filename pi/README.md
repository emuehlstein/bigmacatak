# BigMacATAK Pi (upstream-only)

**Vanilla OpenTAK Server deployment for Raspberry Pi** — OTS + PostgreSQL + RabbitMQ + nginx, no extras.

This is the `upstream-only` branch. For MeshCore/Meshtastic bridges, ADS-B, and MediaMTX, see the `main` branch.

## What's Included

| Component | Description | Ports |
|-----------|-------------|-------|
| **OpenTAK Server** | TAK server backend (v1.7.10) | 8081 (API), 8088 (TCP), 8089 (SSL) |
| **OpenTAK Server UI** | Web dashboard (v1.7.4) | 8080 (HTTP), 443 (HTTPS) |
| **nginx** | Reverse proxy + SSL termination | 80, 443, 8080, 8443, 8446 |
| **RabbitMQ** | Message broker with MQTT | 1883 (MQTT), 5672 (AMQP), 8883 (SSL) |
| **PostgreSQL** | Database backend | 5432 |

## Requirements

- **Raspberry Pi 4/5** (4GB+ RAM recommended)
- **Debian 12+ (Bookworm) / Debian 13 (Trixie) / Raspberry Pi OS** — arm64
- **Storage**: 16GB+ SD card or SSD (SSD strongly recommended)

## Quick Start

```bash
git clone https://github.com/emuehlstein/bigmacatak.git
cd bigmacatak
git checkout upstream-only
cd pi
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

## Configuration

### Environment Variables

Set these before running `install.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `BIGMAC_USER` | Current user | User to run services as |
| `BIGMAC_DATA` | `~/ots` | OTS data directory |
| `BIGMAC_VENV` | `~/.opentakserver_venv` | Python virtualenv |

### Main Config File

```
~/ots/config.yml
```

## Services

All services are managed via systemd:

```bash
# Check status
sudo systemctl status opentakserver
sudo systemctl status eud-handler-tcp
sudo systemctl status eud-handler-ssl
sudo systemctl status cot-parser

# View logs
journalctl -u opentakserver -f

# Restart
sudo systemctl restart opentakserver
```

## Network Ports

| Port | Protocol | Service |
|------|----------|---------|
| 80 | TCP | HTTP → HTTPS redirect |
| 443 | TCP | HTTPS (nginx → OTS) |
| 1883 | TCP | MQTT (RabbitMQ) |
| 5672 | TCP | AMQP (RabbitMQ) |
| 8080 | TCP | HTTP Web UI |
| 8081 | TCP | OTS API (localhost only) |
| 8088 | TCP | TAK TCP (unencrypted) |
| 8089 | TCP | TAK SSL (encrypted) |
| 8443 | TCP | Marti API (HTTPS) |
| 8446 | TCP | Certificate enrollment |
| 8883 | TCP | MQTT SSL (nginx → RabbitMQ) |

## Patches Applied

| Patch | Problem | Fix |
|-------|---------|-----|
| **BigInteger size** | `data_packages.size` is INTEGER (max 2.1 GB) | Patched model + DB column to BigInteger/bigint |
| **TMPDIR** | `/tmp` is RAM-backed tmpfs on Debian 13 — large uploads fill it | Systemd service sets `TMPDIR` to disk-backed `~/ots/tmp` |
| **MAX_CONTENT_LENGTH** | Default 0 = reject all uploads | Config set to 10 GB |

## Updating

```bash
cd bigmacatak/pi
git pull
./install.sh
```

## Adding Bridges Later

If you want to add MeshCore, Meshtastic, ADS-B, or MediaMTX later, switch to the `main` branch:

```bash
git checkout main
./install.sh
```

The installer is idempotent and will add the additional components.

## License

MIT

## Credits

- [OpenTAK Server](https://github.com/brian7704/OpenTAKServer) by brian7704
- [OpenTAK Server UI](https://github.com/brian7704/OpenTAKServer-UI) by brian7704
