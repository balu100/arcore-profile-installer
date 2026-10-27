# Changelog

## 0.1.0 — 2026-10-28

Initial experimental release.

- Generic-first profile preparation from the original Google runtime asset.
- Optional model-matched JSON overrides; includes measured POCO `dash` rear-camera metadata.
- Legacy backup/restore profile installation with pinned staging APK and byte verification.
- Official SDK-release runtime downloads with published digest/size checks.
- Data-preserving updates, APK-only recovery attempts and ADB version reporting.
- Filtered runtime evidence reports and local per-run records.
- Offline tests and Windows/Linux packaging checks.

The generalized installer has not been destructively re-tested on the phone
during packaging. Its underlying technique was observed in the preceding
dash-specific workflow. See [VALIDATION.md](VALIDATION.md).
