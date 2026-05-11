# Hue Disco Club Ansible Setup

Complete Ansible setup for Raspberry Pi with:

- DTLS over OpenSSL PSK for Hue Entertainment streaming
- `official_bridge` and `diyhue` backend modes
- diyHue + Zigbee2MQTT + Mosquitto support for local Zigbee-backed lights
- idempotent roles for the Python virtualenv, application, web UI, and systemd
- mobile-friendly web interface with separate control and admin access levels
- profile-based rendering with built-in and custom profiles
- profile light groups so different bulbs can behave differently while sharing one beat clock
- beat-locked strobe presets and smoother hybrid rendering

## Current package position

This package is intended as an installable Ansible baseline that preserves the known-good diyHue streaming path, keeps automatic runtime patching, keeps calibration mode as a dedicated timing/debug mode, and moves the rendering model toward profile-based control. It is more complete than the original handoff baseline, but it should still be treated as a serious integration build rather than as a field-proven final release.

It is explicit about what is mature versus what is still inherently experimental:

### Verified package capabilities

- MG21 startup no longer clears `lights.yaml` or `groups.yaml`; existing diyHue config is preserved and backed up instead.
- The MG21 native group-action monkeypatch now synthesizes a local diyHue entertainment group when needed and only starts the local entertainment thread on the inactive→active transition.
- diyHue entertainment streaming works through the package baseline.
- diyHue runtime patches are applied automatically on deployment/startup.
- `/status` returns a flat JSON object and `/api/state` remains available.
- BTrack backend is integrated as a third detector option and exposed in the Admin UI detector selector.
- Current BTrack runtime uses a downsampled ~22.05 kHz path with a shorter rolling buffer and throttled evaluation cadence to keep Raspberry Pi CPU load reasonable.
- Real-bridge bootstrap now falls back to CLIP v2 light discovery when v1 light listing fails, so entertainment-area light metadata can be repopulated after bridge/environment changes.
- Detector backends: `native`, `aubio`, `btrack`.
- Beat modes: `onset`, `phase_locked`.
- Grid behaviors: `continuous`, `gated`, `adaptive`.
- Render modes: `calibration`, `color_only`, `pulse_only`, `hybrid`.
- Intensity modes: `fixed`, `audio`, `adaptive`.
- Built-in profiles and custom profiles are supported.
- Profiles can contain multiple light groups with separate render settings.
- Multiple named strobe presets are supported, including `beat_locked` cadence.
- Control and Admin UI both expose the new profile/group and strobe concepts.

### Still inherently limited

- Beat detection is improved but still not guaranteed perfect on every genre or mix.
- `aubio` can still prefer half-time or double-time interpretations on some material.
- Some visible timing instability can still come from the Zigbee/Hue/diyHue path rather than from detection alone.
- Hard ON/OFF strobe remains a poor long-term visual fit for many Zigbee bulbs; hybrid and pulse-oriented rendering are usually better.

## Architecture summary

The system is now organized around three layers:

1. **Shared timing layer**
   - audio capture
   - onset / phase-locked beat timing
   - BPM estimate
   - shared beat grid

2. **Profile layer**
   - built-in or custom style
   - default render settings for the room
   - profile-level palette choices

3. **Light-group render layer**
   - one or more groups inside the active profile
   - each group can target different lights
   - each group can have different brightness, pulse, intensity, activity, and palette behavior
   - all groups still follow the same shared beat/timing model

That last point matters: lights should not drift into their own rhythm when a second bulb appears mid-stream.

## Profile and palette precedence

The package no longer treats a single global color constraint as the main styling mechanism.

Practical precedence is now:

1. per-light `allowed_colors` if present as a hard safety limit
2. active group `static_color` or `palette_colors`
3. active profile `static_color` or `palette_colors`
4. built-in internal fallback colors only if nothing else is configured

This means the active profile and its groups now define the room style in normal use. The old global palette is no longer intended as the main styling tool.

## Quick start

1. Copy `inventory.ini.example` to `inventory.ini`
2. Copy `group_vars/all.yml.example` to `group_vars/all.yml`
3. Set at least:
   - `hue.backend_mode`
   - `backend.zigbee2mqtt.serial_port`
   - your `hue.lights` list
4. Deploy:

```bash
ansible-playbook -i inventory.ini site.yml
```

After installation:

- Hue Disco UI: `http://<pi-ip>:8090`
- Zigbee2MQTT UI: `http://<pi-ip>:8099`
- service status: `sudo systemctl status hue-disco zigbee2mqtt diyhue`
- logs: `journalctl -u hue-disco -f`

## UI guide

### Control page

Use the Control page for:

- start / stop disco mode
- on / off
- switching the active profile
- triggering configured strobe presets
- checking live BPM / energy / flux / phase confidence

### Admin page

Use the Admin page for:

- detector backend and beat mode tuning
- BPM and beat-band tuning
- intensity defaults and render defaults
- building or editing profiles
- creating light groups inside profiles
- assigning lights to groups
- setting pulse envelope, pulse mix, color motion, group activity, palette, and group role
- creating named strobe presets

The Admin page is now a structured editor rather than only a raw JSON textarea, although it still exposes the generated JSON for inspection.

## diyHue + Zigbee2MQTT workflow

When `hue.backend_mode: diyhue` is enabled, the playbook does the following:

1. enables Docker and Mosquitto
2. deploys Zigbee2MQTT with Home Assistant MQTT discovery enabled
3. deploys diyHue and patches its MQTT settings to subscribe to discovery on `homeassistant`
4. calls the diyHue scan endpoint after the services are up
5. tries to register Hue API credentials locally
6. automatically patches diyHue entertainment handling on container start so no manual in-container edits are required
7. auto-discovers an Entertainment group, or creates one from configured lights when possible

