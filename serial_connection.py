"""Serial discovery and reconnect loop shared by both AIS readers."""

import argparse
import math
import os
import signal
import time
from contextlib import contextmanager

import serial
from serial.tools import list_ports


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return seconds


def usb_id(value):
    try:
        number = int(value, 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a hexadecimal USB ID") from exc
    if not 0 <= number <= 0xFFFF:
        raise argparse.ArgumentTypeError("USB ID must be between 0000 and FFFF")
    return number


def add_serial_arguments(parser):
    parser.add_argument("-p", "--port", default="auto",
                        help="Serial path or auto (default: auto)")
    parser.add_argument("-b", "--baudrate", type=int, default=38400)
    parser.add_argument("--serial-number", help="USB adapter serial number")
    parser.add_argument("--vid", type=usb_id, help="USB vendor ID in hex, e.g. 0403")
    parser.add_argument("--pid", type=usb_id, help="USB product ID in hex, e.g. 6015")
    parser.add_argument("--location", help="USB physical location (for adapters without serial numbers)")
    parser.add_argument("--retry-interval", type=positive_seconds, default=3.0,
                        help="Seconds between connection attempts (default: 3)")
    parser.add_argument("--list-ports", action="store_true",
                        help="List serial devices and exit without connecting")


def print_ports():
    for port in sorted(list_ports.comports()):
        print(f"{port.device}: {port.description} [{port.hwid}]", flush=True)


class PortSelector:
    def __init__(self, args):
        self.port = args.port
        self.filters = {
            name: getattr(args, name, None)
            for name in ("vid", "pid", "serial_number", "location")
            if getattr(args, name, None) is not None
        }
        if self.port != "auto" and self.filters:
            raise ValueError("USB identity filters require --port auto")
        self.identity = None

    def matches(self, port):
        filters = self.identity if self.identity is not None else self.filters
        return all(getattr(port, name, None) == value for name, value in filters.items())

    def select(self):
        if self.port != "auto":
            return self.port, None
        # USB only: exclude Bluetooth and built-in UARTs. Avoid symlink duplicates.
        candidates = [port for port in list_ports.comports()
                      if port.vid is not None and port.pid is not None and self.matches(port)]
        if not candidates:
            return None, "Waiting for matching USB serial device"
        if len(candidates) > 1:
            devices = ", ".join(sorted(port.device for port in candidates))
            return None, f"Multiple matching USB serial devices ({devices}); configure --serial-number or --location"
        port = candidates[0]
        if not port.serial_number and not port.location:
            return None, f"No stable USB identity for {port.device}; configure an explicit --port"
        return port.device, port

    def remember(self, port):
        if port is None or self.identity is not None:
            return
        self.identity = dict(self.filters, vid=port.vid, pid=port.pid)
        if port.serial_number:
            self.identity["serial_number"] = port.serial_number
        else:
            self.identity["location"] = port.location
        if port.interface is not None:
            self.identity["interface"] = port.interface

    def present(self, device):
        if self.port != "auto":
            return os.path.exists(device)
        return any(port.device == device and self.matches(port)
                   for port in list_ports.comports())


def serial_lines(args):
    """Yield bytes, or None at each new connection to reset decoder state.

    The caller must close this generator on shutdown (contextlib.closing).
    """
    selector = PortSelector(args)
    last_status = None

    def status(message):
        nonlocal last_status
        if message != last_status:
            print(message, flush=True)
            last_status = message

    while True:
        try:
            device, info = selector.select()
            if device is None:
                status(f"{info}; retrying every {args.retry_interval:g}s")
            else:
                with serial.Serial(port=device, baudrate=args.baudrate, timeout=1) as connection:
                    selector.remember(info)
                    status(f"Serial connected: {device} at {args.baudrate} baud")
                    yield None
                    while True:
                        line = connection.readline()
                        if line:
                            yield line
                        elif not selector.present(device):
                            raise serial.SerialException(f"Device removed: {device}")
        except (serial.SerialException, OSError) as exc:
            status(f"Serial unavailable: {exc}; retrying every {args.retry_interval:g}s")
        time.sleep(args.retry_interval)


@contextmanager
def stop_on_sigterm():
    """Allow service managers to stop readers through their normal cleanup path."""
    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
