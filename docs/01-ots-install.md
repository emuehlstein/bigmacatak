# OTS Native macOS Installation

## Prerequisites

```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## Step 1: Run the OTS Installer

The official macOS installer handles most Homebrew dependencies:

```bash
curl https://i.opentakserver.io/macos_installer | bash
```

This installs:
- Python 3.12 (via Homebrew)
- RabbitMQ
- nginx
- MediaMTX
- ffmpeg
- OTS Python packages into `~/.opentakserver_venv/`

The installer will prompt for `sudo` to create LaunchDaemon plists.

## Step 2: PostgreSQL Setup

The installer defaults to PostgreSQL but doesn't install it. Fix that:

```bash
brew install postgresql@17
brew services start postgresql@17

# Create the OTS database and user
createuser ots
createdb -O ots ots
```

## Step 3: Configure OTS

Edit `~/ots/config.yml`:

```yaml
# Database — use the PostgreSQL instance we just created
SQLALCHEMY_DATABASE_URI: "postgresql+psycopg://ots@127.0.0.1/ots"
```

Install the PostgreSQL driver in the OTS venv:

```bash
~/.opentakserver_venv/bin/pip install "psycopg[binary]"
```

## Step 4: Start OTS

The installer creates a LaunchDaemon at `/Library/LaunchDaemons/launchd.opentakserver.plist` that auto-starts OTS. Verify:

```bash
# Check if OTS is running
curl -s http://localhost:8081/api/health | python3 -m json.tool

# Check the LaunchDaemon
sudo launchctl list | grep opentakserver
```

## Step 5: Start the EUD Handler

The EUD handler manages ATAK client connections. It's not auto-started by the installer:

```bash
# Start TCP handler (port 8088)
~/.opentakserver_venv/bin/eud_handler &

# Start SSL handler (port 8089) — optional, for certificate auth
~/.opentakserver_venv/bin/eud_handler --ssl &
```

For persistent operation, see [02-services.md](02-services.md) for LaunchDaemon setup.

## Step 6: Fix cot_parser

The `cot_parser` process crashes on macOS due to ObjC fork safety. Run it in single-process mode:

```python
#!/usr/bin/env python3
"""cot_parser launcher — single-process mode for macOS."""
import os
os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'

from opentakserver.cot_parser.cot_parser import app, logger, db, CoTController, child_processes
from flask_socketio import SocketIO

sio = SocketIO(message_queue='amqp://' + app.config.get('OTS_RABBITMQ_SERVER_ADDRESS'))
cot_parser = CoTController(app.app_context(), logger, db, sio)
print('cot_parser running (no fork)...')
cot_parser.run()
```

Save as `~/ots/cot_parser_nofork.py` and run:

```bash
~/.opentakserver_venv/bin/python ~/ots/cot_parser_nofork.py &
```

## Step 7: Verify

```bash
# API health
curl -s http://localhost:8081/api/health

# Check RabbitMQ queues
rabbitmqctl list_queues

# Check all processes
ps aux | grep -E 'opentakserver|eud_handler|cot_parser' | grep -v grep
```

## Default Credentials

- **OTS Web UI**: `administrator` / `password`
- **URL**: `http://localhost:8081` (API) / `https://localhost:8443` (Web UI via nginx)

## Directory Layout

```
~/ots/                          # OTS data directory
├── config.yml                  # Main config
├── logs/                       # Log files
├── ca/                         # Certificate authority
├── mediamtx/                   # Video streaming config
└── meshcore_bridge.py          # MeshCore → CoT bridge

~/.opentakserver_venv/          # Python virtual environment
├── bin/opentakserver           # OTS API server
├── bin/eud_handler             # ATAK connection handler
└── bin/cot_parser              # CoT XML parser (don't use directly on macOS)
```

## Networking

For remote ATAK connections, [Tailscale](https://tailscale.com) is recommended:

```bash
# Get your Tailscale IP
tailscale ip -4

# ATAK connects to: <tailscale-ip>:8088:tcp
```
