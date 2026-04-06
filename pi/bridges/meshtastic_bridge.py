#!/usr/bin/env python3
"""
meshtastic_bridge.py — Meshtastic Serial → OTS RabbitMQ CoT bridge

Connects to a Meshtastic node via USB serial, receives all mesh packets,
and converts them to CoT XML for delivery to ATAK via OpenTAK Server's
RabbitMQ exchanges.

No MQTT or WiFi config needed on the Meshtastic node — pure serial.

Usage:
    python3 meshtastic_bridge.py [--port /dev/cu.usbmodemXXXX] [--config ~/ots/config.yml]
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from xml.etree.ElementTree import Element, SubElement, tostring

import meshtastic
import meshtastic.serial_interface
import pika
import yaml
from pubsub import pub

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("meshtastic-bridge")

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = os.path.expanduser("~/ots/config.yml")
DEFAULT_SERIAL_PORT = "/dev/ttyACM0"

ICONSET_UID = "34ae1613-9645-4222-a9d2-e5f243dea2865"

# Meshtastic hwModel → icon mapping (white silhouettes)
HW_ICONS = {
    "TBEAM":                    f"{ICONSET_UID}/Military/radar.png",
    "TBEAM_V0P7":               f"{ICONSET_UID}/Military/radar.png",
    "STATION_G2":               f"{ICONSET_UID}/Military/radar.png",
    "HELTEC_V3":                f"{ICONSET_UID}/Military/radar.png",
    "HELTEC_WIRELESS_TRACKER":  f"{ICONSET_UID}/Transportation/Motorcycle.png",
    "HELTEC_WSL_V3":            f"{ICONSET_UID}/Military/radar.png",
    "RAK4631":                  f"{ICONSET_UID}/Military/radar.png",
    "WISMESH_TAP":              f"{ICONSET_UID}/Military/radar.png",
    "TLORA_V2_1_1P6":           f"{ICONSET_UID}/Military/radar.png",
    "NANO_G2_ULTRA":            f"{ICONSET_UID}/Military/radar.png",
}
DEFAULT_ICON = f"{ICONSET_UID}/Military/soldier.png"


def load_config(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning(f"Config not found at {path}, using defaults")
        return {}


# ---------------------------------------------------------------------------
#  RabbitMQ publisher (same as meshcore_bridge)
# ---------------------------------------------------------------------------

class RabbitPublisher:
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.connection = None
        self.channel = None

    def connect(self):
        creds = pika.PlainCredentials(self.user, self.password)
        params = pika.ConnectionParameters(
            host=self.host, credentials=creds,
            heartbeat=300, blocked_connection_timeout=60,
        )
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        log.info(f"RabbitMQ connected to {self.host}")

    def publish(self, uid, cot_xml, group="Meshtastic"):
        if not self.channel or self.channel.is_closed:
            self.connect()
        body = json.dumps({"uid": uid, "cot": cot_xml.decode("utf-8")})
        props = pika.BasicProperties(expiration="86400000")
        self.channel.basic_publish(exchange="firehose", routing_key="", body=body, properties=props)
        self.channel.basic_publish(exchange="groups", routing_key=f"{group}.OUT", body=body, properties=props)
        self.channel.basic_publish(exchange="groups", routing_key="__ANON__.OUT", body=body, properties=props)

    def close(self):
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  CoT builders
# ---------------------------------------------------------------------------

def make_position_cot(uid, callsign, lat, lon, alt, battery, speed, course,
                      hw_model, snr, stale_hours=1, group="Meshtastic"):
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now + datetime.timedelta(hours=stale_hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    event = Element("event", {
        "version": "2.0", "uid": uid, "type": "a-f-G-U-C",
        "how": "m-g", "time": now.strftime(fmt),
        "start": now.strftime(fmt), "stale": stale.strftime(fmt),
    })

    SubElement(event, "point", {
        "lat": str(lat), "lon": str(lon),
        "hae": str(alt) if alt else "9999999.0",
        "ce": "50.0", "le": "50.0",
    })

    detail = SubElement(event, "detail")
    SubElement(detail, "takv", {
        "device": hw_model or "Meshtastic",
        "version": "", "platform": "Meshtastic", "os": "Meshtastic",
    })
    SubElement(detail, "contact", {"callsign": callsign, "endpoint": "Serial"})
    SubElement(detail, "uid", {"Droid": callsign})

    if speed or course:
        track_attrs = {}
        if speed is not None:
            track_attrs["speed"] = str(speed)
        if course is not None:
            track_attrs["course"] = str(course)
        SubElement(detail, "track", track_attrs)

    # Meshtastic metadata
    mesh_attrs = {"hw_model": hw_model or "Unknown"}
    if snr is not None:
        mesh_attrs["snr"] = str(snr)
    SubElement(detail, "meshtastic", mesh_attrs)

    # Icon
    icon_path = HW_ICONS.get(hw_model, DEFAULT_ICON)
    SubElement(detail, "usericon", {"iconsetpath": icon_path})

    batt_str = str(battery) if battery else "0"
    SubElement(detail, "status", {"battery": batt_str})
    SubElement(detail, "__group", {"name": group, "role": "Team Member"})

    return tostring(event)


def make_geochat_cot(sender_uid, sender_callsign, text, group="Meshtastic"):
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now + datetime.timedelta(minutes=10)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    msg_id = uuid.uuid4().hex[:8]
    chat_uid = f"GeoChat.{sender_uid}.All Chat Rooms.{msg_id}"

    event = Element("event", {
        "version": "2.0", "uid": chat_uid, "type": "b-t-f",
        "how": "h-g-i-g-o", "time": now.strftime(fmt),
        "start": now.strftime(fmt), "stale": stale.strftime(fmt),
    })

    SubElement(event, "point", {
        "lat": "0.0", "lon": "0.0", "hae": "9999999.0",
        "ce": "9999999.0", "le": "9999999.0",
    })

    detail = SubElement(event, "detail")
    SubElement(detail, "__chat", {
        "parent": "RootContactGroup", "groupOwner": "false",
        "chatroom": "Meshtastic", "id": "Meshtastic",
        "senderCallsign": sender_callsign,
    })
    SubElement(detail, "link", {
        "uid": sender_uid, "type": "a-f-G-U-C", "relation": "p-p",
    })
    remarks = SubElement(detail, "remarks", {
        "source": sender_uid, "to": "Meshtastic", "time": now.strftime(fmt),
    })
    remarks.text = text
    SubElement(detail, "__group", {"name": group, "role": "Team Member"})

    return tostring(event)


def make_telemetry_cot(uid, callsign, lat, lon, battery, voltage, 
                       channel_util, air_util, uptime, hw_model,
                       stale_hours=1, group="Meshtastic"):
    """Position CoT with enriched telemetry in remarks."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now + datetime.timedelta(hours=stale_hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    event = Element("event", {
        "version": "2.0", "uid": uid, "type": "a-f-G-U-C",
        "how": "m-g", "time": now.strftime(fmt),
        "start": now.strftime(fmt), "stale": stale.strftime(fmt),
    })

    if lat and lon:
        SubElement(event, "point", {
            "lat": str(lat), "lon": str(lon), "hae": "0.0",
            "ce": "50.0", "le": "50.0",
        })
    else:
        SubElement(event, "point", {
            "lat": "0.0", "lon": "0.0", "hae": "9999999.0",
            "ce": "9999999.0", "le": "9999999.0",
        })

    detail = SubElement(event, "detail")
    SubElement(detail, "contact", {"callsign": callsign, "endpoint": "Serial"})
    SubElement(detail, "uid", {"Droid": callsign})

    # Telemetry in remarks
    parts = []
    if battery is not None:
        parts.append(f"Batt:{battery}%")
    if voltage is not None:
        parts.append(f"V:{voltage:.2f}")
    if channel_util is not None:
        parts.append(f"ChUtil:{channel_util:.1f}%")
    if air_util is not None:
        parts.append(f"AirTx:{air_util:.1f}%")
    if uptime is not None:
        hours = uptime // 3600
        mins = (uptime % 3600) // 60
        parts.append(f"Up:{hours}h{mins}m")
    if parts:
        SubElement(detail, "remarks").text = " ".join(parts)

    mesh_attrs = {"hw_model": hw_model or "Unknown"}
    if voltage is not None:
        mesh_attrs["voltage"] = str(voltage)
    if channel_util is not None:
        mesh_attrs["channel_util"] = str(channel_util)
    SubElement(detail, "meshtastic", mesh_attrs)

    icon_path = HW_ICONS.get(hw_model, DEFAULT_ICON)
    SubElement(detail, "usericon", {"iconsetpath": icon_path})

    SubElement(detail, "status", {"battery": str(battery or 0)})
    SubElement(detail, "__group", {"name": group, "role": "Team Member"})

    return tostring(event)


