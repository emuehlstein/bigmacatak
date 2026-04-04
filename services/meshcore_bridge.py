#!/usr/bin/env python3
"""
meshcore_bridge.py — Standalone MeshCore MQTT → OTS RabbitMQ CoT bridge

Subscribes to MeshCore MQTT topics on local mosquitto, converts node
heartbeats into CoT XML and mesh messages into GeoChat CoT, and publishes
to OTS's RabbitMQ exchanges. No Flask/OTS dependency — runs as its own
LaunchDaemon.

Usage:
    python3 meshcore_bridge.py [--config /path/to/config.yml]

Config is read from OTS's config.yml (default: ~/ots/config.yml).
Relevant keys:
    OTS_MESHCORE_MQTT_HOST      (default: localhost)
    OTS_MESHCORE_MQTT_PORT      (default: 1883)
    OTS_MESHCORE_MQTT_USERNAME  (default: "")
    OTS_MESHCORE_MQTT_PASSWORD  (default: "")
    OTS_MESHCORE_TOPIC          (default: meshcore)
    OTS_MESHCORE_GROUP          (default: MeshCore)
    OTS_MESHCORE_STALE_HOURS    (default: 1)
    OTS_RABBITMQ_SERVER_ADDRESS (default: 127.0.0.1)
    OTS_RABBITMQ_USERNAME       (default: guest)
    OTS_RABBITMQ_PASSWORD       (default: guest)
"""

import argparse
import datetime
import json
import logging
import os
import signal
import sys
import time
from xml.etree.ElementTree import Element, SubElement, tostring

import hashlib
import uuid

import paho.mqtt.client as mqtt
import pika
import yaml

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("meshcore-bridge")

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = os.path.expanduser("~/ots/config.yml")


def load_config(path: str) -> dict:
    """Load OTS config.yml and return as flat dict."""
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning(f"Config not found at {path}, using defaults")
        return {}


# ---------------------------------------------------------------------------
#  CoT builder
# ---------------------------------------------------------------------------

def make_status_cot(
    uid: str,
    callsign: str,
    model: str,
    firmware: str,
    freq_mhz: str,
    stats: dict,
    status: str,
    stale_hours: int,
    group: str,
) -> bytes:
    """Build CoT XML for a MeshCore node status heartbeat."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now + datetime.timedelta(hours=stale_hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    event = Element("event", {
        "version": "2.0",
        "uid": uid,
        "type": "a-f-G-U-C",
        "how": "m-g",
        "time": now.strftime(fmt),
        "start": now.strftime(fmt),
        "stale": stale.strftime(fmt),
    })

    # No GPS — unknown position
    SubElement(event, "point", {
        "lat": "0.0", "lon": "0.0", "hae": "9999999.0",
        "ce": "9999999.0", "le": "9999999.0",
    })

    detail = SubElement(event, "detail")
    SubElement(detail, "takv", {
        "device": model,
        "version": firmware,
        "platform": "MeshCore",
        "os": "MeshCore",
    })
    SubElement(detail, "contact", {"callsign": callsign, "endpoint": "MQTT"})
    SubElement(detail, "uid", {"Droid": callsign})

    # MeshCore telemetry
    SubElement(detail, "meshcore", {
        "status": status,
        "freq_mhz": freq_mhz,
        "noise_floor_dbm": str(stats.get("noise_floor", "")),
        "uptime_secs": str(stats.get("uptime_secs", "")),
        "battery_mv": str(stats.get("battery_mv", "0")),
        "tx_air_secs": str(stats.get("tx_air_secs", "")),
        "rx_air_secs": str(stats.get("rx_air_secs", "")),
        "queue_len": str(stats.get("queue_len", "")),
    })
    SubElement(detail, "status", {"battery": "0"})
    SubElement(detail, "__group", {"name": group, "role": "Team Member"})

    return tostring(event)


def make_geochat_cot(
    sender_uid: str,
    sender_callsign: str,
    message_text: str,
    chat_group: str,
    group: str,
) -> bytes:
    """Build GeoChat CoT XML for a MeshCore mesh message.

    GeoChat CoT type: b-t-f (bits - text - friendly)
    This is what ATAK uses for its built-in chat system.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now + datetime.timedelta(minutes=10)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    # GeoChat UID format: GeoChat.{sender}.{target}.{unique}
    msg_id = uuid.uuid4().hex[:8]
    chat_uid = f"GeoChat.{sender_uid}.All Chat Rooms.{msg_id}"

    event = Element("event", {
        "version": "2.0",
        "uid": chat_uid,
        "type": "b-t-f",
        "how": "h-g-i-g-o",  # human-generated
        "time": now.strftime(fmt),
        "start": now.strftime(fmt),
        "stale": stale.strftime(fmt),
    })

    # GeoChat doesn't need a real position
    SubElement(event, "point", {
        "lat": "0.0", "lon": "0.0", "hae": "9999999.0",
        "ce": "9999999.0", "le": "9999999.0",
    })

    detail = SubElement(event, "detail")

    # __chat element — defines the chat room and sender
    SubElement(detail, "__chat", {
        "parent": "RootContactGroup",
        "groupOwner": "false",
        "chatroom": chat_group,
        "id": chat_group,
        "senderCallsign": sender_callsign,
    })

    # link to sender
    SubElement(detail, "link", {
        "uid": sender_uid,
        "type": "a-f-G-U-C",
        "relation": "p-p",
    })

    # remarks — the actual message text
    remarks = SubElement(detail, "remarks", {
        "source": sender_uid,
        "to": chat_group,
        "time": now.strftime(fmt),
    })
    remarks.text = message_text

    SubElement(detail, "__group", {"name": group, "role": "Team Member"})

    return tostring(event)


