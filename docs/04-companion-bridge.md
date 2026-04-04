# MeshCore Companion Bridge (meshcore-mqtt)

The companion bridge connects a USB MeshCore companion device to MQTT, enabling decoded mesh messages to flow into the OTS pipeline.

## Why a Companion?

MeshCore repeaters with native MQTT only publish **status heartbeats** to the local broker. Actual mesh messages (channel broadcasts, direct messages, adverts) are **not** forwarded via native MQTT — they require a companion device that receives RF packets and decodes them.

The companion bridge ([ipnet-mesh/meshcore-mqtt](https://github.com/ipnet-mesh/meshcore-mqtt)) connects to a companion via USB serial, auto-fetches messages from the mesh network, and publishes decoded data to mosquitto.

## Supported Devices

Any MeshCore companion device works:
- Heltec V3 / V4
- LILYGO T-Deck
- RAK WisBlock
- Any device running MeshCore companion firmware

## Installation

```bash
# Install the companion bridge
pip install meshcore-mqtt

# Verify
meshcore-mqtt --help
```

## Configuration

Create `config.yaml`:

```yaml
mqtt:
  broker: localhost
  port: 1883
  username: ""          # Set if mosquitto requires auth
  password: ""
  topic_prefix: meshcore
  qos: 0
  retain: false

meshcore:
  connection_type: serial
  address: "/dev/cu.usbmodemXXXXXXXXX"   # Your USB device path
  baudrate: 115200
  timeout: 10
  auto_fetch_restart_delay: 10
  message_initial_delay: 15.0
  message_send_delay: 15.0
  events:
    - CONTACT_MSG_RECV
    - CHANNEL_MSG_RECV
    - CONNECTED
    - DISCONNECTED
    - BATTERY
    - DEVICE_INFO

log_level: INFO
```

### Finding Your USB Device

```bash
# List USB serial devices
ls /dev/cu.usbmodem*

# Example: /dev/cu.usbmodem9070699BDEBC1
```

## Running as a Service

```bash
# Install LaunchDaemon
sudo cp plists/launchd.meshcore-mqtt.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/launchd.meshcore-mqtt.plist

# Edit the plist to set your config path and USB device
sudo nano /Library/LaunchDaemons/launchd.meshcore-mqtt.plist

# Load
sudo launchctl load /Library/LaunchDaemons/launchd.meshcore-mqtt.plist
```

## Verify

```bash
# Check service
sudo launchctl list | grep meshcore-mqtt

# Watch logs
tail -f ~/ots/logs/meshcore-mqtt.log

# Subscribe to all companion MQTT topics
mosquitto_sub -h localhost -t "meshcore/#" -v
```

## MQTT Output Topics

| Topic | Event | Description |
|-------|-------|-------------|
| `meshcore/message/channel/{idx}` | CHANNEL_MSG_RECV | Broadcast channel message |
| `meshcore/message/direct/{pubkey}` | CONTACT_MSG_RECV | Direct message |
| `meshcore/status` | CONNECTED/DISCONNECTED | Companion connection status (retained) |
| `meshcore/events/connection` | CONNECTED/DISCONNECTED | Connection events |
| `meshcore/battery` | BATTERY | Companion battery level |
| `meshcore/device_info` | DEVICE_INFO | Device model, firmware, etc. |
| `meshcore/advertisement` | ADVERTISEMENT | Node adverts received |
| `meshcore/traceroute/{tag}` | TRACE_DATA | Network trace results |

## Message Payload Examples

### Channel Message
```json
{
  "type": "CHAN",
  "channel_idx": 0,
  "text": "Hello everyone!",
  "sender_timestamp": 1712234567,
  "txt_type": 0,
  "path_len": 2,
  "SNR": 12.5,
  "RSSI": -41
}
```

### Direct Message
```json
{
  "type": "PRIV",
  "pubkey_prefix": "a1b2c3d4e5f6",
  "text": "Hey, are you on freq?",
  "sender_timestamp": 1712234568,
  "txt_type": 0,
  "path_len": 1
}
```

## Companion Firmware Setup

The companion device must be on the same radio configuration as your mesh network:

```
Frequency: 910.525024 MHz (US default)
Bandwidth: 62.5 kHz
Spreading Factor: 7
Coding Rate: 5
```

To reflash a companion from EU to US settings, use the MeshCore web flasher or CLI tools.

## Sending Messages via MQTT

The companion bridge also supports **sending** messages to the mesh via MQTT commands:

```bash
# Send a channel message
mosquitto_pub -h localhost \
  -t "meshcore/command/send_chan_msg" \
  -m '{"channel": 0, "message": "Hello from ATAK!"}'

# Send a direct message
mosquitto_pub -h localhost \
  -t "meshcore/command/send_msg" \
  -m '{"destination": "a1b2c3d4e5f6", "message": "Direct from server"}'
```

## Troubleshooting

### No messages received
- Verify companion is on the correct frequency/radio settings
- Ensure the companion is in sender's contact list (for direct messages)
- Check that auto-fetch is running: look for `Started auto message fetching` in logs
- Test with explicit broadcast channel messages while bridge is running

### USB device not found
- Check `ls /dev/cu.usbmodem*`
- Try unplugging and re-plugging the device
- Some USB hubs cause issues — connect directly to the Mac

### Connection drops
- The bridge auto-reconnects with exponential backoff
- Serial connections can be disrupted by Mac sleep — consider `caffeinate` or energy settings
