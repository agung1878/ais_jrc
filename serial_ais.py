import argparse
from contextlib import closing
from datetime import datetime

from serial_connection import (
    PortSelector, add_serial_arguments, print_ports, serial_lines, stop_on_sigterm,
)


def read_serial(args):
    try:
        with stop_on_sigterm(), closing(serial_lines(args)) as lines:
            for data in lines:
                if data:
                    message = data.decode("utf-8", errors="replace").strip()
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{timestamp}] {message}", flush=True)
    except KeyboardInterrupt:
        print("\nStopped by user.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read serial data and print to terminal")
    add_serial_arguments(parser)
    args = parser.parse_args()
    if args.list_ports:
        print_ports()
        parser.exit()
    try:
        PortSelector(args)
    except ValueError as exc:
        parser.error(str(exc))
    read_serial(args)