# ---------------------------------------------------------------------------
#  Bridge
# ---------------------------------------------------------------------------

class MeshtasticBridge:
    def __init__(self, serial_port, cfg):
        self.serial_port = serial_port
        self.cfg = cfg
        self.group = cfg.get("OTS_MESHTASTIC_GROUP", "Meshtastic")
        self.stale_hours = 1
        self.iface = None
        self.node_cache = {}  # node_id → {callsign, hw_model, lat, lon, ...}

        self.rabbit = RabbitPublisher(
            host=cfg.get("OTS_RABBITMQ_SERVER_ADDRESS", "127.0.0.1"),
            user=cfg.get("OTS_RABBITMQ_USERNAME", "guest"),
            password=cfg.get("OTS_RABBITMQ_PASSWORD", "guest"),
        )

    def start(self):
        # Connect to RabbitMQ
        while True:
            try:
                self.rabbit.connect()
                break
            except Exception as e:
                log.error(f"RabbitMQ connect failed: {e} — retrying in 5s")
                time.sleep(5)

        # Subscribe to meshtastic pubsub events
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_connection, "meshtastic.connection.established")
        pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")

        # Connect to serial
        log.info(f"Connecting to Meshtastic on {self.serial_port}...")
        self.iface = meshtastic.serial_interface.SerialInterface(self.serial_port)

        # Publish initial node list
        self._publish_known_nodes()

        # Block forever
        log.info("Meshtastic bridge running. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    def stop(self):
        if self.iface:
            self.iface.close()
        self.rabbit.close()
        log.info("Bridge stopped")

    def _on_connection(self, interface, topic=pub.AUTO_TOPIC):
        log.info(f"Connected to Meshtastic on {interface.devPath}")
        log.info(f"Nodes in mesh: {len(interface.nodes)}")

    def _on_disconnect(self, interface, topic=pub.AUTO_TOPIC):
        log.warning("Meshtastic serial disconnected!")

    def _publish_known_nodes(self):
        """Publish CoT for all nodes the device already knows about."""
        if not self.iface:
            return
        count = 0
        for node_id, node in self.iface.nodes.items():
            user = node.get("user", {})
            pos = node.get("position", {})
            dm = node.get("deviceMetrics", {})

            callsign = user.get("longName", node_id)
            hw_model = user.get("hwModel", "UNSET")
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            alt = pos.get("altitude")
            battery = dm.get("batteryLevel")

            if hw_model == "UNSET" and not lat:
                continue  # Skip unknown nodes with no position

            self.node_cache[node_id] = {
                "callsign": callsign,
                "hw_model": hw_model,
                "lat": lat, "lon": lon,
            }

            uid = f"Meshtastic-{node_id.replace('!', '')}"

            if lat and lon:
                cot = make_position_cot(
                    uid, callsign, lat, lon, alt, battery,
                    None, None, hw_model, None,
                    self.stale_hours, self.group,
                )
            else:
                cot = make_telemetry_cot(
                    uid, callsign, None, None, battery, None,
                    dm.get("channelUtilization"), dm.get("airUtilTx"),
                    dm.get("uptimeSeconds"), hw_model,
                    self.stale_hours, self.group,
                )

            try:
                self.rabbit.publish(uid, cot, self.group)
                count += 1
            except pika.exceptions.AMQPConnectionError:
                self._reconnect_rabbit(uid, cot)

        log.info(f"Published {count} known nodes to OTS")

    def _on_receive(self, packet, interface):
        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum", "")
            from_id = packet.get("fromId") or packet.get("from", "")
            if not from_id:
                # Some packets (e.g., local node) have no fromId
                num = packet.get("from", 0)
                if num:
                    from_id = f"!{num:08x}"
                else:
                    return
            snr = packet.get("snr")

            if portnum == "POSITION_APP":
                self._handle_position(from_id, decoded, snr)
            elif portnum == "TEXT_MESSAGE_APP":
                self._handle_text(from_id, decoded, snr)
            elif portnum == "NODEINFO_APP":
                self._handle_nodeinfo(from_id, decoded)
            elif portnum == "TELEMETRY_APP":
                self._handle_telemetry(from_id, decoded, snr)
            else:
                log.debug(f"Ignoring {portnum} from {from_id}")

        except Exception as e:
            log.error(f"Packet handler error: {e}", exc_info=True)

    def _handle_position(self, from_id, decoded, snr):
        pos = decoded.get("position", {})
        lat = pos.get("latitude") or pos.get("latitudeI", 0) / 1e7
        lon = pos.get("longitude") or pos.get("longitudeI", 0) / 1e7
        alt = pos.get("altitude")
        speed = pos.get("groundSpeed")
        course = pos.get("groundTrack")

        if not lat or not lon:
            return

        cached = self.node_cache.get(from_id, {})
        callsign = cached.get("callsign", from_id)
        hw_model = cached.get("hw_model", "UNSET")

        # Update cache
        self.node_cache.setdefault(from_id, {}).update({
            "lat": lat, "lon": lon, "callsign": callsign,
        })

        uid = f"Meshtastic-{from_id.replace('!', '')}"
        cot = make_position_cot(
            uid, callsign, lat, lon, alt, None,
            speed, course, hw_model, snr,
            self.stale_hours, self.group,
        )

        try:
            self.rabbit.publish(uid, cot, self.group)
        except pika.exceptions.AMQPConnectionError:
            self._reconnect_rabbit(uid, cot)

        log.info(f"Position: {callsign} ({lat:.6f}, {lon:.6f}) SNR:{snr}")

    def _handle_text(self, from_id, decoded, snr):
        text = decoded.get("text", "")
        if not text:
            return

        cached = self.node_cache.get(from_id, {})
        callsign = cached.get("callsign", from_id)

        uid = f"Meshtastic-{from_id.replace('!', '')}"

        # Add SNR to display
        display_text = f"{text} [SNR:{snr}]" if snr else text

        cot = make_geochat_cot(uid, callsign, display_text, self.group)

        try:
            self.rabbit.publish(uid, cot, self.group)
        except pika.exceptions.AMQPConnectionError:
            self._reconnect_rabbit(uid, cot)

        log.info(f"Text: [{callsign}] {text}")

    def _handle_nodeinfo(self, from_id, decoded):
        user = decoded.get("user", {})
        if not user:
            return

        callsign = user.get("longName", from_id)
        hw_model = user.get("hwModel", "UNSET")

        self.node_cache.setdefault(from_id, {}).update({
            "callsign": callsign, "hw_model": hw_model,
        })

        log.info(f"NodeInfo: {callsign} ({from_id}) hw={hw_model}")

    def _handle_telemetry(self, from_id, decoded, snr):
        telemetry = decoded.get("telemetry", {})
        dm = telemetry.get("deviceMetrics", {})
        if not dm:
            return

        cached = self.node_cache.get(from_id, {})
        callsign = cached.get("callsign", from_id)
        hw_model = cached.get("hw_model", "UNSET")
        lat = cached.get("lat")
        lon = cached.get("lon")

        battery = dm.get("batteryLevel")
        voltage = dm.get("voltage")
        ch_util = dm.get("channelUtilization")
        air_util = dm.get("airUtilTx")
        uptime = dm.get("uptimeSeconds")

        uid = f"Meshtastic-{from_id.replace('!', '')}"
        cot = make_telemetry_cot(
            uid, callsign, lat, lon, battery, voltage,
            ch_util, air_util, uptime, hw_model,
            self.stale_hours, self.group,
        )

        try:
            self.rabbit.publish(uid, cot, self.group)
        except pika.exceptions.AMQPConnectionError:
            self._reconnect_rabbit(uid, cot)

        log.info(f"Telemetry: {callsign} batt={battery}% v={voltage} chUtil={ch_util}")

    def _reconnect_rabbit(self, uid, cot):
        log.warning("RabbitMQ connection lost, reconnecting...")
        try:
            self.rabbit.connect()
            self.rabbit.publish(uid, cot, self.group)
        except Exception as e:
            log.error(f"RabbitMQ reconnect failed: {e}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Meshtastic Serial → OTS CoT bridge")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="OTS config.yml path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    bridge = MeshtasticBridge(args.port, cfg)

    def shutdown(signum, frame):
        log.info(f"Caught signal {signum}, shutting down...")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("Meshtastic bridge starting...")
    bridge.start()


if __name__ == "__main__":
    main()
