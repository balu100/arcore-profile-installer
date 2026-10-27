import io
import re
import shutil
import subprocess

from .apk import PACKAGE


class ADB:
    def __init__(self, known_devices=(), serial=None, executable="adb"):
        self.exe = shutil.which(executable)
        if not self.exe:
            raise ValueError("adb is missing from PATH")
        result = subprocess.run([self.exe, "devices", "-l"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        online = [line.split() for line in result.stdout.splitlines() if re.search(r"\sdevice(?:\s|$)", line)]
        if serial:
            online = [row for row in online if row[0] == serial]
        else:
            known = [row for row in online if any(f"device:{device}" in row for device in known_devices)]
            if known:
                online = known
        if len(online) != 1:
            raise ValueError("Connect one authorized device or supply --serial; no device was changed")
        self.serial = online[0][0]
        self.device = self.run("shell", "getprop", "ro.product.device").strip()
        self.manufacturer = self.run("shell", "getprop", "ro.product.manufacturer").strip()

    def command(self, *args):
        return [self.exe, "-s", self.serial, *map(str, args)]

    def run(self, *args, output=None, timeout=60):
        with output.open("wb") if output else io.BytesIO() as sink:
            result = subprocess.run(self.command(*args), stdout=sink if output else subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout)
        error = result.stderr.decode("utf-8", "replace")
        text = result.stdout.decode("utf-8", "replace") if output is None else ""
        if result.returncode or re.search(r"\b(Failure|INSTALL_FAILED_|DELETE_FAILED_)", text):
            raise RuntimeError(f"ADB {args}: {text.strip()} {error.strip()}")
        return text

    def installed(self):
        text = self.run("shell", "dumpsys", "package", PACKAGE)
        code = re.search(r"versionCode=(\d+)", text)
        name = re.search(r"versionName=(\S+)", text)
        return {"versionCode": int(code[1]), "versionName": name[1] if name else "?"} if code else None

    def bind(self, profile=None):
        if profile is not None and (self.device != profile["device"]
                or self.manufacturer.casefold() != profile["manufacturer"].casefold()):
            raise ValueError("Device/manufacturer does not match this profile; refusing installation")
        if self.run("shell", "am", "get-current-user").strip() != "0":
            raise ValueError("Select Android's primary user (0) before installation")
        if "arm64-v8a" not in self.run("shell", "getprop", "ro.product.cpu.abilist").strip().split(","):
            raise ValueError("This release's installation backend requires ARM64")
