# Validation record

Initial preparation: 2026-10-27.

- 22 offline unit tests passed with Python 3.14 on Windows.
- Generic is now the default mode. Both the CLI fixture test and a preparation
  against the actual Google 1.57 APK verified that the generic output is
  byte-identical to that APK's own compatibility asset, including newlines.
- Optional device overrides require `--mode device`. Tests verify that generic
  mode never selects dash overrides and that device mode rejects unknown models.
- Generic mode's capability check accepts an unregistered ARM64 model in a mock
  test. This is not a claim of on-device tracking compatibility for that model.
- Profile rendering against the actual official Google ARCore 1.57 APK was
  compared semantically with the previously used dash v1 profile; all parsed
  scalar values and repeated-field ordering matched.
- The generalized CLI's read-only `status` command selected the connected
  Xiaomi dash correctly while a watch was also connected and reported ARCore
  1.57.262640093.
- The backup codec tests cover injection, replacement, verification, empty
  archives, encryption rejection, foreign package paths, traversal and duplicates.
- A mocked setup failure after restore verification confirmed that a recovery
  runtime installation is attempted and target setup is not reported successful.

The generalized `install-profile` command has **not** been rerun destructively on
the phone during this packaging pass. Its underlying 1.6 → backup/restore → modern
runtime technique worked in the preceding dash-specific workflow. No additional
device model, Android release, ROM or CPU architecture is claimed tested.

The CI workflow is included for future Windows/Linux checks; hosted CI has not
run until this source is pushed to GitHub.
