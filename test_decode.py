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
        self.connected = True

    def is_connected(self):
        return self.connected

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
        decoded = [json.loads(payload)["messageType"] for topic, payload, _ in messages
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
        self.assertEqual([item["messageType"] for item in decoded], [1, 5])
        self.assertEqual(decoded[0]["lat"], -6.2)
        self.assertEqual(decoded[0]["lng"], 106.8)
        self.assertEqual(decoded[0]["name"], None)
        self.assertEqual(decoded[0]["text"], None)
        self.assertNotIn("data", decoded[0])
        self.assertEqual(decoded[1]["name"], "TEST SHIP")
        self.assertIsNone(decoded[1]["lat"])
        self.assertEqual({topic for topic, _, _ in messages}, {
            "ais/aivdm/raw", "ais/aivdo/position", "ais/aivdm/decoded"
        })
        self.assertTrue(all(qos == 0 for _, _, qos in messages))

    def test_invalid_own_position_is_not_published(self):
        vdo = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": 91, "lon": 181},
            talker_id="AIVDO"
        )
        messages = self.run_reader(vdo)
        self.assertEqual(messages, [])

    def test_vdo_is_published_only_to_own_position(self):
        vdo = encode_dict(
            {"type": 1, "mmsi": 123456789, "lat": -6.2, "lon": 106.8},
            talker_id="AIVDO"
        )
        messages = self.run_reader(vdo)
        self.assertEqual([topic for topic, _, _ in messages],
                         ["ais/aivdo/position"])
        payload = json.loads(messages[0][1])
        self.assertEqual(set(payload), {"received_at", "lat", "lon"})
        self.assertEqual((payload["lat"], payload["lon"]), (-6.2, 106.8))

    def test_raw_vdm_is_published_even_with_vdo_filter(self):
        vdm = encode_dict({"type": 1, "mmsi": 123456789}, talker_id="AIVDM")
        messages = self.run_reader(vdm, message_type="VDO")
        self.assertEqual([(topic, payload) for topic, payload, _ in messages],
                         [(mqtt_publisher.RAW_TOPIC, vdm[0])])

    def test_bad_checksum_is_rejected_before_decode(self):
        valid = encode_dict({"type": 1, "mmsi": 123456789}, talker_id="AIVDM")[0]
        bad = valid[:-2] + ("00" if valid[-2:] != "00" else "FF")
        messages = self.run_reader([bad, valid])
        self.assertEqual([topic for topic, _, _ in messages], [
            mqtt_publisher.RAW_TOPIC, mqtt_publisher.RAW_TOPIC,
            mqtt_publisher.DECODED_TOPIC,
        ])

    def test_bad_multipart_fragment_cannot_complete_message(self):
        parts = encode_dict({"type": 5, "mmsi": 123456789,
                             "shipname": "TEST SHIP"}, talker_id="AIVDM")
        bad_second = parts[1][:-2] + ("00" if parts[1][-2:] != "00" else "FF")
        messages = self.run_reader([parts[0], bad_second])
        self.assertEqual([topic for topic, _, _ in messages],
                         [mqtt_publisher.RAW_TOPIC, mqtt_publisher.RAW_TOPIC])

    def test_type_14_keeps_text_in_flat_json(self):
        sentence = encode_dict({"type": 14, "mmsi": 123456789,
                                "text": "TEST MESSAGE"}, talker_id="AIVDM")
        messages = self.run_reader(sentence)
        payload = json.loads(messages[-1][1])
        self.assertEqual(payload["text"], "TEST MESSAGE")
        self.assertIsNone(payload["lat"])
        self.assertIsNone(payload["name"])

    def test_type_5_maps_identity_and_voyage(self):
        sentence = encode_dict({
            "type": 5, "mmsi": 525015424, "shipname": "TEST SHIP",
            "imo": 9934656, "callsign": "YDLF2", "ship_type": 80,
            "to_bow": 83, "to_stern": 4, "to_port": 19,
            "to_starboard": 3, "draught": 2.4,
            "destination": "SAMARINDA", "month": 8, "day": 20,
            "hour": 11, "minute": 0, "epfd": 1,
        }, talker_id="AIVDM")
        messages = self.run_reader(sentence)
        payload = json.loads(messages[-1][1])
        self.assertEqual([item[0] for item in messages].count(mqtt_publisher.DECODED_TOPIC), 1)
        self.assertEqual(payload["mmsi"], "525015424")
        self.assertEqual(payload["name"], "TEST SHIP")
        self.assertEqual(payload["imo"], "9934656")
        self.assertEqual(payload["type"], "TANKER")
        self.assertEqual(payload["aisType"], 80)
        self.assertEqual(payload["eta"], "08-20 11:00")
        self.assertEqual(payload["dest"], "SAMARINDA")
        self.assertEqual(payload["flag"], "ID")
        self.assertIsNone(payload["lat"])
        self.assertIsNone(payload["text"])

    def test_binary_message_is_flat_and_payload_is_hex(self):
        sentence = encode_dict({"type": 8, "mmsi": 123456789,
                                "dac": 1, "fid": 2}, talker_id="AIVDM")
        messages = self.run_reader(sentence)
        payload = json.loads(messages[-1][1])
        self.assertEqual(payload["messageType"], 8)
        self.assertEqual(payload["dac"], 1)
        self.assertEqual(payload["fid"], 2)
        self.assertIsInstance(payload["binaryPayload"], str)
        self.assertTrue(all(not isinstance(value, (dict, list))
                            for value in payload.values()))

    def test_known_position_sentence_and_bad_checksum(self):
        valid = "!AIVDM,1,1,,A,17ldIP1P01Wa6mAtRpL1GQ6D0000,0*32"
        bad = "!AIVDM,1,1,,A,15M67FC000G?ufbE`FepT@3n00Sa,0*5C"
        messages = self.run_reader([valid, bad])
        decoded = [json.loads(payload) for topic, payload, _ in messages
                   if topic == mqtt_publisher.DECODED_TOPIC]
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["mmsi"], "525015424")
        self.assertEqual(decoded[0]["lat"], -6.038)
        self.assertEqual(decoded[0]["lng"], 106.847)
        self.assertEqual(decoded[0]["rot"], None)
        self.assertEqual(decoded[0]["channel"], "A")

    def test_disconnected_publisher_drops_messages(self):
        client = FakeMqtt()
        publisher = mqtt_publisher.MqttPublisher(client)
        client.connected = False
        publisher.publish_decoded('{"mmsi":"123456789"}')
        client.connected = True
        publisher.publish_decoded('{"mmsi":"987654321"}')
        self.assertEqual(client.messages, [
            (mqtt_publisher.DECODED_TOPIC, '{"mmsi":"987654321"}', 0)
        ])

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
