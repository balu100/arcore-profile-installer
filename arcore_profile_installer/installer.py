import hashlib
import json
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

from .apk import PACKAGE, apk_info, sha256
from .backup import extract_profile, inject, unpack
from .profiles import render_profile

MAPS = "com.google.android.apps.maps"
STAGE_VERSION = 181112096
STAGE_SHA = "dfcfd34078d84bc687571c42d28b6a683ecece40d790f83582502d98b98cce11"
RELEASE_API = "https://api.github.com/repos/google-ar/arcore-android-sdk/releases/latest"


def fetch_latest(work: Path) -> Path:
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": "ARCoreProfileInstaller/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    matches = [asset for asset in release["assets"] if re.fullmatch(r"Google_Play_Services_for_AR_[0-9.]+\.apk", asset["name"])]
    if len(matches) != 1:
        raise ValueError("Official release has no unique ARM runtime APK")
    asset = matches[0]
    url, digest = asset["browser_download_url"], asset.get("digest", "")
    if not url.startswith("https://github.com/google-ar/arcore-android-sdk/releases/download/"):
        raise ValueError("Unexpected asset origin")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not 0 < asset["size"] < 256 * 1024 * 1024:
        raise ValueError("Missing digest or unsupported asset size")
    downloads = work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target, expected = downloads / asset["name"], digest.split(":", 1)[1]
    if not target.exists() or sha256(target) != expected:
        temporary = target.with_suffix(".apk.part")
        print(f"Downloading official Google release {release['tag_name']}...", flush=True)
        with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as output:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > asset["size"]:
                    raise ValueError("Download exceeds advertised size")
                output.write(chunk)
        if size != asset["size"] or sha256(temporary) != expected:
            raise ValueError("Download checksum mismatch")
        apk_info(temporary)
        temporary.replace(target)
    apk_info(target)
    target.with_suffix(".source.json").write_text(json.dumps({
        "release": release["tag_name"], "url": url, "sha256": expected,
        "size": asset["size"], "github_digest": digest,
    }, indent=2) + "\n", encoding="utf-8")
    return target


