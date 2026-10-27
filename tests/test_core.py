import copy
import io
import json
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from arcore_profile_installer.apk import apk_info, manifest_info
from arcore_profile_installer.backup import PREFIX, TARGET, extract_profile, inject, unpack
from arcore_profile_installer.device import ADB
from arcore_profile_installer.cli import main, select_profile
from arcore_profile_installer.installer import install_profile, prepare, update
from arcore_profile_installer.profiles import load_profile, registry, render_profile

TEST_TEMP = Path(__file__).resolve().parents[1] / ".arcore-state" / "tests"
TEST_TEMP.mkdir(parents=True, exist_ok=True)

BASE = '''# Synthetic fixture; not a Google asset.
cameras {
  camera_id: "0"
  rolling_shutter_readout_time_nanoseconds: 33333333
  calibrated_focal_length { x: 512.0 y: 512.0 }
  calibrated_principal_point { x: 320.0 y: 240.0 }
  camera_timebase_trust_level: RUNTIME_CHECK_REQUIRED
}
cameras {
  camera_id: "1"
  calibrated_focal_length { x: 512.0 y: 512.0 }
}
'''


def archive(extra=()):
    output = io.BytesIO()
    entries = [(PREFIX + "_manifest", b"1\ncom.google.ar.core\n181112096\n36\n\n0\n1\nTEST_CERT\n"), *extra]
    with tarfile.open(fileobj=output, mode="w:", format=tarfile.USTAR_FORMAT) as tar:
        for name, body in entries:
            member = tarfile.TarInfo(name)
            member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    return b"ANDROID BACKUP\n5\n1\nnone\n" + zlib.compress(output.getvalue())


def binary_manifest(package="com.google.ar.core"):
    names = ["manifest", "package", package, "versionCode", "versionName", "1.57.test", "uses-sdk", "minSdkVersion", "targetSdkVersion"]
    encoded, offsets = b"", []
    for name in names:
        offsets.append(len(encoded))
        text = name.encode()
        encoded += bytes([len(name), len(text)]) + text + b"\0"
    pool_size = 28 + 4 * len(names) + len(encoded)
    padding = -pool_size % 4
    pool_size += padding
    pool = struct.pack("<HHIIIIII", 1, 28, pool_size, len(names), 0, 0x100, 28 + 4 * len(names), 0)
    pool += struct.pack("<" + "I" * len(offsets), *offsets) + encoded + b"\0" * padding

    def element(name, attrs):
        values = b""
        for key, value, kind in attrs:
            values += struct.pack("<IIIHBBI", 0xffffffff, names.index(key), 0xffffffff, 8, 0, kind,
                                  names.index(value) if kind == 3 else value)
        node = struct.pack("<HHIII", 0x102, 16, 36 + len(values), 1, 0xffffffff)
        ext = struct.pack("<IIHHHHHH", 0xffffffff, names.index(name), 20, 20, len(attrs), 0, 0, 0)
        return node + ext + values

    body = pool + element("manifest", [("package", package, 3), ("versionCode", 262640093, 0x10), ("versionName", "1.57.test", 3)])
    body += element("uses-sdk", [("minSdkVersion", 24, 0x10), ("targetSdkVersion", 37, 0x10)])
    return struct.pack("<HHI", 3, 8, 8 + len(body)) + body


