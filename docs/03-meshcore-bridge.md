# MeshCore → OTS CoT Bridge

The MeshCore bridge converts MeshCore MQTT data into CoT (Cursor on Target) events that ATAK can display.

## What It Does

### Node Status → Presence Markers
MeshCore repeaters with native MQTT enabled publish status heartbeats:
- Topic: `meshcore/{region}/{node_id}/status`
- Converted to: `a-f-G-U-C` CoT (friendly ground unit)
- Shows up as: Node marker in ATAK with callsign, model, firmware, frequency, and telemetry

### Mesh Messages → GeoChat
Decoded mesh messages (via the companion bridge) are converted to GeoChat CoT:
- Channel messages: `meshcore/message/channel/{idx}` → GeoChat room "MeshCore Channel {idx}"
- Direct messages: `meshcore/message/direct/{pubkey}` → GeoChat room "MeshCore Direct"
- Type: `b-t-f` CoT (text/chat)
- Shows up as: Chat messages in ATAK's built-in chat system
- SNR/RSSI metadata is appended to message text when available

### RF Packet Metadata (Future)
- Topic: `meshcore/{region}/{node_id}/packets`
- Currently: logged only
- Future: RF sensor CoT for coverage visualization

## Installation

### Prerequisites
- mosquitto MQTT broker running on localhost:1883
- RabbitMQ running on localhost:5672
- OTS installed with EUD handler active

### Install Dependencies

The bridge uses the OTS Python venv:

```bash
# Should already be installed, but verify:
~/.opentakserver_venv/bin/pip show paho-mqtt pika PyYAML
```

### Deploy the Bridge

```bash
# Copy bridge script
cp services/meshcore_bridge.py ~/ots/

# Install LaunchDaemon
sudo cp plists/launchd.meshcore-bridge.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/launchd.meshcore-bridge.plist
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-bridge.plist
```

### Verify

```bash
# Check it's running
sudo launchctl list | grep meshcore-bridge

# Check logs
tail -f ~/ots/logs/meshcore-bridge.log

# Test with a simulated status heartbeat
mosquitto_pub -h localhost \
  -t "meshcore/ORD/TEST123/status" \
  -m '{"status":"online","origin":"TestNode","origin_id":"TEST123","model":"Station G2","firmware_version":"1.14.1","radio":"910.525,62.5,7,5","stats":{"noise_floor":-105,"uptime_secs":7000}}'

# Test with a simulated channel message
mosquitto_pub -h localhost \
  -t "meshcore/message/channel/0" \
  -m '{"type":"CHAN","channel_idx":0,"text":"Hello from mesh!","sender_timestamp":1712234567}'
```

## Configuration

The bridge reads from OTS's `~/ots/config.yml`. Add these keys:

```yaml
# MeshCore bridge config
OTS_ENABLE_MESHCORE: true
OTS_MESHCORE_MQTT_HOST: "localhost"
OTS_MESHCORE_MQTT_PORT: 1883
OTS_MESHCORE_TOPIC: "meshcore"
OTS_MESHCORE_GROUP: "MeshCore"
OTS_MESHCORE_STALE_HOURS: 1
```

If your mosquitto broker requires authentication:

```yaml
OTS_MESHCORE_MQTT_USERNAME: "meshcore"
OTS_MESHCORE_MQTT_PASSWORD: "meshcore"
```

## MQTT Topic Structure

### From MeshCore Firmware (native MQTT)
```
meshcore/{region}/{node_id}/status    — JSON heartbeat every ~5min
meshcore/{region}/{node_id}/packets   — RF packet metadata
```

### From Companion Bridge (meshcore-mqtt)
```
meshcore/message/channel/{idx}        — Channel messages (broadcast)
meshcore/message/direct/{pubkey}      — Direct messages (private)
meshcore/status                       — Companion connection status (retained)
meshcore/events/connection            — Connect/disconnect events
meshcore/battery                      — Battery status
meshcore/device_info                  — Device information
```

## CoT Output

### Node Status CoT
```xml
<event version="2.0" uid="MeshCore-{origin_id}" type="a-f-G-U-C" how="m-g" ...>
  <point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0" />
  <detail>
    <takv device="{model}" version="{firmware}" platform="MeshCore" os="MeshCore" />
    <contact callsign="{node_name}" endpoint="MQTT" />
    <meshcore status="online" freq_mhz="910.525" noise_floor_dbm="-105" ... />
    <__group name="MeshCore" role="Team Member" />
  </detail>
</event>
```

### GeoChat CoT
```xml
<event version="2.0" uid="GeoChat.MeshCore-channel-0.All Chat Rooms.{uuid}" type="b-t-f" how="h-g-i-g-o" ...>
  <point lat="0.0" lon="0.0" ... />
  <detail>
    <__chat chatroom="MeshCore Channel 0" senderCallsign="MeshCore-Ch0" ... />
    <link uid="MeshCore-channel-0" type="a-f-G-U-C" relation="p-p" />
    <remarks source="MeshCore-channel-0" to="MeshCore Channel 0">Hello from mesh!</remarks>
  </detail>
</event>
```

## Design Notes

- **Standalone**: No Flask/OTS dependency — uses paho-mqtt + pika BlockingConnection directly
- **Reconnection**: Auto-reconnects to both MQTT and RabbitMQ on failure
- **Thread-safe**: Uses pika BlockingConnection (not SelectConnection) to avoid the threading bug that breaks Docker OTS on ARM64
- **Node cache**: Maintains a cache of known nodes for callsign resolution in direct messages
