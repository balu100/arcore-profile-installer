"""Read/repack Android full backups without extracting archive paths to disk."""
import io
import re
import tarfile
import time
import zlib

PACKAGE = "com.google.ar.core"
PREFIX = f"apps/{PACKAGE}/"
TARGET = PREFIX + "f/custom_device_profile.textproto"
MAX_SIZE = 32 * 1024 * 1024


def unpack(raw: bytes):
    if len(raw) > MAX_SIZE:
        raise ValueError("Backup exceeds size limit")
    stream = io.BytesIO(raw)
    header = [stream.readline(128) for _ in range(4)]
    if header[0] != b"ANDROID BACKUP\n" or header[1].strip() not in (b"1", b"2", b"3", b"4", b"5"):
        raise ValueError("Invalid Android backup")
    if header[3] != b"none\n" or header[2] not in (b"0\n", b"1\n"):
        raise ValueError("Expected an unencrypted full backup")
    data = stream.read()
    if header[2] == b"1\n":
        decoder = zlib.decompressobj()
        data = decoder.decompress(data, MAX_SIZE + 1)
        if len(data) > MAX_SIZE or not decoder.eof:
            raise ValueError("Truncated or oversized backup")
    if len(data) > MAX_SIZE:
        raise ValueError("Backup payload exceeds size limit")
    entries = []
    names = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            name = member.name
            if (not name.startswith(PREFIX) or ".." in name.split("/") or "\\" in name
                    or name in names or not (member.isfile() or member.isdir())):
                raise ValueError(f"Unexpected backup member: {name}")
            names.add(name)
            if member.size > MAX_SIZE:
                raise ValueError("Oversized archive member")
            body = archive.extractfile(member).read() if member.isfile() else b""
            entries.append((member, body))
    manifests = [body for member, body in entries if member.name == PREFIX + "_manifest"]
    if len(manifests) != 1:
        raise ValueError("Missing ARCore manifest; backup may be empty or confirmation timed out")
    lines = manifests[0].decode("utf-8").splitlines()
    if len(lines) < 7 or lines[0] != "1" or lines[1] != PACKAGE or not re.fullmatch(r"\d+", lines[2]):
        raise ValueError("Invalid ARCore backup manifest")
    return header, entries, int(lines[2])


def inject(raw: bytes, profile: bytes) -> bytes:
    if not profile or len(profile) > 1024 * 1024:
        raise ValueError("Invalid profile size")
    header, entries, _ = unpack(raw)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        for member, body in entries:
            if member.name != TARGET:
                archive.addfile(member, io.BytesIO(body) if member.isfile() else None)
        member = tarfile.TarInfo(TARGET)
        member.size, member.mode, member.mtime = len(profile), 0o600, int(time.time())
        archive.addfile(member, io.BytesIO(profile))
    payload = output.getvalue()
    if header[2] == b"1\n":
        payload = zlib.compress(payload, 9)
    result = b"".join(header) + payload
    if extract_profile(result) != profile:
        raise ValueError("Generated backup verification failed")
    return result


def extract_profile(raw: bytes) -> bytes:
    _, entries, _ = unpack(raw)
    for member, body in entries:
        if member.name == TARGET:
            return body
    raise ValueError("Custom profile missing from verification backup")
