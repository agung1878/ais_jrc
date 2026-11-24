"""Convert decoded AIS messages to the flat, live MQTT representation."""

import json
import math
from datetime import datetime, timezone
from enum import Enum


FIELDS = (
    "mmsi", "ts", "messageType", "source", "channel", "lat", "lng",
    "cog", "sog", "heading", "navStat", "rot", "accuracy",
    "aisSecond", "raim", "name", "imo", "callSign", "type",
    "aisType", "flag", "toBow", "toStern", "toStarboard",
    "toPortside", "draught", "dest", "eta", "epfd", "text",
    "partNumber", "binaryPayload",
)

MAPPED = {
    "msg_type", "mmsi", "lat", "lon", "course", "speed", "heading",
    "status", "turn", "accuracy", "second", "raim", "shipname", "imo",
    "callsign", "ship_type", "to_bow", "to_stern", "to_starboard",
    "to_port", "draught", "destination", "month", "day", "hour",
    "minute", "epfd", "text", "partno", "data",
}


def valid_checksum(sentence):
    """Verify the NMEA XOR checksum, including its exact two-digit suffix."""
    if not sentence.startswith("!") or sentence.count("*") != 1:
        return False
    body, suffix = sentence[1:].split("*")
    if len(suffix) != 2 or any(c not in "0123456789abcdefABCDEF" for c in suffix):
        return False
    value = 0
    for char in body:
        value ^= ord(char)
    return value == int(suffix, 16)


def available(value, unavailable=()):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return None
    if value in unavailable:
        return None
    return value.value if isinstance(value, Enum) else value


def eta(data):
    parts = [data.get(key) for key in ("month", "day", "hour", "minute")]
    if any(not isinstance(value, int) for value in parts):
        return None
    month, day, hour, minute = parts
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def flat_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return available(value)


def format_message(data, source, channel, received_at=None):
    result = dict.fromkeys(FIELDS)
    result["ts"] = (received_at or datetime.now(timezone.utc)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    result["source"] = source
    result["channel"] = channel or None
    mmsi = data.get("mmsi")
    result["mmsi"] = str(mmsi) if mmsi is not None else None
    result["messageType"] = data.get("msg_type")
    if mmsi is not None:
        result["flag"] = {525: "ID", 366: "US"}.get(int(mmsi) // 1_000_000)

    lat, lon = data.get("lat"), data.get("lon")
    if (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        result["lat"], result["lng"] = lat, lon

    course = available(data.get("course"))
    result["cog"] = course if isinstance(course, (int, float)) and 0 <= course < 360 else None
    result["sog"] = available(data.get("speed"), (102.3, 63))
    result["heading"] = available(data.get("heading"), (511,))
    result["navStat"] = available(data.get("status"), (15,))
    result["rot"] = available(data.get("turn"), (-128, -128.0))
    result["accuracy"] = data.get("accuracy")
    result["aisSecond"] = available(data.get("second"), (60, 61, 62, 63))
    result["raim"] = data.get("raim")
    result["name"] = data.get("shipname") or None
    result["imo"] = str(data["imo"]) if data.get("imo") else None
    result["callSign"] = data.get("callsign") or None
    ship_type = data.get("ship_type")
    result["aisType"] = available(ship_type)
    result["type"] = ship_type.name.upper() if isinstance(ship_type, Enum) and result["aisType"] else None
    for output, source_key in (("toBow", "to_bow"), ("toStern", "to_stern"),
                               ("toStarboard", "to_starboard"), ("toPortside", "to_port")):
        result[output] = available(data.get(source_key), (0,))
    result["draught"] = available(data.get("draught"), (0,))
    result["dest"] = data.get("destination") or None
    result["eta"] = eta(data)
    result["epfd"] = available(data.get("epfd"), (0,))
    result["text"] = data.get("text") or None
    result["partNumber"] = data.get("partno")
    result["binaryPayload"] = flat_value(data["data"]) if "data" in data else None

    # Keep fields unique to other AIS types without creating nested JSON.
    for key, value in data.items():
        if key not in MAPPED and key not in result:
            result[key] = flat_value(value)
    return result
