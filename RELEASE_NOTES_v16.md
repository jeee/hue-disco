# Release Notes v16

## Fixed

- Removed the destructive `clear_legacy_yaml()` behavior from `apply_diyhue_mg21_current.py`. The installer now preserves and backs up `lights.yaml` and `groups.yaml` instead of overwriting them with `{}`.
- Aligned the MG21 monkeypatch path with the native entertainment runtime patch so `PUT /groups/<id>/action` with `{"stream":{"active":true}}` can synthesize a local `EntertainmentConfiguration`, populate lights and locations from native state, and start the local diyHue entertainment thread exactly once per inactive→active transition.

## Why this matters

These changes directly address the restart guidance in the incident notes: avoid further diyHue config corruption, keep the bridge bootable, and make stream activation drive the local listener instead of succeeding as a no-op.
