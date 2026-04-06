# BigMacATAK 🍔📡

> **B**rian's **I**mages **G**et **M**angled on **A**pple **C**hips

**OpenTAK Server (OTS) for macOS + Raspberry Pi with MeshCore integration**

A repeatable guide for running [OpenTAK Server](https://github.com/brian7704/OpenTAKServer) natively on Apple Silicon Macs and Raspberry Pi, with MeshCore/Meshtastic LoRa mesh network integration and ADS-B aircraft tracking for ATAK.

## Platforms

| Platform | Directory | Description |
|----------|-----------|-------------|
| **macOS** | [This directory](#quick-start) | Native OTS on Apple Silicon |
| **Raspberry Pi** | [`pi/`](pi/README.md) | Complete Pi installer with ADS-B + bridges |

## Why Native macOS?

OTS Docker images are `linux/amd64` only. Running them on Apple Silicon via QEMU emulation causes a [pika threading bug](https://github.com/brian7704/OpenTAKServer/issues/) in the EUD handler — `SelectConnection` channel methods called from the socket thread silently fail under emulation, meaning **no CoT is ever delivered to ATAK**. The native macOS install eliminates this entirely.

## What's Included

| Component | Description |
|-----------|-------------|
| [OTS Install Guide](docs/01-ots-install.md) | Native macOS OTS installation with PostgreSQL |
| [Service Setup](docs/02-services.md) | LaunchDaemon plists for all components |
| [MeshCore Bridge](docs/03-meshcore-bridge.md) | MQTT → OTS CoT bridge for mesh node presence + chat |
| [Companion Bridge](docs/04-companion-bridge.md) | USB companion device → MQTT for decoded mesh messages |
| [Data Packages](docs/05-data-packages.md) | Creating and pushing ATAK data packages to OTS |
| [ATAK Client Setup](docs/06-atak-client.md) | Connecting ATAK/iTAK to your OTS server |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        macOS (Apple Silicon)                     │
│                                                                  │
│  ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐ │
│  │ MeshCore │    │ meshcore- │    │mosquitto │    │ meshcore- │ │
│  │ Companion├───►│   mqtt    ├───►│  MQTT    ├───►│  bridge  │ │
│  │  (USB)   │    │ (serial)  │    │ broker   │    │ (CoT)    │ │
│  └──────────┘    └───────────┘    └──────────┘    └────┬─────┘ │
│                                                         │       │
│  ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌────▼─────┐ │
│  │ MeshCore │    │  Native   │    │          │    │          │ │
│  │ Repeater ├───►│   MQTT    ├───►│          │    │ RabbitMQ │ │
│  │ (WiFi)   │    │ heartbeat │    │          │    │          │ │
│  └──────────┘    └───────────┘    │          │    └────┬─────┘ │
│                                    │          │         │       │
│                                    │          │    ┌────▼─────┐ │
│                                    │          │    │   OTS    │ │
│                  ┌───────────┐    │          │    │  + EUD   │ │
│                  │ PostgreSQL│◄───┤          │◄───┤ handler  │ │
│                  └───────────┘    │          │    └────┬─────┘ │
│                                    │          │         │       │
│                                    └──────────┘    ┌────▼─────┐ │
│                                                    │  ATAK /  │ │
│                                                    │  iTAK    │ │
│                                                    └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Node Status** (firmware native MQTT): MeshCore repeater → WiFi → mosquitto → meshcore-bridge → RabbitMQ → ATAK (node presence markers)
2. **Mesh Messages** (companion bridge): Radio RF → USB companion → meshcore-mqtt → mosquitto → meshcore-bridge → RabbitMQ → ATAK (GeoChat messages)
3. **Standard CoT**: ATAK ↔ TCP:8088 ↔ EUD handler ↔ RabbitMQ (normal TAK operations)

## Quick Start

```bash
# 1. Install OTS natively
curl https://i.opentakserver.io/macos_installer | bash

# 2. Fix PostgreSQL (installer doesn't set it up)
brew install postgresql@17
brew services start postgresql@17
createuser ots
createdb -O ots ots

# 3. Install MeshCore bridge
cp services/meshcore_bridge.py ~/ots/
sudo cp plists/launchd.meshcore-bridge.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-bridge.plist

# 4. (Optional) Install companion bridge for mesh messages
pip install meshcore-mqtt
sudo cp plists/launchd.meshcore-mqtt.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-mqtt.plist
```

See [docs/01-ots-install.md](docs/01-ots-install.md) for the full walkthrough.

## Requirements

- macOS on Apple Silicon (M1/M2/M3/M4)
- [Homebrew](https://brew.sh)
- ATAK (Android) or iTAK (iOS) client
- Network connectivity between Mac and ATAK device (Tailscale recommended for remote access)
- (Optional) MeshCore companion device (Heltec V3/V4, T-Deck, etc.) for mesh integration

## Known Issues

- **cot_parser fork crash**: macOS ObjC runtime blocks `os.fork()`. Workaround: run cot_parser in single-process mode with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. See [docs/02-services.md](docs/02-services.md).
- **OTS Web UI 502**: nginx SSL proxy config returns 502 on login. Cosmetic — doesn't affect ATAK/CoT operations.
- **iTAK auth errors**: iTAK may reject plain TCP connections that ATAK accepts fine. May need certificate-based auth.

## License

MIT
