
import argparse
import json
import time
from datetime import datetime, timezone

from contextlib import closing
from pyais import decode

from mqtt_publisher import add_mqtt_arguments, create_publisher
from serial_connection import (
    PortSelector, add_serial_arguments, print_ports, serial_lines, stop_on_sigterm,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="AIS Serial Reader & Decoder"
    )

    add_serial_arguments(parser)

    parser.add_argument(
        "--type",
        choices=["VDM", "VDO", "BOTH"],
        default="BOTH",
        help="Filter AIVDM / AIVDO"
    )

    parser.add_argument(
        "--mmsi",
        type=int,
        nargs="+",
        help="Filter satu atau lebih MMSI"
    )

    parser.add_argument(
        "--msg-type",
        type=int,
        nargs="+",
        help="Filter AIS message type"
    )

    parser.add_argument(
        "--position-only",
        action="store_true",
        help="Hanya tampilkan pesan dengan posisi valid"
    )

    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LAT", "MIN_LON", "MAX_LAT", "MAX_LON"),
        help="Filter berdasarkan area koordinat"
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Tampilkan raw NMEA bersama hasil decode"
    )

    add_mqtt_arguments(parser)

    args = parser.parse_args()
    if args.list_ports:
        print_ports()
        parser.exit()
    try:
        PortSelector(args)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def get_position(data):
    lat = data.get("lat")
    lon = data.get("lon")

    if not isinstance(lat, (int, float)):
        return None

    if not isinstance(lon, (int, float)):
        return None

    # AIS menggunakan nilai khusus untuk posisi tidak tersedia
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    return lat, lon


def matches_filter(data, source, args):
    if args.type != "BOTH" and source != args.type:
        return False

    if args.mmsi and data.get("mmsi") not in args.mmsi:
        return False

    msg_type = data.get("msg_type", data.get("type"))

    if args.msg_type and msg_type not in args.msg_type:
        return False

    position = get_position(data)

    if args.position_only and position is None:
        return False

    if args.bbox:
        if position is None:
            return False

        min_lat, min_lon, max_lat, max_lon = args.bbox
        lat, lon = position

        if not (
            min_lat <= lat <= max_lat
            and min_lon <= lon <= max_lon
        ):
            return False

    return True


def handle_message(parts, source, args, publisher):
    try:
        decoded = decode(*parts)
        data = decoded.asdict()

        position = get_position(data)

        # Normalisasi koordinat tidak valid
        if position is None:
            data.pop("lat", None)
            data.pop("lon", None)

        received_at = datetime.now(timezone.utc).isoformat()
        if source == "VDO" and position is not None:
            lat, lon = position
            publisher.publish_position(received_at, data.get("mmsi"), lat, lon)

        if not matches_filter(data, source, args):
            return

        result = {
            "received_at": received_at,
            "source": source,
            "mmsi": data.get("mmsi"),
            "message_type": data.get(
                "msg_type", data.get("type")
            ),
            "data": data,
        }

        if args.raw:
            result["raw"] = parts

        payload = json.dumps(result, ensure_ascii=False, default=str)
        print(payload, flush=True)
        if source == "VDM":
            publisher.publish_decoded(payload)

    except Exception as e:
        print(f"[DECODE ERROR] {e}", flush=True)


def main():
    args = parse_args()

    if args.bbox:
        a, b, c, d = args.bbox
        if a > c or b > d:
            raise SystemExit("Invalid bbox boundaries")

    publisher = create_publisher(args)

    # Penyimpanan sementara fragmen multipart
    pending = {}

    print(f"Port     : {args.port}")
    print(f"Baudrate : {args.baudrate}")
    print(f"Filter   : {args.type}")
    print("Listening AIS data... CTRL+C to stop\n")

    try:
        with stop_on_sigterm(), closing(serial_lines(args)) as lines:
            for line in lines:
                if line is None:
                    pending.clear()
                    continue

                raw = line.decode(
                    "ascii", errors="replace"
                ).strip()

                # Mendukung prefix NMEA tag block
                start = raw.find("!AI")
                if start == -1:
                    continue

                raw = raw[start:]

                if not raw.startswith(("!AIVDM,", "!AIVDO,")):
                    continue

                if raw.startswith("!AIVDM,"):
                    publisher.publish_raw(raw)

                fields = raw.split(",")

                if len(fields) < 7:
                    continue

                source = fields[0][3:]  # VDM atau VDO

                if (args.type != "BOTH" and source != args.type
                        and (source != "VDO" or not publisher.enabled)):
                    continue

                try:
                    total = int(fields[1])
                    number = int(fields[2])
                except ValueError:
                    continue

                # Single-part AIS message
                if total == 1:
                    handle_message([raw], source, args, publisher)
                    continue

                # Multipart AIS message
                seq_id = fields[3]
                channel = fields[4]

                key = (source, channel, seq_id)

                now = time.monotonic()

                # Buang fragmen lama
                for k in list(pending):
                    if now - pending[k]["time"] > 30:
                        del pending[k]

                if number == 1:
                    pending[key] = {
                        "total": total,
                        "parts": {1: raw},
                        "time": now,
                    }

                elif key in pending:
                    entry = pending[key]

                    if entry["total"] != total:
                        del pending[key]
                        continue

                    entry["parts"][number] = raw
                    entry["time"] = now

                    if len(entry["parts"]) == total:
                        if all(
                            i in entry["parts"]
                            for i in range(1, total + 1)
                        ):
                            parts = [
                                entry["parts"][i]
                                for i in range(1, total + 1)
                            ]

                            handle_message(parts, source, args, publisher)

                        del pending[key]

    except KeyboardInterrupt:
        print("\nAIS reader stopped.")

    finally:
        publisher.close()


if __name__ == "__main__":
    main()