# ---------------------------------------------------------------------------
#  RabbitMQ publisher
# ---------------------------------------------------------------------------

class RabbitPublisher:
    """Manages a pika BlockingConnection to OTS's RabbitMQ."""

    def __init__(self, host: str, user: str, password: str):
        self.host = host
        self.user = user
        self.password = password
        self.connection = None
        self.channel = None

    def connect(self):
        creds = pika.PlainCredentials(self.user, self.password)
        params = pika.ConnectionParameters(
            host=self.host,
            credentials=creds,
            heartbeat=300,
            blocked_connection_timeout=60,
        )
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        log.info(f"RabbitMQ connected to {self.host}")

    def publish(self, uid: str, cot_xml: bytes, group: str, ttl: str = "86400000"):
        """Publish CoT to OTS firehose + group exchanges."""
        if not self.channel or self.channel.is_closed:
            self.connect()

        body = json.dumps({"uid": uid, "cot": cot_xml.decode("utf-8")})
        props = pika.BasicProperties(expiration=ttl)

        self.channel.basic_publish(
            exchange="firehose", routing_key="", body=body, properties=props
        )
        self.channel.basic_publish(
            exchange="groups", routing_key=f"{group}.OUT",
            body=body, properties=props,
        )
        self.channel.basic_publish(
            exchange="groups", routing_key="__ANON__.OUT",
            body=body, properties=props,
        )
        log.debug(f"Published CoT for {uid}")

    def close(self):
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Bridge
# ---------------------------------------------------------------------------

class MeshCoreBridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.group = cfg.get("OTS_MESHCORE_GROUP", "MeshCore")
        self.stale_hours = int(cfg.get("OTS_MESHCORE_STALE_HOURS", 1))
        self.topic = cfg.get("OTS_MESHCORE_TOPIC", "meshcore")
        self.nodes: dict[str, dict] = {}

        self.rabbit = RabbitPublisher(
            host=cfg.get("OTS_RABBITMQ_SERVER_ADDRESS", "127.0.0.1"),
            user=cfg.get("OTS_RABBITMQ_USERNAME", "guest"),
            password=cfg.get("OTS_RABBITMQ_PASSWORD", "guest"),
        )

        self.mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="meshcore-bridge",
        )
        mqtt_user = cfg.get("OTS_MESHCORE_MQTT_USERNAME", "")
        mqtt_pw = cfg.get("OTS_MESHCORE_MQTT_PASSWORD", "")
        if mqtt_user:
            self.mqtt_client.username_pw_set(mqtt_user, mqtt_pw)

        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)

    def start(self):
        """Connect to RabbitMQ and MQTT, then loop forever."""
        # RabbitMQ first
        while True:
            try:
                self.rabbit.connect()
                break
            except Exception as e:
                log.error(f"RabbitMQ connect failed: {e} — retrying in 5s")
                time.sleep(5)

        # MQTT
        mqtt_host = self.cfg.get("OTS_MESHCORE_MQTT_HOST", "localhost")
        mqtt_port = int(self.cfg.get("OTS_MESHCORE_MQTT_PORT", 1883))
        log.info(f"Connecting MQTT to {mqtt_host}:{mqtt_port}")
        self.mqtt_client.connect(mqtt_host, mqtt_port, keepalive=60)
        self.mqtt_client.loop_forever()

    def stop(self):
        self.mqtt_client.disconnect()
        self.rabbit.close()
        log.info("Bridge stopped")

    # -- MQTT callbacks --

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0 or rc.value == 0:
            client.subscribe(f"{self.topic}/#")
            log.info(f"MQTT subscribed to {self.topic}/#")
        else:
            log.error(f"MQTT connect error rc={rc}")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        log.warning(f"MQTT disconnected rc={rc}, auto-reconnecting...")

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            parts = topic.split("/")
            raw = msg.payload.decode("utf-8")

            # Route based on topic structure
            # Firmware topics: meshcore/{region}/{node_id}/status|packets
            # Companion topics: meshcore/message/channel/{idx}
            #                   meshcore/message/direct/{pubkey}
            #                   meshcore/status  (retained: "connected"/"disconnected")
            #                   meshcore/events/connection
            #                   meshcore/battery
            #                   meshcore/device_info

            # Companion rx_log / event topics (includes RX_LOG_DATA with advert data)
            if len(parts) >= 2 and parts[-1] in ("event", "rx_log"):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return
                self._handle_generic_event(payload)
                return

            # Companion message topics
            if len(parts) >= 4 and parts[1] == "message":
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return
                msg_kind = parts[2]  # "channel" or "direct"
                msg_target = parts[3]  # channel_idx or pubkey_prefix
                self._handle_mesh_message(msg_kind, msg_target, payload)
                return

            # Firmware status/packets topics: meshcore/{region}/{node_id}/{type}
            if len(parts) >= 4:
                msg_type = parts[-1]
                node_id = parts[-2]
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return
                if msg_type == "status":
                    self._handle_status(node_id, payload)
                elif msg_type == "packets":
                    self._handle_packet(node_id, payload)
                return

        except Exception as e:
            log.error(f"Message handler error: {e}", exc_info=True)

    # -- Handlers --

    def _handle_status(self, node_id: str, payload: dict):
        origin = payload.get("origin", f"MeshCore-{node_id[:8]}")
        origin_id = payload.get("origin_id", node_id)
        model = payload.get("model", "Unknown")
        firmware = payload.get("firmware_version", "")
        status = payload.get("status", "online")
        stats = payload.get("stats", {})

        radio_parts = payload.get("radio", "").split(",")
        freq_mhz = radio_parts[0] if radio_parts else ""

        uid = f"MeshCore-{origin_id}"
        cot_xml = make_status_cot(
            uid, origin, model, firmware, freq_mhz,
            stats, status, self.stale_hours, self.group,
        )

        try:
            self.rabbit.publish(uid, cot_xml, self.group)
        except pika.exceptions.AMQPConnectionError:
            log.warning("RabbitMQ connection lost, reconnecting...")
            try:
                self.rabbit.connect()
                self.rabbit.publish(uid, cot_xml, self.group)
            except Exception as e:
                log.error(f"RabbitMQ reconnect failed: {e}")

        self.nodes[origin_id] = {
            "callsign": origin,
            "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        log.info(f"Status: {origin} ({model}) freq={freq_mhz}MHz status={status}")

    def _handle_packet(self, node_id: str, payload: dict):
        origin = payload.get("origin", node_id)
        snr = payload.get("SNR", "?")
        rssi = payload.get("RSSI", "?")
        route = payload.get("route", "?")
        pkt_type = payload.get("packet_type", "?")
        log.debug(f"Packet [{origin}] type={pkt_type} route={route} SNR={snr} RSSI={rssi}")

    # -- Companion message handler --

    def _handle_mesh_message(self, msg_kind: str, msg_target: str, payload: dict):
        """Handle a decoded mesh message from the companion bridge.

        Converts CONTACT_MSG_RECV / CHANNEL_MSG_RECV payloads into GeoChat
        CoT events so mesh messages show up in ATAK chat.

        payload shape (from meshcore lib):
          PRIV: {"type":"PRIV", "pubkey_prefix":"...", "text":"...", "sender_timestamp":..., ...}
          CHAN: {"type":"CHAN", "channel_idx":0, "text":"...", "sender_timestamp":..., ...}

        When serialized by meshcore-mqtt's _serialize_to_json, the event
        object's __dict__ is dumped — so payload may be the event dict with
        nested "payload" key, or the payload directly. We handle both.
        """
        # Unwrap nested payload if present (event object serialization)
        if "payload" in payload and isinstance(payload["payload"], dict):
            inner = payload["payload"]
        else:
            inner = payload

        text = inner.get("text", "")
        if not text:
            log.debug(f"Empty message text from {msg_kind}/{msg_target}, skipping")
            return

        msg_type = inner.get("type", msg_kind.upper())

        if msg_type == "CHAN" or msg_kind == "channel":
            channel_idx = inner.get("channel_idx", msg_target)
            sender_callsign = f"MeshCore-Ch{channel_idx}"
            sender_uid = f"MeshCore-channel-{channel_idx}"
            chat_group = f"MeshCore Channel {channel_idx}"
        elif msg_type == "PRIV" or msg_kind == "direct":
            pubkey = inner.get("pubkey_prefix", msg_target)
            # Look up callsign from node cache if available
            sender_callsign = self._resolve_callsign(pubkey)
            sender_uid = f"MeshCore-{pubkey}"
            chat_group = "MeshCore Direct"
        else:
            log.warning(f"Unknown message kind: {msg_kind}/{msg_type}")
            return

        # Add metadata to message text
        snr = inner.get("SNR")
        rssi = inner.get("RSSI")
        meta_parts = []
        if snr is not None:
            meta_parts.append(f"SNR:{snr}")
        if rssi is not None:
            meta_parts.append(f"RSSI:{rssi}")
        display_text = text
        if meta_parts:
            display_text = f"{text} [{' '.join(meta_parts)}]"

        cot_xml = make_geochat_cot(
            sender_uid=sender_uid,
            sender_callsign=sender_callsign,
            message_text=display_text,
            chat_group=chat_group,
            group=self.group,
        )

        uid = f"GeoChat-MeshCore-{hashlib.md5(f'{sender_uid}{text}{time.time()}'.encode()).hexdigest()[:8]}"

        try:
            self.rabbit.publish(uid, cot_xml, self.group)
        except pika.exceptions.AMQPConnectionError:
            log.warning("RabbitMQ connection lost, reconnecting...")
            try:
                self.rabbit.connect()
                self.rabbit.publish(uid, cot_xml, self.group)
            except Exception as e:
                log.error(f"RabbitMQ reconnect failed: {e}")

        log.info(f"GeoChat: [{sender_callsign}] → {chat_group}: {text}")

    # -- Generic event handler (RX_LOG_DATA adverts) --

    def _handle_generic_event(self, payload: dict):
        """Handle generic events from the companion bridge.

        We're primarily interested in RX_LOG_DATA events where
        payload_typename == 'ADVERT' — these contain full advert data
        including node name, GPS, and RF metadata.
        """
        # Unwrap nested structures — meshcore-mqtt serializes the Event
        # object, so payload may be wrapped
        inner = payload
        if "payload" in payload and isinstance(payload["payload"], dict):
            inner = payload["payload"]

        # Only process advert packets from RX_LOG_DATA
        payload_typename = inner.get("payload_typename", "")
        if payload_typename != "ADVERT":
            return

        adv_name = inner.get("adv_name", "")
        adv_key = inner.get("adv_key", "")
        adv_lat = inner.get("adv_lat", 0.0)
        adv_lon = inner.get("adv_lon", 0.0)
        adv_type = inner.get("adv_type", 0)
        snr = inner.get("snr", None)
        rssi = inner.get("rssi", None)

        if not adv_name or not adv_key:
            return

        # Determine node type from adv_type
        # 0=unknown, 1=repeater, 2=chat node, 3=room server
        type_names = {0: "Unknown", 1: "Repeater", 2: "Chat Node", 3: "Room Server"}
        node_type = type_names.get(adv_type, f"Type-{adv_type}")

        uid = f"MeshCore-{adv_key[:16]}"

        # Build CoT — use actual GPS if available
        has_gps = adv_lat != 0.0 or adv_lon != 0.0

        cot_xml = self._make_advert_cot(
            uid=uid,
            callsign=adv_name,
            node_type=node_type,
            lat=adv_lat,
            lon=adv_lon,
            has_gps=has_gps,
            snr=snr,
            rssi=rssi,
        )

        try:
            self.rabbit.publish(uid, cot_xml, self.group)
        except pika.exceptions.AMQPConnectionError:
            log.warning("RabbitMQ connection lost, reconnecting...")
            try:
                self.rabbit.connect()
                self.rabbit.publish(uid, cot_xml, self.group)
            except Exception as e:
                log.error(f"RabbitMQ reconnect failed: {e}")

        # Update node cache
        self.nodes[adv_key] = {
            "callsign": adv_name,
            "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        gps_str = f"({adv_lat:.6f}, {adv_lon:.6f})" if has_gps else "(no GPS)"
        rf_str = ""
        if snr is not None:
            rf_str = f" SNR:{snr}"
        if rssi is not None:
            rf_str += f" RSSI:{rssi}"
        log.info(f"Advert: {adv_name} [{node_type}] {gps_str}{rf_str}")

    def _make_advert_cot(
        self,
        uid: str,
        callsign: str,
        node_type: str,
        lat: float,
        lon: float,
        has_gps: bool,
        snr: float | None,
        rssi: float | None,
    ) -> bytes:
        """Build CoT XML for a MeshCore advert."""
        now = datetime.datetime.now(datetime.timezone.utc)
        stale = now + datetime.timedelta(hours=self.stale_hours)
        fmt = "%Y-%m-%dT%H:%M:%SZ"

        event = Element("event", {
            "version": "2.0",
            "uid": uid,
            "type": "a-f-G-U-C",
            "how": "m-g",
            "time": now.strftime(fmt),
            "start": now.strftime(fmt),
            "stale": stale.strftime(fmt),
        })

        if has_gps:
            SubElement(event, "point", {
                "lat": str(lat), "lon": str(lon), "hae": "0.0",
                "ce": "100.0", "le": "100.0",
            })
        else:
            SubElement(event, "point", {
                "lat": "0.0", "lon": "0.0", "hae": "9999999.0",
                "ce": "9999999.0", "le": "9999999.0",
            })

        detail = SubElement(event, "detail")
        SubElement(detail, "takv", {
            "device": node_type,
            "version": "",
            "platform": "MeshCore",
            "os": "MeshCore",
        })
        SubElement(detail, "contact", {"callsign": callsign, "endpoint": "RF"})
        SubElement(detail, "uid", {"Droid": callsign})

        meshcore_attrs = {
            "node_type": node_type,
        }
        if snr is not None:
            meshcore_attrs["snr"] = str(snr)
        if rssi is not None:
            meshcore_attrs["rssi"] = str(rssi)
        SubElement(detail, "meshcore", meshcore_attrs)

        SubElement(detail, "status", {"battery": "0"})
        SubElement(detail, "__group", {"name": self.group, "role": "Team Member"})

        return tostring(event)

    def _resolve_callsign(self, pubkey_prefix: str) -> str:
        """Try to resolve a pubkey prefix to a human-readable callsign.

        Checks the node cache (populated from status heartbeats) first.
        Falls back to a truncated pubkey.
        """
        # Check node cache — origin_id might match pubkey prefix
        for origin_id, info in self.nodes.items():
            if origin_id.lower().startswith(pubkey_prefix.lower()):
                return info.get("callsign", f"MC-{pubkey_prefix[:8]}")
        return f"MC-{pubkey_prefix[:8]}"


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MeshCore → OTS CoT bridge")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to OTS config.yml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    bridge = MeshCoreBridge(cfg)

    def shutdown(signum, frame):
        log.info(f"Caught signal {signum}, shutting down...")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("MeshCore bridge starting...")
    bridge.start()


if __name__ == "__main__":
    main()