## Pairing lights

1. Make sure your Zigbee adapter path is correct, preferably using `/dev/serial/by-id/...`
2. Open Zigbee2MQTT at `http://<pi-ip>:8099`
3. Temporarily allow joins and pair your bulbs
4. Confirm lights appear in Zigbee2MQTT
5. diyHue should ingest them after a scan

## Entertainment area creation

This build waits a bounded amount of time for diyHue to resolve configured lights from MQTT discovery, then creates an Entertainment group automatically once those lights exist.

If that still fails, pair the bulbs first, confirm they appear in Zigbee2MQTT, and rerun:

```bash
ansible-playbook -i inventory.ini site.yml
```

## Manual helpers

Re-run credential bootstrap:

```bash
sudo runuser -u hue -- /opt/hue-disco/.venv/bin/python /opt/hue-disco/bootstrap_hue_credentials.py /opt/hue-disco/config.yaml
```

For a real Hue bridge, you can leave `bridge_ip` empty and let bootstrap try automatic discovery first. If discovery does not work on your network, set `bridge_ip` explicitly and run bootstrap again.

Trigger a diyHue scan manually:

```bash
curl -s http://127.0.0.1/scan
```

## Detector notes

### Native

- lower dependency footprint
- simpler onset logic
- may still miss or misclassify some beats depending on material

### Aubio

- often stronger onset clues on some material
- still not immune to half-time/double-time ambiguity
- useful to compare against native during testing

## Calibration mode

Calibration mode remains intentionally simple:

- obvious white pulse per beat
- useful for checking visible timing
- not intended as the final club-light effect

Because it is a debug mode, its CPU cost is not representative of the final desired runtime profile.

## Why hybrid rendering exists

The project moved away from a single hard strobe style because timing and intensity needed to be decoupled and because Zigbee bulbs do not respond well to extreme hard ON/OFF strobing.

Hybrid mode therefore separates:

- **color layer**: what color motion is happening
- **pulse layer**: how hard the beat pushes brightness

This gives softer but still rhythmic visuals and works better with both fixed and audio/adaptive intensity.

## Documentation in this package

- `NEXT_CHAT_HANDOFF_PROMPT.md`
- `docs/FEATURE_SPEC_PROFILE_LIGHT_GROUPS.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/DIYHUE_UPSTREAM_PATCH.md`
- `docs/DIYHUE_GITHUB_BUGREPORT_entertainment-v2-parser-crash.md`
- `docs/ADDING_LIGHTS.md`

## Known practical observations preserved in the package README

- Lowering sample rate from 44100 to 22050 can reduce CPU, but may hurt beat detection depending on detector settings and block size.
- `aubio` can still lock around half-time on some ~120 BPM material.
- Some visible instability is likely in the Zigbee/Hue/diyHue path rather than the detector alone.
- New lights should still join the shared timing model rather than starting on their own independent event rhythm.

## Profile-first color workflow

The recommended workflow is now:

- use profiles to define the room mood
- use light groups inside a profile to split ambient/accent/background behavior
- use per-light `allowed_colors` only when a lamp should be hard-limited for safety, taste, or hardware reasons

In other words: color style should now live in the profile, not in one global room-wide color constraint.


## Settings backup and restore

The Admin page now includes **Download backup** and **Restore backup** actions.

- Backups are stored as a versioned JSON bundle rather than a raw config file copy.
- The bundle contains the current install's interface-editable settings, including profiles, light groups, strobe presets, detector settings, bridge settings, lights, and web credentials.
- Imports restore supported settings into the current schema, which makes the backup format more resilient across package upgrades.
- Backups may contain secrets such as passwords, session secrets, and bridge keys. Store them securely.

The runtime still keeps compatibility with older configs, but the backup/restore flow is now the recommended migration path when moving to a new install.


## Uninstall and reset

Use `uninstall.yml` for a clean reinstall.

Soft reset keeps diyHue bridge state:

```bash
ansible-playbook -i inventory.ini uninstall.yml
```

Full reset removes diyHue state too:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e reset_mode=full
```

## Native MG21 current-mode installer

This bundle includes a reproducible installer path for the current working MG21 setup.
It keeps diyHue light objects in place and reroutes light/group actions to a native MG21 daemon.

Key points:
- use `backend.native_mg21.enabled: true`
- set `backend.zigbee2mqtt.enabled: false`
- mount `${install_dir}/mg21-native` into the diyHue container
- patch diyHue at startup and start `mg21_daemon.py`
- MG21 code is included under `roles/hue_disco/files/mg21-native/`
- a nested archive is included as `roles/hue_disco/files/mg21-native-code.zip`


## v15 note

This archive supersedes v14 for MG21-native entertainment testing. See `RELEASE_NOTES_v15.md` and `LATEST_STATUS.md` first.


## Real Hue bridge registration note

If you set `backend_mode: official_bridge` and a correct `bridge_ip`, the app must still run bootstrap to create the Hue application key and client key. The updated package now attempts that bootstrap automatically for real bridges too and surfaces a clearer status message when the bridge returns the usual link-button-required error. When you see that message, press the physical button on top of the bridge and wait for the next retry.

As of the latest patch, Ansible also runs the same bootstrap step for `official_bridge` during deployment and prints the bootstrap stdout/stderr in the terminal, so you should no longer have to guess whether registration was attempted.