def prepare(apk: Path, profile: dict | None, folder: Path) -> bytes:
    info = apk_info(apk)
    with zipfile.ZipFile(apk) as archive:
        base = archive.read("assets/device_profile_max_compatibility.textproto")
    # Generic mode copies Google's asset byte-for-byte, without device overrides.
    data = base if profile is None else render_profile(base.decode("utf-8"), profile).encode("utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "custom_device_profile.textproto").write_bytes(data)
    (folder / "prepared.json").write_text(json.dumps({
        "mode": "generic" if profile is None else "device",
        "profile_id": "google-max-compatibility" if profile is None else profile["id"],
        "device": None if profile is None else profile["device"],
        "base_asset_sha256": hashlib.sha256(base).hexdigest(),
        "profile_sha256": hashlib.sha256(data).hexdigest(), "source_apk": info,
        "limitations": (["Generic compatibility template; camera and IMU values are not calibrated for this device."]
                        if profile is None else profile.get("limitations", [])),
    }, indent=2), encoding="utf-8")
    return data


def snapshot(adb, folder):
    installed = adb.installed()
    paths = [line.removeprefix("package:").strip() for line in adb.run("shell", "pm", "path", PACKAGE).splitlines()
             if line.startswith("package:")]
    if not installed or len(paths) != 1:
        raise ValueError("Cannot save a complete standalone recovery APK; split installations are not supported yet")
    target = folder / "before.apk"
    adb.run("pull", paths[0], target, timeout=180)
    if int(apk_info(target)["versionCode"]) != installed["versionCode"]:
        raise ValueError("Installed ARCore changed during snapshot; retry")
    (folder / "before.json").write_text(json.dumps(installed, indent=2), encoding="utf-8")
    return target


def validate_target(adb, apk):
    info, installed = apk_info(apk), adb.installed()
    if installed and int(info["versionCode"]) < installed["versionCode"]:
        raise ValueError("Target is older than installed ARCore; refusing downgrade")
    sdk = int(adb.run("shell", "getprop", "ro.build.version.sdk").strip())
    if sdk < int(info.get("minSdkVersion", 999)):
        raise ValueError("Android version is below the APK minimum")
    return info, installed


def finish(adb, info):
    actual = adb.installed()
    if not actual or actual["versionCode"] != int(info["versionCode"]):
        raise RuntimeError("Installed version verification failed")
    print(f"ADB installed version:\n  versionName={actual['versionName']}\n  versionCode={actual['versionCode']}")
    print("Installation is verified. Test custom-profile loading with the verify command.")


def update(adb, apk, profile, folder):
    adb.bind(profile)
    info, installed = validate_target(adb, apk)
    if not installed:
        raise ValueError("ARCore absent; use install-profile for initial setup")
    snapshot(adb, folder)
    print("Updating original APK with install -r; no app-data reset.", flush=True)
    print(adb.run("install", "-r", apk.resolve(), timeout=180).strip())
    finish(adb, info)


def install_profile(adb, apk, stage, profile, folder):
    adb.bind(profile)
    info, installed = validate_target(adb, apk)
    if sha256(stage) != STAGE_SHA or int(apk_info(stage)["versionCode"]) != STAGE_VERSION:
        raise ValueError("Staging APK is not the pinned ARM64 ARCore 1.6 build")
    if int(info["versionCode"]) <= STAGE_VERSION:
        raise ValueError("Target runtime must be newer than the staging runtime")
    if adb.run("shell", "test -x /system/bin/bu && echo present || true").strip() != "present":
        raise ValueError("Legacy bu backend is unavailable; no app data was changed")
    if not re.search(r"\bunstop\b", adb.run("shell", "cmd", "package", "help")):
        raise ValueError("Package unstop command is unavailable; this staging backend is unsupported")
    data = prepare(apk, profile, folder)
    previous = snapshot(adb, folder) if installed else None
    remote = f"/data/local/tmp/arcore-profile-{folder.name}.adb"
    print("Profile choice: " + ("Google generic (unchanged)" if profile is None else profile["id"]), flush=True)
    print("Replacing ARCore app data. Maps app data is not cleared.", flush=True)
    try:
        adb.run("shell", "am", "force-stop", MAPS)
        if installed:
            adb.run("uninstall", PACKAGE)
        adb.run("install", stage.resolve(), timeout=180)
        adb.run("shell", "cmd", "package", "unstop", PACKAGE)
        print("Approve BACK UP MY DATA on the unlocked phone (empty password).", flush=True)
        source = folder / "stage.adb"
        adb.run("exec-out", "bu", "backup", "-noapk", "-noshared", "-nokeyvalue", PACKAGE, output=source, timeout=100)
        raw = source.read_bytes()
        if unpack(raw)[2] != STAGE_VERSION:
            raise ValueError("Staging backup manifest version mismatch")
        injected = folder / "injected.adb"
        injected.write_bytes(inject(raw, data))
        adb.run("push", injected, remote)
        if adb.run("shell", "sha256sum", remote).split()[0] != sha256(injected):
            raise RuntimeError("Uploaded backup hash mismatch")
        print("Approve RESTORE MY DATA on the phone (empty password).", flush=True)
        adb.run("shell", f"bu restore < {remote}", timeout=100)
        adb.run("shell", "cmd", "package", "unstop", PACKAGE)
        print("Approve BACK UP MY DATA again to verify the restored bytes.", flush=True)
        verified = folder / "verified-profile.adb"
        adb.run("exec-out", "bu", "backup", "-noapk", "-noshared", "-nokeyvalue", PACKAGE, output=verified, timeout=100)
        if extract_profile(verified.read_bytes()) != data:
            raise RuntimeError("Restored profile differs from prepared profile")
        print("Restored profile bytes match. Installing the target Google runtime.", flush=True)
        print(adb.run("install", "-r", apk.resolve(), timeout=180).strip())
        finish(adb, info)
    except (Exception, KeyboardInterrupt):
        # Recovery is APK-only, not restoration of previous private app data.
        recovery = previous or apk
        print(f"Setup failed. Attempting runtime recovery from {recovery}.", flush=True)
        try:
            print(adb.run("install", "-r", recovery.resolve(), timeout=180).strip())
        except Exception as error:
            print(f"Runtime recovery failed: {error}")
        raise
    finally:
        try:
            adb.run("shell", "rm", "-f", remote)
        except Exception as error:
            print(f"Temporary-file cleanup: {error}")
