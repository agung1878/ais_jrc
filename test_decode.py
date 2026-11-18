import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pyais.encode import encode_dict

import decode
import mqtt_publisher
import serial_connection


class FakeSerial:
    def __init__(self, sentences):
        self.sentences = iter(sentences)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def readline(self):
        try:
            sentence = next(self.sentences)
        except StopIteration:
            raise KeyboardInterrupt
        if isinstance(sentence, BaseException):
            raise sentence
        return (sentence + "\r\n").encode("ascii")


class FakeMqtt:
    def __init__(self):
        self.messages = []

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos):
        self.messages.append((topic, payload, qos))
        return SimpleNamespace(rc=0)

    def disconnect(self):
        pass

    def loop_stop(self):
        pass


class DecodeMqttTests(unittest.TestCase):
    def run_reader(self, sentences, message_type="BOTH", sessions=None):
        client = FakeMqtt()
        args = SimpleNamespace(
            port="/dev/fake", baudrate=38400, retry_interval=3, type=message_type,
            mmsi=None, msg_type=None, position_only=False, bbox=None,
            raw=False
        )
        output = io.StringIO()
        with patch.object(decode, "parse_args", return_value=args), \
                patch.object(decode, "create_publisher",
                             return_value=mqtt_publisher.MqttPublisher(client)) as create_publisher, \
                patch.object(serial_connection.serial, "Serial",
                             side_effect=[FakeSerial(items) for items in sessions]
                             if sessions is not None else [FakeSerial(sentences)]), \
                patch.object(serial_connection.time, "sleep"), \
                contextlib.redirect_stdout(output):
            decode.main()
        create_publisher.assert_called_once()
        return client.messages

    def test_reconnect_resumes_mqtt_and_discards_old_multipart_fragments(self):
        multipart = encode_dict(
            {"type": 5, "mmsi": 123456789, "shipname": "TEST SHIP"},
            talker_id="AIVDM"
        )
        single = encode_dict({"type": 1, "mmsi": 123456789}, talker_id="AIVDM")
        messages = self.run_reader([], sessions=[
            [multipart[0], serial_connection.serial.SerialException("unplugged")],
            [multipart[1]] + single + multipart,
        ])
        decoded = [json.loads(payload)["message_type"] for topic, payload, _ in messages
                   if topic == mqtt_publisher.DECODED_TOPIC]
        self.assertEqual(decoded, [1, 5])
        self.assertEqual([payload for topic, payload, _ in messages
                          if topic == mqtt_publisher.RAW_TOPIC],
                         multipart + single + multipart)

    def test_raw_fragments_and_decoded_messages(self):
        vdm = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": -6.2, "lon": 106.8},
            talker_id="AIVDM"
        )
        multipart = encode_dict(
            {"type": 5, "mmsi": 123456789, "shipname": "TEST SHIP"},
            talker_id="AIVDM"
        )
        vdo = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": -6.2, "lon": 106.8},
            talker_id="AIVDO"
        )
        messages = self.run_reader(vdm + multipart + vdo, message_type="VDM")

        raw = [payload for topic, payload, qos in messages
               if topic == "ais/aivdm/raw"]
        own = [json.loads(payload) for topic, payload, qos in messages
               if topic == "ais/aivdo/position"]
        decoded = [json.loads(payload) for topic, payload, qos in messages
                   if topic == "ais/aivdm/decoded"]

        self.assertEqual(raw, vdm + multipart)
        self.assertEqual(len(own), 1)
        self.assertEqual((own[0]["lat"], own[0]["lon"]), (-6.2, 106.8))
        self.assertEqual([item["message_type"] for item in decoded], [1, 5])
        self.assertEqual({topic for topic, _, _ in messages}, {
            "ais/aivdm/raw", "ais/aivdo/position", "ais/aivdm/decoded"
        })
        self.assertTrue(all(qos == 1 for _, _, qos in messages))

    def test_invalid_own_position_is_not_published(self):
        vdo = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": 91, "lon": 181},
            talker_id="AIVDO"
        )
        messages = self.run_reader(vdo)
        self.assertEqual(messages, [])

    def test_vdo_does_not_publish_decoded(self):
        vdo = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": -6.2, "lon": 106.8},
            talker_id="AIVDO"
        )
        messages = self.run_reader(vdo)
        self.assertEqual([topic for topic, _, _ in messages],
                         ["ais/aivdo/position"])

    def test_raw_vdm_is_published_even_with_vdo_filter(self):
        vdm = encode_dict({"type": 1, "mmsi": 123456789}, talker_id="AIVDM")
        messages = self.run_reader(vdm, message_type="VDO")
        self.assertEqual([(topic, payload) for topic, payload, _ in messages],
                         [(mqtt_publisher.RAW_TOPIC, vdm[0])])

    def test_mqtt_requires_credentials(self):
        args = SimpleNamespace(mqtt_host="localhost", mqtt_port=1883, mqtt_tls=False)
        with patch.dict("os.environ", {"MQTT_USERNAME": "", "MQTT_PASSWORD": ""}):
            with self.assertRaisesRegex(SystemExit, "MQTT_USERNAME"):
                mqtt_publisher.create_publisher(args)

    def test_mqtt_can_be_disabled(self):
        publisher = mqtt_publisher.create_publisher(SimpleNamespace(mqtt_host=None))
        self.assertFalse(publisher.enabled)
        publisher.publish_raw("!AIVDM,...")
        publisher.close()

    def test_mqtt_authenticates_before_connecting(self):
        class BrokerClient:
            def __init__(self):
                self.calls = []

            def username_pw_set(self, username, password):
                self.calls.append(("auth", username, password))

            def max_queued_messages_set(self, count):
                pass

            def tls_set(self, ca_certs=None):
                self.calls.append(("tls", ca_certs))

            def connect(self, host, port, keepalive):
                self.calls.append(("connect", host, port))

            def loop_start(self):
                self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)

            def is_connected(self):
                return True

        broker = BrokerClient()
        args = SimpleNamespace(mqtt_host="localhost", mqtt_port=8883, mqtt_tls=True)
        with patch.dict("os.environ", {
            "MQTT_USERNAME": "user",
            "MQTT_PASSWORD": "secret",
            "MQTT_CA_CERTS": "/etc/ais-jrc/ca.crt",
        }), \
                patch.object(mqtt_publisher.mqtt, "Client", return_value=broker), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(mqtt_publisher.create_publisher(args).client, broker)
        self.assertEqual(broker.calls, [
            ("auth", "user", "secret"), ("tls", "/etc/ais-jrc/ca.crt"),
            ("connect", "localhost", 8883)
        ])


if __name__ == "__main__":
    unittest.main()