class CoreTests(unittest.TestCase):
    def test_generic_mode_never_selects_registered_dash_overrides(self):
        self.assertIsNone(select_profile("generic", registry()))

    def test_device_mode_requires_known_device_or_external_profile(self):
        with self.assertRaisesRegex(ValueError, "registered"):
            select_profile("device", registry(), "unknown-phone")
        self.assertEqual(select_profile("device", registry(), "dash")["device"], "dash")

    def test_generic_rejects_accidental_device_flags(self):
        with self.assertRaisesRegex(ValueError, "--mode device"):
            select_profile("generic", registry(), "dash")

    def test_generic_mode_supports_unregistered_arm64_device(self):
        adb = object.__new__(ADB)
        adb.device, adb.manufacturer = "another-model", "AnotherVendor"
        adb.run = Mock(side_effect=["0\n", "arm64-v8a,armeabi-v7a\n"])
        adb.bind(None)
        self.assertEqual(adb.run.call_count, 2)

    @patch("arcore_profile_installer.cli.ADB")
    @patch("arcore_profile_installer.cli.fetch_latest")
    def test_cli_defaults_to_exact_generic_asset_without_phone(self, fetch, adb):
        base = BASE.replace("\n", "\r\n").encode("utf-8")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            tmp = Path(tmp)
            apk = tmp / "original.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", binary_manifest())
                archive.writestr("lib/arm64-v8a/libarcore_c.so", b"synthetic fixture")
                archive.writestr("assets/device_profile_max_compatibility.textproto", base)
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["prepare", "--apk", str(apk), "--work-dir", str(tmp / "work")])
            self.assertEqual(code, 0)
            folder = next((tmp / "work" / "runs").iterdir())
            self.assertEqual((folder / "custom_device_profile.textproto").read_bytes(), base)
            info = json.loads((folder / "prepared.json").read_text())
            self.assertEqual(info["mode"], "generic")
            self.assertIsNone(info["device"])
            self.assertEqual(info["profile_sha256"], info["base_asset_sha256"])
            # The same APK can explicitly receive the device overrides.
            custom = prepare(apk, registry()["dash"], tmp / "device")
            self.assertNotEqual(custom, base)
            self.assertIn(b"430.928115845", custom)
        fetch.assert_not_called()
        adb.assert_not_called()

    def test_registry_only_claims_dash_experimental(self):
        self.assertEqual(set(registry()), {"dash"})
        self.assertEqual(registry()["dash"]["status"], "experimental")

    def test_profile_changes_rear_only_and_preserves_comments(self):
        result = render_profile(BASE, registry()["dash"])
        self.assertIn("x: 430.928115845", result)
        self.assertIn("25436160", result)
        self.assertIn("# Synthetic fixture", result)
        self.assertEqual(result.split('camera_id: "1"')[1], BASE.split('camera_id: "1"')[1])

    def test_changed_base_format_stops_preparation(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            render_profile(BASE.replace("camera_timebase_trust_level", "removed_field"), registry()["dash"])

    def test_duplicate_camera_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            render_profile(BASE + 'cameras { camera_id: "0" }', registry()["dash"])

    def test_invalid_override_values(self):
        for key, value in [("calibrated_focal_length.x", -1), ("calibrated_focal_length.x", float("nan")),
                           ("camera_timebase_trust_level", "GUESS"), ("arbitrary.path", 1)]:
            profile = copy.deepcopy(registry()["dash"])
            profile["camera_overrides"]["0"][key] = value
            with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
                file = Path(tmp) / "profile.json"
                file.write_text(json.dumps(profile), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_profile(file)

    def test_backup_inject_and_verify(self):
        data = b"profile contents\n"
        result = inject(archive(), data)
        self.assertEqual(extract_profile(result), data)
        self.assertEqual(unpack(result)[2], 181112096)

    def test_existing_profile_replaced_once(self):
        result = inject(archive([(TARGET, b"old")]), b"new")
        self.assertEqual(extract_profile(result), b"new")
        self.assertEqual(sum(member.name == TARGET for member, _ in unpack(result)[1]), 1)

    def test_empty_backup_rejected(self):
        raw = b"ANDROID BACKUP\n5\n1\nnone\n" + zlib.compress(b"\0" * 1024)
        with self.assertRaisesRegex(ValueError, "Missing"):
            unpack(raw)

    def test_foreign_traversal_and_duplicate_entries_rejected(self):
        for entries in [[("apps/other/f/x", b"x")], [(PREFIX + "f/../../x", b"x")],
                        [(TARGET, b"one"), (TARGET, b"two")]]:
            with self.assertRaises(ValueError):
                unpack(archive(entries))

    def test_encrypted_backup_rejected(self):
        with self.assertRaisesRegex(ValueError, "unencrypted"):
            unpack(archive().replace(b"none\n", b"AES-256\n", 1))

    def test_axml_metadata(self):
        info = manifest_info(binary_manifest())
        self.assertEqual(info["package"], "com.google.ar.core")
        self.assertEqual(info["versionCode"], 262640093)
        self.assertEqual(info["minSdkVersion"], 24)

    def test_wrong_apk_package_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            apk = Path(tmp) / "wrong.apk"
            with zipfile.ZipFile(apk, "w") as z:
                z.writestr("AndroidManifest.xml", binary_manifest("com.example.other"))
                z.writestr("lib/arm64-v8a/libarcore_c.so", b"synthetic fixture")
            with self.assertRaisesRegex(ValueError, "standalone"):
                apk_info(apk)

    @patch("arcore_profile_installer.device.shutil.which", return_value="adb")
    @patch("arcore_profile_installer.device.subprocess.run")
    def test_device_selection_ignores_watch_when_known_phone_present(self, run, _):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "List of devices attached\nWATCH device device:blenny\nPHONE device device:dash\n", ""),
            subprocess.CompletedProcess([], 0, b"dash\n", b""),
            subprocess.CompletedProcess([], 0, b"Xiaomi\n", b""),
        ]
        adb = ADB({"dash"})
        self.assertEqual(adb.serial, "PHONE")
        self.assertEqual(adb.command("install", "-r", "a.apk")[:3], ["adb", "-s", "PHONE"])

    def test_wrong_device_rejected_before_adb_commands(self):
        adb = object.__new__(ADB)
        adb.device, adb.manufacturer = "other", "Xiaomi"
        adb.run = Mock()
        with self.assertRaisesRegex(ValueError, "match"):
            adb.bind(registry()["dash"])
        adb.run.assert_not_called()

    @patch("arcore_profile_installer.installer.apk_info", return_value={"versionCode": 2, "minSdkVersion": 24})
    def test_downgrade_refused_before_snapshot(self, _):
        adb = Mock()
        adb.installed.return_value = {"versionCode": 3}
        with self.assertRaisesRegex(ValueError, "downgrade"):
            update(adb, Path("target.apk"), registry()["dash"], Path("unused"))
        adb.run.assert_not_called()

    @patch("arcore_profile_installer.installer.sha256", return_value="bad")
    @patch("arcore_profile_installer.installer.validate_target", return_value=({"versionCode": 262640093}, None))
    def test_wrong_staging_hash_refused_before_any_uninstall(self, *_):
        adb = Mock()
        with self.assertRaisesRegex(ValueError, "pinned"):
            install_profile(adb, Path("target.apk"), Path("old.apk"), registry()["dash"], Path("unused"))
        adb.run.assert_not_called()

    @patch("arcore_profile_installer.installer.snapshot", return_value=Path("before.apk"))
    @patch("arcore_profile_installer.installer.prepare", return_value=b"expected profile")
    @patch("arcore_profile_installer.installer.sha256")
    @patch("arcore_profile_installer.installer.apk_info", return_value={"versionCode": 181112096})
    @patch("arcore_profile_installer.installer.validate_target", return_value=({"versionCode": 262640093}, {"versionCode": 262080393}))
    def test_restore_verification_failure_recovers_runtime(self, _, _info, sha, _prepare, _snapshot):
        from arcore_profile_installer.installer import STAGE_SHA
        sha.side_effect = [STAGE_SHA, "uploaded"]
        adb = Mock()
        calls = []

        def command(*args, output=None, **kwargs):
            calls.append(args)
            if args[:2] == ("shell", "test -x /system/bin/bu && echo present || true"):
                return "present\n"
            if args == ("shell", "cmd", "package", "help"):
                return "unstop PACKAGE"
            if args[:2] == ("shell", "sha256sum"):
                return "uploaded  remote.adb"
            if output:
                # Initial backup is valid, but the verification backup lacks
                # the requested profile: setup must not report success.
                output.write_bytes(archive())
            return "Success\n"

        adb.run.side_effect = command
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            with self.assertRaisesRegex(ValueError, "profile missing"):
                install_profile(adb, Path("target.apk"), Path("stage.apk"), registry()["dash"], Path(tmp))
        installs = [args for args in calls if args[0] == "install"]
        self.assertTrue(any(Path("before.apk").resolve() in args for args in installs))
        self.assertFalse(any(Path("target.apk").resolve() in args for args in installs))


if __name__ == "__main__":
    unittest.main()
