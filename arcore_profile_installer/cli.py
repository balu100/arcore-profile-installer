import argparse
import json
import subprocess
import struct
import sys
import tarfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

from .device import ADB
from .diagnostics import verify_runtime
from .installer import fetch_latest, install_profile, prepare, update
from .profiles import load_profile, registry


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)

    def flush(self):
        self.console.flush()
        self.log.flush()


def select_profile(mode, profiles, device=None, supplied=None):
    if mode == "generic":
        if supplied is not None or device is not None:
            raise ValueError("--profile and --device require --mode device")
        return None
    if mode != "device":
        raise ValueError("Unknown profile mode")
    if supplied is not None:
        if device is not None and device != supplied["device"]:
            raise ValueError("--device differs from the external profile's device")
        return supplied
    if device not in profiles:
        raise ValueError("Device mode needs a registered --device or an external --profile; no guessed override is applied")
    return profiles[device]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Experimental ARCore installer: Google generic profile by default, optional device overrides (ARM64 backend).")
    parser.add_argument("action", choices=("profiles", "status", "fetch", "prepare", "update", "install-profile", "verify"))
    parser.add_argument("--apk", type=Path, help="Original standalone Google ARM64 APK; otherwise fetch Google's latest SDK release runtime")
    parser.add_argument("--staging-apk", type=Path, help="Pinned original ARM64 ARCore 1.6.181112096 APK")
    parser.add_argument("--mode", choices=("generic", "device"), default="generic",
                        help="Profile preparation/installation mode (default: generic); update preserves the current profile")
    parser.add_argument("--profile", type=Path, help="External device override JSON, using the documented schema")
    parser.add_argument("--device", help="Device-mode codename; auto-detected when connected")
    parser.add_argument("--serial", help="Explicit ADB serial, required when selection is ambiguous")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--work-dir", type=Path, default=Path.cwd() / ".arcore-state")
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--replace-arcore-data", action="store_true", help="Required for install-profile: acknowledges ARCore app-data replacement")
    args = parser.parse_args(argv)
    if not 5 <= args.seconds <= 120:
        parser.error("--seconds must be 5..120")
    if args.action == "install-profile" and (not args.replace_arcore_data or not args.staging_apk):
        parser.error("install-profile requires --staging-apk and --replace-arcore-data")
    if args.mode == "generic" and (args.profile or args.device):
        parser.error("--profile and --device require --mode device")
    work = args.work_dir.resolve()
    folder = work / "runs" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + args.action)
    folder.mkdir(parents=True)
    original = sys.stdout, sys.stderr
    transcript = (folder / "console.txt").open("w", encoding="utf-8", buffering=1)
    sys.stdout, sys.stderr = Tee(original[0], transcript), Tee(original[1], transcript)
    try:
        profiles = registry()
        supplied = load_profile(args.profile) if args.profile else None
        if supplied:
            profiles[supplied["device"]] = supplied
        if args.action == "profiles":
            print(json.dumps([{k: value.get(k) for k in ("device", "label", "status", "tested", "limitations")}
                              for value in profiles.values()], indent=2))
            return 0
        if args.action == "fetch":
            print(fetch_latest(work))
            return 0
        if args.action == "prepare":
            profile = select_profile(args.mode, profiles, args.device, supplied)
            data = prepare(args.apk or fetch_latest(work), profile, folder)
            label = "Google generic (unchanged)" if profile is None else profile["id"]
            print(f"Prepared {len(data)} profile bytes: {label}; nothing was installed")
            return 0
        adb = ADB(profiles, args.serial, args.adb)
        print(f"Selected {adb.manufacturer}/{adb.device}: {adb.serial}", flush=True)
        if args.action == "status":
            info = {"device": adb.device, "manufacturer": adb.manufacturer, "installed": adb.installed(),
                    "known_profile": adb.device in profiles}
            (folder / "status.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            print(json.dumps(info, indent=2))
            return 0
        code = (args.device or adb.device) if args.mode == "device" else None
        profile = select_profile(args.mode, profiles, code, supplied)
        adb.bind(profile)
        if args.action == "verify":
            return verify_runtime(adb, folder, args.seconds)
        apk = args.apk or fetch_latest(work)
        if args.action == "update":
            update(adb, apk, profile, folder)
        else:
            install_profile(adb, apk, args.staging_apk, profile, folder)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Check the phone and the run log before retrying.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError, IndexError,
            struct.error, tarfile.TarError, zipfile.BadZipFile, zlib.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        (folder / "error.txt").write_text(str(error), encoding="utf-8")
        return 1
    finally:
        print(f"Run files: {folder}")
        sys.stdout, sys.stderr = original
        transcript.close()
