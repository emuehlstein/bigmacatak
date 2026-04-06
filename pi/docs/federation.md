# TAK Server Federation

> **Status:** 🗺️ Roadmap — Not yet implemented

This document outlines the plan for federating multiple BigMacATAK Pi instances so they share situational awareness data bidirectionally.

## Overview

TAK Server federation allows multiple OTS instances to share CoT (Cursor on Target) data over SSL connections. Each server maintains its own database and client connections, but positions, tracks, and chat messages propagate across the federation.

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   Site Alpha    │◄───────►│   Site Bravo    │◄───────►│   Site Charlie  │
│   (Pi + OTS)    │  Fed/SSL│   (Pi + OTS)    │  Fed/SSL│   (Pi + OTS)    │
│                 │         │                 │         │                 │
│  ┌───────────┐  │         │  ┌───────────┐  │         │  ┌───────────┐  │
│  │ Local EUDs│  │         │  │ Local EUDs│  │         │  │ Local EUDs│  │
│  │ ADS-B     │  │         │  │ MeshCore  │  │         │  │ ADS-B     │  │
│  │ MeshCore  │  │         │  │           │  │         │  │           │  │
│  └───────────┘  │         │  └───────────┘  │         │  └───────────┘  │
└─────────────────┘         └─────────────────┘         └─────────────────┘
```

## Benefits

- **Autonomous sites**: Each Pi works independently if federation links fail
- **Shared SA**: All ATAK clients see the same picture regardless of which server they connect to
- **Distributed sensing**: ADS-B and mesh data from each site propagates to all
- **Redundancy**: No single point of failure for the network

## Requirements

- Each Pi must be reachable by the others (LAN, Tailscale, VPN, or public IP)
- SSL certificates for federation (OTS CA or external PKI)
- Port 9000/tcp open between servers (configurable)
- Clock sync (NTP) on all Pis

## Planned Configuration

### OTS Config (`~/ots/config.yml`)

```yaml
# Federation settings
OTS_FEDERATION_ENABLED: true
OTS_FEDERATION_PORT: 9000

# This server's identity for federation
OTS_FEDERATION_ID: "site-alpha"

# Peer servers to federate with
OTS_FEDERATION_PEERS:
  - id: "site-bravo"
    host: "192.168.1.101"  # Or Tailscale IP, or hostname
    port: 9000
  - id: "site-charlie"
    host: "100.64.0.15"    # Tailscale example
    port: 9000

# What to share
OTS_FEDERATION_SHARE_CONTACTS: true
OTS_FEDERATION_SHARE_CHAT: true
OTS_FEDERATION_SHARE_TRACKS: true      # ADS-B, mesh nodes
OTS_FEDERATION_SHARE_MISSIONS: false   # Keep missions local

# Certificate auth
OTS_FEDERATION_CA_CERT: "/home/pi/ots/ca/ca.pem"
OTS_FEDERATION_CERT: "/home/pi/ots/ca/certs/federation/federation.pem"
OTS_FEDERATION_KEY: "/home/pi/ots/ca/certs/federation/federation.key"
```

### Firewall Rules

```bash
# Allow federation port from trusted peers
sudo ufw allow from 192.168.1.0/24 to any port 9000 proto tcp
sudo ufw allow from 100.64.0.0/10 to any port 9000 proto tcp  # Tailscale
```

### nginx Stream Proxy (Optional)

If federation needs SSL termination at nginx:

```nginx
# /etc/nginx/streams/federation
upstream ots_federation {
    server 127.0.0.1:9000;
}

server {
    listen 9001 ssl;
    proxy_pass ots_federation;
    
    ssl_certificate     /home/pi/ots/ca/certs/opentakserver/opentakserver.pem;
    ssl_certificate_key /home/pi/ots/ca/certs/opentakserver/opentakserver.nopass.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
}
```

## Certificate Setup

Federation requires mutual TLS. Options:

### Option A: Shared CA (Recommended for small networks)

1. Generate federation certs on one Pi
2. Copy CA + certs to all peers
3. Each peer trusts the shared CA

```bash
# On the "primary" Pi, generate federation cert
cd ~/ots/ca
openssl req -new -nodes -keyout certs/federation/federation.key \
    -out certs/federation/federation.csr \
    -subj "/CN=federation-$(hostname)"

openssl x509 -req -in certs/federation/federation.csr \
    -CA ca.pem -CAkey ca-do-not-share.key -CAcreateserial \
    -out certs/federation/federation.pem -days 365

# Copy to peers
scp ca.pem certs/federation/* pi@site-bravo:~/ots/ca/certs/federation/
```

### Option B: Cross-signed CAs

Each site maintains its own CA but cross-signs with peers. More complex, better for larger networks.

### Option C: External PKI

Use Let's Encrypt or organizational PKI. Best for internet-facing federation.

## Implementation Tasks

- [ ] Verify OTS 1.7.10 federation support and config schema
- [ ] Create federation certificate generation script
- [ ] Add federation config template to `config/ots-config.yml`
- [ ] Add federation systemd service (if separate process needed)
- [ ] Write peer discovery/health check script
- [ ] Test CoT propagation latency
- [ ] Test failure modes (peer offline, cert expiry)
- [ ] Document operational procedures (adding/removing peers)

## Testing Plan

1. **Lab setup**: Two Pis on same LAN
2. **Basic federation**: Verify CoT flows both directions
3. **Selective sharing**: Test share/filter settings
4. **Failure recovery**: Kill one peer, verify reconnection
5. **Scale test**: Add third peer, verify mesh topology
6. **WAN test**: Federation over Tailscale

## Operational Considerations

### Monitoring

```bash
# Check federation status
curl -s http://localhost:8081/api/federation/status | jq

# View federation logs
journalctl -u opentakserver -f | grep -i federation
```

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Connection refused | Firewall, wrong port | Check `ufw status`, verify port |
| SSL handshake failed | Cert mismatch | Verify CA trust, cert validity |
| No CoT propagation | Filter settings | Check `SHARE_*` config flags |
| Duplicate tracks | Loop in topology | Use unique `FEDERATION_ID` |

## References

- [TAK Server Federation Protocol](https://wiki.tak.gov/display/TPS/TAK+Server+Federation)
- [OTS Federation Docs](https://docs.opentakserver.io/configuration/federation.html)
- [Mutual TLS Setup](https://docs.opentakserver.io/configuration/certificates.html)

---

*This is a roadmap document. Implementation will be tracked in GitHub issues.*
