# BigMacATAK 🍔📡

> **B**rian's **I**mages **G**et **M**angled on **A**pple **C**hips

**Vanilla OpenTAK Server (OTS) for macOS + Raspberry Pi**

A repeatable guide for running [OpenTAK Server](https://github.com/brian7704/OpenTAKServer) natively on Apple Silicon Macs and Raspberry Pi.

## Branches

| Branch | Description |
|--------|-------------|
| **`main`** | Full install with MeshCore, Meshtastic, ADS-B, MediaMTX |
| **`upstream-only`** | Vanilla OTS — no bridges, no ADS-B, no video streaming |

## Platforms

| Platform | Directory | Description |
|----------|-----------|-------------|
| **macOS** | [This directory](#quick-start) | Native OTS on Apple Silicon |
| **Raspberry Pi** | [`pi/`](pi/README.md) | Complete Pi installer |

## Why Native macOS?

OTS Docker images are `linux/amd64` only. Running them on Apple Silicon via QEMU emulation causes a [pika threading bug](https://github.com/brian7704/OpenTAKServer/issues/) in the EUD handler — `SelectConnection` channel methods called from the socket thread silently fail under emulation, meaning **no CoT is ever delivered to ATAK**. The native macOS install eliminates this entirely.

## What's Included (upstream-only)

| Component | Description |
|-----------|-------------|
| [OTS Install Guide](docs/01-ots-install.md) | Native macOS OTS installation with PostgreSQL |
| [Service Setup](docs/02-services.md) | LaunchDaemon plists for all components |
| [Data Packages](docs/05-data-packages.md) | Creating and pushing ATAK data packages to OTS |
| [ATAK Client Setup](docs/06-atak-client.md) | Connecting ATAK/iTAK to your OTS server |

## Architecture (upstream-only)

```
┌────────────────────────────────────────────────────────┐
│                   Raspberry Pi / macOS                  │
│                                                        │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│  │PostgreSQL│◄───┤ RabbitMQ │◄───┤   OTS    │         │
│  └──────────┘    └──────────┘    │  + EUD   │         │
│                                  │ handler  │         │
│                                  └────┬─────┘         │
│                                       │               │
│                                  ┌────▼─────┐         │
│                                  │  ATAK /  │         │
│                                  │  iTAK    │         │
│                                  └──────────┘         │
└────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Install OTS natively (macOS)
curl https://i.opentakserver.io/macos_installer | bash

# 2. Fix PostgreSQL (installer doesn't set it up)
brew install postgresql@17
brew services start postgresql@17
createuser ots
createdb -O ots ots
```

See [docs/01-ots-install.md](docs/01-ots-install.md) for the full walkthrough.

For Raspberry Pi, see [`pi/README.md`](pi/README.md).

## Requirements

- macOS on Apple Silicon (M1/M2/M3/M4) **or** Raspberry Pi 4/5 (Debian 12+)
- [Homebrew](https://brew.sh) (macOS only)
- ATAK (Android) or iTAK (iOS) client
- Network connectivity between host and ATAK device (Tailscale recommended for remote access)

## Known Issues

- **cot_parser fork crash**: macOS ObjC runtime blocks `os.fork()`. Workaround: run cot_parser in single-process mode with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. See [docs/02-services.md](docs/02-services.md).
- **OTS Web UI 502**: nginx SSL proxy config returns 502 on login. Cosmetic — doesn't affect ATAK/CoT operations.
- **iTAK auth errors**: iTAK may reject plain TCP connections that ATAK accepts fine. May need certificate-based auth.

## License

MIT
