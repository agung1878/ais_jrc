import argparse
import contextlib
import io
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import serial
from serial.tools.list_ports_common import ListPortInfo

import serial_connection as connection


def options(**kwargs):
    values = dict(port="auto", baudrate=38400, retry_interval=3,
                  serial_number=None, vid=None, pid=None, location=None)
    values.update(kwargs)
    return SimpleNamespace(**values)


def usb_port(device="/dev/cu.usbserial-A", serial_number="AIS", location="1-2"):
    port = ListPortInfo(device)
    port.vid, port.pid = 0x0403, 0x6015
    port.serial_number, port.location = serial_number, location
    return port


class FakeSerial:
    def __init__(self, *events):
        self.events = iter(events)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def readline(self):
        event = next(self.events)
        if isinstance(event, BaseException):
            raise event
        return event


class DiscoveryTests(unittest.TestCase):
    def test_usb_only_and_ambiguous_selection(self):
        selector = connection.PortSelector(options())
        first, second = usb_port(), usb_port("/dev/ttyUSB0", "OTHER")
        bluetooth = ListPortInfo("/dev/cu.Bluetooth-Incoming-Port")
        with patch.object(connection.list_ports, "comports", return_value=[bluetooth, first]):
            self.assertEqual(selector.select(), (first.device, first))
        with patch.object(connection.list_ports, "comports", return_value=[first, second]):
            device, reason = selector.select()
            self.assertIsNone(device)
            self.assertIn("Multiple matching", reason)

    def test_filters_and_sticky_identity_follow_port_rename(self):
        selector = connection.PortSelector(options(serial_number="AIS", vid=0x0403, pid=0x6015))
        first = usb_port()
        with patch.object(connection.list_ports, "comports", return_value=[first]):
            self.assertEqual(selector.select()[0], first.device)
        selector.remember(first)
        renamed = usb_port("/dev/ttyUSB1", location="2-3")
        other = usb_port("/dev/ttyUSB0", "OTHER")
        with patch.object(connection.list_ports, "comports", return_value=[other, renamed]):
            self.assertEqual(selector.select()[0], renamed.device)
        with patch.object(connection.list_ports, "comports", return_value=[other]):
            self.assertIsNone(selector.select()[0])

    def test_location_pins_adapters_without_serial_number(self):
        selector = connection.PortSelector(options())
        selector.remember(usb_port(serial_number=None))
        moved = usb_port("/dev/ttyUSB0", serial_number=None, location="3-4")
        original = usb_port("/dev/ttyUSB1", serial_number=None)
        with patch.object(connection.list_ports, "comports", return_value=[moved, original]):
            self.assertEqual(selector.select()[0], original.device)

    def test_unidentifiable_adapter_requires_explicit_path(self):
        port = usb_port(serial_number=None, location=None)
        with patch.object(connection.list_ports, "comports", return_value=[port]):
            device, reason = connection.PortSelector(options()).select()
        self.assertIsNone(device)
        self.assertIn("explicit --port", reason)

    def test_explicit_path_and_filter_conflict(self):
        with patch.object(connection.list_ports, "comports") as enumerate_ports:
            self.assertEqual(connection.PortSelector(options(port="/dev/ais")).select(),
                             ("/dev/ais", None))
            enumerate_ports.assert_not_called()
        with self.assertRaisesRegex(ValueError, "require --port auto"):
            connection.PortSelector(options(port="/dev/ais", serial_number="AIS"))

    def test_cli_validation(self):
        parser = argparse.ArgumentParser()
        connection.add_serial_arguments(parser)
        args = parser.parse_args(["--vid", "0403", "--pid", "0x6015"])
        self.assertEqual((args.port, args.vid, args.pid), ("auto", 0x0403, 0x6015))
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(argparse.ArgumentTypeError):
                connection.positive_seconds(value)


class ReconnectTests(unittest.TestCase):
    def test_missing_at_start_and_repeated_reconnect_with_renamed_ports(self):
        first = usb_port()
        second = usb_port("/dev/cu.usbserial-B")
        third = usb_port("/dev/ttyUSB1")
        handles = [FakeSerial(b"first\n", serial.SerialException("unplugged")),
                   FakeSerial(b"second\n", OSError("removed")),
                   FakeSerial(b"third\n")]
        with patch.object(connection.list_ports, "comports",
                          side_effect=[[], [first], [], [second], [third]]), \
                patch.object(connection.serial, "Serial", side_effect=handles) as open_port, \
                patch.object(connection.time, "sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.closing(connection.serial_lines(options())) as lines:
            self.assertIsNone(next(lines))
            self.assertEqual(next(lines), b"first\n")
            self.assertIsNone(next(lines))
            self.assertEqual(next(lines), b"second\n")
            self.assertIsNone(next(lines))
            self.assertEqual(next(lines), b"third\n")
        self.assertEqual([call.kwargs["port"] for call in open_port.call_args_list],
                         [first.device, second.device, third.device])
        self.assertEqual(sleep.call_count, 4)
        self.assertTrue(all(handle.closed for handle in handles))

    def test_explicit_port_open_failure_is_retried(self):
        handle = FakeSerial(b"AIS\n")
        with patch.object(connection.serial, "Serial",
                          side_effect=[serial.SerialException("missing"), PermissionError("denied"), handle]) as open_port, \
                patch.object(connection.time, "sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.closing(connection.serial_lines(options(port="/dev/ais"))) as lines:
            self.assertIsNone(next(lines))
            self.assertEqual(next(lines), b"AIS\n")
        self.assertEqual(open_port.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(handle.closed)

    def test_read_timeout_does_not_reconnect_when_device_is_present(self):
        handle = FakeSerial(b"", b"AIS\n")
        with patch.object(connection.serial, "Serial", return_value=handle) as open_port, \
                patch.object(connection.os.path, "exists", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.closing(connection.serial_lines(options(port="/dev/ais"))) as lines:
            self.assertIsNone(next(lines))
            self.assertEqual(next(lines), b"AIS\n")
        open_port.assert_called_once()

    def test_missing_device_on_timeout_reconnects(self):
        first, second = FakeSerial(b""), FakeSerial(b"AIS\n")
        with patch.object(connection.serial, "Serial", side_effect=[first, second]), \
                patch.object(connection.os.path, "exists", return_value=False), \
                patch.object(connection.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.closing(connection.serial_lines(options(port="/dev/ais"))) as lines:
            self.assertIsNone(next(lines))
            self.assertIsNone(next(lines))
            self.assertTrue(first.closed)
            self.assertEqual(next(lines), b"AIS\n")

    def test_ctrl_c_during_retry_stops(self):
        with patch.object(connection.list_ports, "comports", return_value=[]), \
                patch.object(connection.time, "sleep", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                next(connection.serial_lines(options()))

    def test_sigterm_uses_cleanup_path_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with connection.stop_on_sigterm():
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)


if __name__ == "__main__":
    unittest.main()
