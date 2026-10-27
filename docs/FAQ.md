# Frequently asked questions

## Is this an APK patcher?

It installs a custom profile through a legacy Android backup/restore workflow.
The Google runtime APK is not rewritten or re-signed. There is no Morphe patch
bundle in this release.

## Does it make every phone ARCore-compatible?

No. Generic mode can be attempted on an unregistered ARM64 model, but a working
session depends on the phone, ROM, staging backend and runtime. The tool does
not generate calibrated camera/IMU extrinsics or grant Google certification.

## Is the generic profile the POCO profile?

No. Generic mode copies Google's compatibility asset unchanged from the target
APK. POCO `dash` values are applied only with `--mode device`. A profile from
one phone is not a universal calibration for other phones.

## Which is better: generic or device mode?

Generic is a reproducible starting point. A measured device override can
improve specific fields on that exact model, but does not make all remaining
fields accurate. The bundled dash override adjusts rear-camera metadata;
its IMU calibration and front camera remain generic. Read each profile's
provenance and limitations rather than treating the label as a quality score.

## Do I need root, Shizuku, Android Studio or firmware dumps?

Not for this installer on a compatible setup. You need Python, ADB, the supplied
staging APK for initial setup, and working on-device backup/restore prompts.
Shizuku alone cannot provide a missing calibration profile.

## Why do I need such an old staging APK?

The workflow uses the old runtime's backup behavior to restore a custom profile,
then updates to the current original runtime. The staging APK is a temporary
installation step. A fresh backup and a second byte verification avoid assuming
that an old archive will restore correctly on a different phone.

## The backup is empty or no confirmation screen appears. What next?

Keep the primary Android user unlocked and confirm the phone's backup/restore
UI, leaving its backup password empty. An empty or encrypted archive is rejected.
If the ROM blocks this backend, this installer has no alternate rootless path.
Check the console transcript for runtime recovery status before retrying.

## The Play Store says ARCore is incompatible. Is that a bug here?

Installing a custom profile does not change Google certification or the ROM's
declared `android.hardware.camera.ar` feature. The Play Store compatibility
message may remain. Check a real runtime session with `verify`.

## Why does the installed version differ from the SDK release number?

The SDK release tag, APK `versionName` and APK `versionCode` use different formats.
The installer reports the actual APK version through ADB. The latest SDK-release
runtime also need not match the latest Play Store rollout.

## What happens when I run update?

The current APK is saved locally, then the target APK is installed with
`install -r`. The existing app data and custom profile are preserved. The
command does not convert a device profile back to generic mode. Re-test after
an update because future runtimes may change custom-profile behavior.

## Can I use an APK bundle or a different architecture?

This release expects a standalone ARM64 Google runtime APK. Split installations,
APKM/APKS bundles, x86 and 32-bit-only staging are not implemented.

## What should I attach to a bug report?

Include the command, relevant error, phone codename, Android/ROM version and
runtime version. Share short, redacted excerpts instead of full dumps. Do not
upload APKs, Android backups, serial numbers or location data.
