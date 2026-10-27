"""Small declarative registry: no guessed profile for an unknown device."""
import json
import math
import re
from pathlib import Path

PROFILE_DIR = Path(__file__).with_name("profiles")
NUMERIC_FIELDS = {
    "rolling_shutter_readout_time_nanoseconds", "camera_timestamp_alignment_nanoseconds",
    "calibrated_focal_length.x", "calibrated_focal_length.y",
    "calibrated_principal_point.x", "calibrated_principal_point.y",
    "distortion_poly3.k0", "distortion_poly3.k1", "distortion_poly3.k2",
}
ENUM_FIELDS = {
    "camera_timebase_trust_level": {"TRUSTED_AS_BOOTTIME", "RUNTIME_CHECK_REQUIRED"},
    "rolling_shutter_data_source": {"DEVICE_PROFILE", "IMAGE_METADATA"},
}
TOKEN = re.compile(r'#[^\n]*|"(?:\\.|[^"\\])*"|[A-Za-z_][\w.]*|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[{}:]')


def load_profile(path: Path) -> dict:
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("schema") != 1 or not re.fullmatch(r"[a-zA-Z0-9_-]+", profile.get("device", "")):
        raise ValueError("Invalid profile schema/device")
    if not profile.get("manufacturer") or not re.fullmatch(r"[a-zA-Z0-9_-]+", profile.get("id", "")):
        raise ValueError("Profile needs manufacturer and id")
    cameras = profile.get("camera_overrides")
    if not isinstance(cameras, dict) or not cameras:
        raise ValueError("Profile needs camera_overrides")
    for camera_id, fields in cameras.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", camera_id) or not isinstance(fields, dict):
            raise ValueError("Invalid camera override")
        for key, value in fields.items():
            if key in NUMERIC_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Invalid number: {key}")
                if key.startswith("calibrated_focal_length") and value <= 0:
                    raise ValueError("Focal lengths must be positive")
                if key.endswith("nanoseconds") and not isinstance(value, int):
                    raise ValueError("Nanosecond values must be integers")
                if key == "rolling_shutter_readout_time_nanoseconds" and value < 0:
                    raise ValueError("Readout duration must not be negative")
            elif key in ENUM_FIELDS:
                if value not in ENUM_FIELDS[key]:
                    raise ValueError(f"Invalid enum: {key}")
            else:
                raise ValueError(f"Unsupported override: {key}")
    return profile


def registry() -> dict:
    profiles = [load_profile(path) for path in sorted(PROFILE_DIR.glob("*.json"))]
    result = {profile["device"]: profile for profile in profiles}
    if len(result) != len(profiles):
        raise ValueError("Duplicate device codes in registry")
    return result


def spans(text):
    """Return scalar spans by protobuf path; repeated blocks remain distinct."""
    tokens = [m for m in TOKEN.finditer(text) if not m[0].startswith("#")]
    result = []

    def parse(index, path, nested=False):
        while index < len(tokens):
            if tokens[index][0] == "}":
                if not nested:
                    raise ValueError("Unexpected closing brace")
                return index + 1
            name = tokens[index][0]
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise ValueError("Malformed profile field")
            index += 1
            if index < len(tokens) and tokens[index][0] == ":":
                index += 1
            if index >= len(tokens):
                raise ValueError("Truncated profile")
            value = tokens[index]
            if value[0] == "{":
                # The token position identifies repeated cameras independently.
                block_path = path + ((name, value.start()),)
                index = parse(index + 1, block_path, True)
            else:
                result.append((path, name, value.start(), value.end(), value[0]))
                index += 1
        if nested:
            raise ValueError("Unclosed profile block")
        return index

    parse(0, ())
    return result


def render_profile(base: str, profile: dict) -> str:
    fields = spans(base)
    camera_blocks = {}
    for path, name, _, _, value in fields:
        if len(path) == 1 and path[0][0] == "cameras" and name == "camera_id":
            camera_id = json.loads(value)
            if camera_id in camera_blocks:
                raise ValueError("Duplicate camera ID in base")
            camera_blocks[camera_id] = path[0]
    replacements = []
    for camera_id, overrides in profile["camera_overrides"].items():
        if camera_id not in camera_blocks:
            raise ValueError(f"Camera {camera_id} missing from generic base")
        block = camera_blocks[camera_id]
        for key, value in overrides.items():
            parts = key.split(".")
            matches = [(start, end) for path, name, start, end, _ in fields
                       if path and path[0] == block and [p[0] for p in path[1:]] + [name] == parts]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one {camera_id}.{key}; base format may have changed")
            start, end = matches[0]
            replacements.append((start, end, str(value)))
    for start, end, value in sorted(replacements, reverse=True):
        base = base[:start] + value + base[end:]
    return f"# Experimental device override: {profile['id']}\n" + base
