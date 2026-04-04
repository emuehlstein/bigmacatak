# macOS Services (LaunchDaemons)

All OTS components should run as LaunchDaemons for automatic startup and crash recovery.

## Service Overview

| Service | Plist | Port | Purpose |
|---------|-------|------|---------|
| OTS API | `launchd.opentakserver` | 8081 | REST API, web UI |
| EUD Handler (TCP) | `launchd.ots-eud-handler` | 8088 | ATAK TCP connections |
| EUD Handler (SSL) | `launchd.ots-eud-handler-ssl` | 8089 | ATAK SSL connections |
| CoT Parser | `launchd.ots-cot-parser` | — | CoT processing |
| RabbitMQ | `homebrew.mxcl.rabbitmq` | 5672 | Internal message queue |
| PostgreSQL | `homebrew.mxcl.postgresql@17` | 5432 | Database |
| nginx | `homebrew.mxcl.nginx` | 80, 443, 8080 | Reverse proxy |
| mosquitto | `homebrew.mxcl.mosquitto` | 1883 | MQTT broker |
| MeshCore Bridge | `launchd.meshcore-bridge` | — | MQTT → CoT |
| Companion Bridge | `launchd.meshcore-mqtt` | — | USB serial → MQTT |

## Installing Service Plists

All plists are in the `plists/` directory. Install them:

```bash
# Copy all plists
sudo cp plists/*.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/launchd.ots-*.plist
sudo chown root:wheel /Library/LaunchDaemons/launchd.meshcore-*.plist

# Load them
sudo launchctl load /Library/LaunchDaemons/launchd.ots-eud-handler.plist
sudo launchctl load /Library/LaunchDaemons/launchd.ots-eud-handler-ssl.plist
sudo launchctl load /Library/LaunchDaemons/launchd.ots-cot-parser.plist
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-bridge.plist
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-mqtt.plist
```

## Managing Services

```bash
# Check status
sudo launchctl list | grep -E 'ots|meshcore|opentakserver'

# Stop a service
sudo launchctl unload /Library/LaunchDaemons/launchd.meshcore-bridge.plist

# Start a service
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-bridge.plist

# View logs
tail -f ~/ots/logs/meshcore-bridge.log
tail -f ~/ots/logs/meshcore-mqtt.log
```

## Dependency Order

Services should start in this order (LaunchDaemon `OtherJobEnabled` handles most of this):

1. PostgreSQL, RabbitMQ, mosquitto (Homebrew services)
2. OTS API (`launchd.opentakserver` — waits for RabbitMQ)
3. EUD handlers, CoT parser
4. MeshCore bridge (`launchd.meshcore-bridge` — waits for RabbitMQ)
5. Companion bridge (`launchd.meshcore-mqtt` — independent)

## cot_parser macOS Fix

The standard `cot_parser` binary uses `os.fork()` which crashes on macOS due to ObjC runtime fork safety. The workaround runs it in single-process mode:

```xml
<!-- launchd.ots-cot-parser.plist uses an inline Python script -->
<key>ProgramArguments</key>
<array>
  <string>/Users/YOUR_USERNAME/.opentakserver_venv/bin/python</string>
  <string>-c</string>
  <string>
import os
os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'

from opentakserver.cot_parser.cot_parser import app, logger, db, CoTController, child_processes
from flask_socketio import SocketIO

sio = SocketIO(message_queue='amqp://' + app.config.get('OTS_RABBITMQ_SERVER_ADDRESS'))
cot_parser = CoTController(app.app_context(), logger, db, sio)
print('cot_parser running (no fork)...')
cot_parser.run()
  </string>
</array>
```

## Homebrew Services

These are managed by Homebrew, not custom plists:

```bash
# Start
brew services start rabbitmq
brew services start postgresql@17
brew services start mosquitto
brew services start nginx

# Check status
brew services list
```
