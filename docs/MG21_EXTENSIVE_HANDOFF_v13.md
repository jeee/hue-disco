# MG21 native backend — extensive handoff for v13 installer

This document is the handoff for the **ansible installer baseline**, not a claim that Hue Disco itself is fixed.

## Package intent

This installer packages the currently known-good **MG21 integration baseline** from the live Raspberry Pi work.

The goal of this package is to let a fresh install start from a state where:

- diyHue runs with MG21 native Zigbee integration enabled
- native lights/groups are surfaced through diyHue compatibility mode
- direct light and group control work through MG21
- command-line entertainment control works through MG21
- the next debugging chat can focus on **Hue Disco only**, instead of rebuilding the MG21 integration again

## Current truth boundary

### Considered working for this package

- MG21 discovery
- diyHue compatibility payload shaping for native lights/groups
- direct light control routed to MG21
- direct group control routed to MG21
- entertainment area creation / stream activation plumbing
- command-line entertainment path through `/entertainment_frame`

### Explicitly not considered solved yet

- Hue Disco runtime behavior
- automatic daemon startup after every Docker restart
- coordinator suppression
- final naming / UX polish

## Live-state files that informed this package

The following live-state artifacts were used as the package target:

- `docs/raw_state/mg21_native_backend_handoff_live.md`
- `docs/raw_state/mg21_current_state.diff`
- `docs/raw_state/mg21_native_db_state.txt`

## Effective MG21 file changes represented in this package

### 1. Installed MG21 Python package behavior

Patched areas reflected from the live system:

- `diyhue_native_zigbee/adapters/mg21_bellows.py`
  - endpoint selection uses `in_clusters`
  - capability inference uses `in_clusters`
  - unicast dispatch uses `_get_in_cluster(...)`

- `diyhue_native_zigbee/core/service.py`
  - scheduler and entertainment injection use explicit `is not None`
  - avoids the falsy replacement bug with an empty scheduler

- `diyhue_native_zigbee/core/diyhue_bridge.py`
  - richer diyHue compatibility payloads for lights and groups
  - adds diyHue-like fields required by the web UI
  - keeps MG21 lights visible in diyHue compatibility mode

### 2. diyHue runtime patching represented by `apply_diyhue_mg21_current.py`

The installer helper writes / patches the following inside the running diyHue container:

- `/opt/hue-emulator/ext/mg21-native/bridge_runtime.py`
  - `native_backend_enabled(...)`
  - `get_native_state_sync(...)`
  - `handle_group_action_sync(...)`
  - `permit_join_sync(...)`
  - `handle_light_state_sync(...)`

- `/opt/hue-emulator/ext/mg21-native/mg21_daemon.py`
  - `/health`
  - `/state`
  - `/group_action`
  - `/light_state`
  - `/entertainment_frame`
  - `/permit_join`

- `/opt/hue-emulator/flaskUI/restful.py`
  - merges native lights/groups into diyHue compatibility responses
  - preserves group `0`
  - reroutes direct light state writes to MG21
  - reroutes group actions to MG21
  - preserves permit-join path

- `/opt/hue-emulator/flaskUI/core/views.py`
  - `/lights` returns native lights when native mode is enabled

- `/opt/hue-emulator/services/entertainment.py`
  - MQTT-protocol diyHue lights are rerouted to MG21 `/entertainment_frame` when native MG21 mode is active

### 3. Config cleanup performed by installer helper

The installer clears stale diyHue-managed Zigbee2MQTT resources:

- `/opt/hue-emulator/config/lights.yaml`
- `/opt/hue-emulator/config/groups.yaml`

These are reset to `{}` to avoid conflicts with MG21-backed resources.

## Important live-state DB detail

The live working MG21 state used this effective native DB membership:

### `groups_tbl`

```text
(1, 'Hue Disco Area', '["2", "3", "4"]')
```

### `entertainment_areas`

```text
('1', 'Hue Disco Area', 1, '38efb94a366e11f1aa59dca63255b293',
 '[{"light_id":"2","x":0.0,"y":0.0,"z":0.0},
   {"light_id":"3","x":1.0,"y":0.0,"z":0.0},
   {"light_id":"4","x":2.0,"y":0.0,"z":0.0}]')
```

This is preserved in the raw state files for debugging.

## Why this package is useful even though Disco is still broken

Because it removes the need to rediscover or rebuild the MG21 integration layer in the next chat.

That next debugging session can assume:

- direct native MG21 light color works
- direct native MG21 group control works
- `/entertainment_frame` exists and accepts frames
- remaining work is in the Disco path or in its interaction with the daemon/runtime state

## Known caveats to carry into the next chat

1. `mg21_daemon.py` still needs manual startup after container restart.
2. The coordinator still appears as a light.
3. Lamp names are still low-quality / raw device-based names.
4. Hue Disco may still only toggle stream active/inactive without driving visible output.
5. If entertainment stops working after restart, verify daemon health first:

```bash
curl -fsS http://127.0.0.1:9123/health
```

## Minimum post-install verification steps

After install on a fresh system:

1. Confirm diyHue container is healthy.
2. Start `mg21_daemon.py` manually.
3. Verify `/health` on port `9123`.
4. Confirm direct per-light control works.
5. Confirm direct `xy` color control works.
6. Confirm group action and stream activation work.
7. Only then start debugging Hue Disco.

## Suggested clean-chat opening summary

Use this package as the baseline. Do not spend time rebuilding MG21 integration from scratch.
Assume the installer already includes:

- MG21 adapter fixes
- scheduler falsy bug fix
- diyHue compatibility payload shaping
- daemon `/state`, `/light_state`, `/entertainment_frame`
- diyHue runtime patching to route native control through MG21

Focus next on why Hue Disco still produces no visible effect from this baseline.
