# ATAK / iTAK Client Setup

## Network Requirements

Your ATAK device needs to reach the Mac running OTS. Options:

1. **Same LAN**: Both on the same Wi-Fi network
2. **Tailscale** (recommended): Install Tailscale on both devices for remote/roaming access
3. **Port forwarding**: Not recommended for security reasons

## ATAK (Android)

### Add Server Connection

1. Open ATAK → **Settings** → **Network Preferences** → **TAK Servers**
2. Tap **+** to add a new server
3. Configure:
   - **Description**: BigMacATAK (or whatever you like)
   - **Address**: `<your-mac-ip>` (LAN IP or Tailscale IP)
   - **Port**: `8088`
   - **Protocol**: `TCP`
   - **SSL/TLS**: Off (for plain TCP)
4. Tap **OK** → connection indicator should turn **green**

### Verify Connection

- You should see your EUD appear in the OTS web UI
- CoT events from other connected clients and from MeshCore will appear on your map
- Check ATAK chat for MeshCore messages (if companion bridge is running)

### Data Packages

1. **Automatic**: If packages are pushed to OTS, they auto-sync to connected clients
2. **Manual**: Settings → Tool Preferences → Data Package Mgmt → Download from server

## iTAK (iOS)

> ⚠️ iTAK may have issues with plain TCP connections. If you get auth errors, try SSL (port 8089) with certificate auth.

### Add Server

1. Open iTAK → **Settings** → **Network** → **Connect to Server**
2. Enter server address and port

### Certificate Auth (if needed)

```bash
# Generate client certificate via OTS
curl -u administrator:password \
  "http://localhost:8081/api/certificate?username=itak_user" \
  -o itak_user.p12
```

Transfer the `.p12` file to your iOS device and import it in iTAK.

## Troubleshooting

### ATAK shows green but no CoT
- Verify EUD handler is running: `ps aux | grep eud_handler`
- Check RabbitMQ has consumers: `rabbitmqctl list_consumers`
- Verify cot_parser is running

### Connection refused
- Check firewall: `sudo pfctl -sr`
- Verify the port is listening: `lsof -i :8088`
- If using Tailscale, ensure both devices are on the same tailnet

### Markers don't appear
- Check Overlay Manager — layers may be toggled off
- Verify data packages uploaded: `curl -u admin:password http://localhost:8081/api/data_packages`

### MeshCore chat not showing
- Verify meshcore-bridge is running and connected to MQTT + RabbitMQ
- Check bridge log: `tail -f ~/ots/logs/meshcore-bridge.log`
- Verify MQTT messages are flowing: `mosquitto_sub -h localhost -t "meshcore/#" -v`
