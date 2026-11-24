import json
import os
import sys
import threading

import paho.mqtt.client as mqtt


RAW_TOPIC = "ais/aivdm/raw"
POSITION_TOPIC = "ais/aivdo/position"
DECODED_TOPIC = "ais/aivdm/decoded"


def add_mqtt_arguments(parser):
    parser.add_argument(
        "--mqtt-host",
        default=os.getenv("MQTT_HOST"),
        help="Alamat broker MQTT (atau env MQTT_HOST)"
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=os.getenv("MQTT_PORT", "1883"),
        help="Port broker MQTT (default 1883)"
    )
    parser.add_argument(
        "--mqtt-tls",
        action="store_true",
        default=os.getenv("MQTT_TLS", "").lower() in ("1", "true", "yes"),
        help="Gunakan TLS untuk koneksi MQTT (atau env MQTT_TLS=1)"
    )


class MqttPublisher:
    def __init__(self, client=None):
        self.client = client
        self.enabled = client is not None

    def _publish(self, topic, payload):
        if not self.enabled or not self.client.is_connected():
            return

        # Live feed: QoS 0 avoids replaying an in-flight message after reconnect.
        result = self.client.publish(topic, payload, qos=0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"MQTT publish error ({topic}): {mqtt.error_string(result.rc)}",
                  file=sys.stderr)

    def publish_raw(self, sentence):
        self._publish(RAW_TOPIC, sentence)

    def publish_position(self, received_at, lat, lon):
        self._publish(POSITION_TOPIC, json.dumps({
            "received_at": received_at,
            "lat": lat,
            "lon": lon,
        }))

    def publish_decoded(self, payload):
        self._publish(DECODED_TOPIC, payload)

    def close(self):
        if self.enabled:
            self.client.disconnect()
            self.client.loop_stop()


def create_publisher(args):
    if not args.mqtt_host:
        return MqttPublisher()

    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    if not username or not password:
        raise SystemExit("MQTT_USERNAME dan MQTT_PASSWORD wajib diisi saat MQTT aktif")
    if not 1 <= args.mqtt_port <= 65535:
        raise SystemExit("MQTT_PORT harus antara 1 dan 65535")

    ready = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    if args.mqtt_tls:
        client.tls_set(ca_certs=os.getenv("MQTT_CA_CERTS") or None)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT connection rejected: {reason_code}", file=sys.stderr)
        ready.set()

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT disconnected: {reason_code}", file=sys.stderr)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    except OSError as e:
        raise SystemExit(f"MQTT connection error: {e}") from e

    client.loop_start()
    if not ready.wait(timeout=10) or not client.is_connected():
        client.disconnect()
        client.loop_stop()
        raise SystemExit("MQTT connection failed or timed out")

    print(f"MQTT     : {args.mqtt_host}:{args.mqtt_port}")
    return MqttPublisher(client)
