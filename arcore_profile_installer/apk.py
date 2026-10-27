"""APK metadata helpers; no APK rewriting or signature bypass."""
import hashlib
import json
import struct
import zipfile
from pathlib import Path

PACKAGE = "com.google.ar.core"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def manifest_info(data: bytes) -> dict:
    """Read package/version/SDK from Android binary XML, without an SDK install."""
    if len(data) < 8 or struct.unpack_from("<H", data)[0] != 3:
        raise ValueError("Not an Android binary XML manifest")
    strings = []
    result = {}
    pos = struct.unpack_from("<H", data, 2)[0]
    limit = struct.unpack_from("<I", data, 4)[0]
    if limit > len(data):
        raise ValueError("Truncated manifest")
    while pos < limit:
        kind, header_size, size = struct.unpack_from("<HHI", data, pos)
        if size < header_size or size < 8 or pos + size > limit:
            raise ValueError("Invalid XML chunk")
        if kind == 1:  # ResStringPool
            count, _, flags, string_start, _ = struct.unpack_from("<IIIII", data, pos + 8)
            utf8 = bool(flags & 0x100)

            def length(at):
                if utf8:
                    v = data[at]
                    return (((v & 0x7f) << 8) | data[at + 1], at + 2) if v & 0x80 else (v, at + 1)
                v = struct.unpack_from("<H", data, at)[0]
                return (((v & 0x7fff) << 16) | struct.unpack_from("<H", data, at + 2)[0], at + 4) if v & 0x8000 else (v, at + 2)

            for index in range(count):
                off = struct.unpack_from("<I", data, pos + header_size + index * 4)[0]
                at = pos + string_start + off
                n, at = length(at)
                if utf8:
                    n, at = length(at)  # byte count after UTF-16 character count
                else:
                    n *= 2
                strings.append(data[at:at + n].decode("utf-8" if utf8 else "utf-16le"))
        elif kind == 0x102:  # ResXMLTree_attrExt follows ResXMLTree_node
            ext = pos + header_size
            tag = strings[struct.unpack_from("<I", data, ext + 4)[0]]
            start, stride, count = struct.unpack_from("<HHH", data, ext + 8)
            attrs = {}
            for index in range(count):
                at = ext + start + index * stride
                _, name, raw = struct.unpack_from("<III", data, at)
                typ = data[at + 15]
                value = struct.unpack_from("<I", data, at + 16)[0]
                attrs[strings[name]] = strings[raw] if raw != 0xffffffff else (strings[value] if typ == 3 else value)
            if tag == "manifest":
                result.update({k: attrs[k] for k in ("package", "versionCode", "versionName", "split") if k in attrs})
            elif tag == "uses-sdk":
                result.update({k: attrs[k] for k in ("minSdkVersion", "targetSdkVersion") if k in attrs})
            elif tag == "application":
                result["debuggable"] = bool(attrs.get("debuggable", 0))
        pos += size
    return result


def apk_info(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        info = manifest_info(archive.read("AndroidManifest.xml"))
        info["arm64"] = "lib/arm64-v8a/libarcore_c.so" in archive.namelist()
        info["max_compat_asset"] = "assets/device_profile_max_compatibility.textproto" in archive.namelist()
    if info.get("package") != PACKAGE or not info["arm64"] or info.get("split"):
        raise ValueError("Expected a standalone ARM64 com.google.ar.core APK")
    info.update(path=str(path.resolve()), sha256=sha256(path))
    source = path.with_suffix(".source.json")
    if source.exists() and json.loads(source.read_text(encoding="utf-8"))["sha256"] != info["sha256"]:
        raise ValueError("APK differs from its recorded download hash")
    return info
