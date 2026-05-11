# v14 package release notes

This package is a corrected follow-up to the v13 MG21 baseline.

## Fixed in the archive

- Native MG21 is now the default backend in `group_vars/all.yml.example`.
- Zigbee2MQTT is disabled by default in the shipped example config.
- `hue.entertainment_group_id` now defaults to `"1"` instead of an empty string.
- `diyhue.service` no longer deletes the diyHue container on stop.
- `patch_diyhue_config.py` now hardens empty entertainment group IDs and can patch JSON as well as YAML config files.
- `apply_diyhue_mg21_current.py` was hardened so it no longer depends on one exact import anchor in `restful.py` or `views.py`.
- The diyHue native light PUT path was changed to handle native MG21 lights before touching legacy `bridgeConfig["lights"]`.
- Native light references are now normalized toward raw IEEE values before calling `/light_state`.
- Hue Disco bootstrap now filters out likely coordinator pseudo-lights during light sync.

## Still not fully proven inside this archive alone

- Native diyHue `/groups` creation against all diyHue image variants still needs live validation.
- Native `/group_action` and end-to-end Hue Disco entertainment should be re-tested on hardware after deployment.

## Recommended validation after install

1. Confirm `backend.native_mg21.enabled: true`.
2. Confirm `backend.zigbee2mqtt.enabled: false`.
3. Confirm `curl http://127.0.0.1:9123/health`.
4. Pair a bulb and verify `/api/<user>/lights` returns native lights.
5. Verify `PUT /api/<user>/lights/<id>/state` works for a native light.
6. Verify entertainment group creation/discovery and then test Hue Disco effects.
