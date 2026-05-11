# MG21 current install baseline (v13)

This installer is the **fresh-install baseline** for the current diyHue + MG21 native integration.

## What this installer is intended to reproduce

After a clean install, the package should reproduce the **working MG21 integration baseline** that was verified on the live Raspberry Pi:

- MG21 native Zigbee discovery works.
- Native lights are exposed through diyHue API compatibility mode.
- Direct light control works through the MG21 backend.
- Direct group control works through the MG21 backend.
- Command-line entertainment control works through the MG21 backend.
- The diyHue Lights and Groups pages render correctly with the MG21-backed compatibility payloads.

## What this installer does **not** claim to fix yet

This package is **not** the final Hue Disco fix.

Known unresolved items:

- Hue Disco is still in a broken / inconsistent state and is the next debug target.
- `mg21_daemon.py` still has to be started manually after every container restart.
- The coordinator still appears as a light.
- Human-friendly lamp naming is still not solved.

## Required Ansible settings

Set these in `group_vars/all.yml`:

```yaml
hue:
  backend_mode: diyhue
  entertainment_group_id: "1"

backend:
  native_mg21:
    enabled: true
    serial_port: /dev/serial/by-id/YOUR_MG21_ADAPTER
    baudrate: 115200
    channel: 20
  zigbee2mqtt:
    enabled: false
```

Then run:

```bash
ansible-playbook -i inventory.ini site.yml
```

## Post-install runtime note

Until daemon auto-start is productized, the MG21 daemon still needs to be started manually after Docker restarts:

```bash
sudo docker exec diyhue sh -lc '/opt/mg21-venv/bin/python /opt/hue-emulator/ext/mg21-native/mg21_daemon.py >/tmp/mg21-daemon.log 2>&1 & echo $!'
```

Verify:

```bash
curl -fsS http://127.0.0.1:9123/health
```

Expected:

```json
{"ok": true, "backend": "NativeMG21Backend"}
```

## Included state references

For the exact live-state notes that this installer was aligned to, see:

- `docs/MG21_EXTENSIVE_HANDOFF_v13.md`
- `docs/raw_state/mg21_native_backend_handoff_live.md`
- `docs/raw_state/mg21_current_state.diff`
- `docs/raw_state/mg21_native_db_state.txt`


## 2026-04-20 current experimental state
- Real bridge streaming path uses CLIP v2 entertainment_configuration lookup and `action=start`.
- DTLS reconnect kills stale openssl clients before reconnect.
- Current calibration stack includes two-trigger lock promotion, bass-supported trigger gating, and live diagnostics.
- Current install is still experimental for beat sync; line-in validation is preferred over microphone-based tuning.


## 2026-04-21 current experimental state
- Real bridge streaming path uses CLIP v2 entertainment_configuration lookup and `action=start`.
- DTLS reconnect kills stale openssl clients before reconnect.
- Detector backends now include `native`, `aubio`, and `btrack`.
- Current preferred live test path is `btrack` with a downsampled ~22.05 kHz analysis path, 2.0 s rolling buffer, and 0.50 s BTrack evaluation interval.
- Admin UI detector selector now exposes `BTrack` directly; command-line YAML edits are no longer required just to switch detector backend.
- Bootstrap light discovery now needs the CLIP v2 fallback because the bridge v1 light listing can fail on HTTPS certificate verification; without that fallback, `lights` can remain null even though the entertainment group is found.
- Entertainment-area membership refresh is still bootstrap-driven rather than continuously automatic once the app is already configured; if the bridge area changes, rerun bootstrap or redeploy.
- Current install is still experimental for beat sync feel; line-in validation and video validation are preferred over microphone-based tuning.
