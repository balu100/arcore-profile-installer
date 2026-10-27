# Device profile contributions

Add a JSON under `arcore_profile_installer/profiles/`, following `dash.json`.
These entries are optional overrides used only with `--mode device`. Do not
change the default generic asset when contributing a model-specific profile.

Required fields: `schema: 1`, `id`, exact `device` codename, `manufacturer`, and
`camera_overrides`. The override keys supported by this release are listed in
`profiles.py`. Unknown override fields fail validation. Device recognition does
not derive calibration from CPU model, marketing name or another phone's profile.

Include:

- Provenance for each changed value: sensor/camera metadata, coordinate system,
  active/cropped array sizes, stream size and units.
- Exact tested Android SDK, firmware and ARCore runtime versions.
- What was actually observed: profile load, camera start, VIO initialization,
  sustained tracking, and any remaining failures.
- Remaining generic fields and unmeasured calibration components.

Keep private device identifiers out of contributions. Do not submit APKs, `.adb`
archives, full dumps or binaries. A generic fallback may make a session start
without providing correct metric tracking; avoid "fully supported" claims.

Run `python -m unittest discover -s tests -v` before submitting changes. New
device support needs on-device confirmation from its owner; synthetic tests do
not establish hardware compatibility.
