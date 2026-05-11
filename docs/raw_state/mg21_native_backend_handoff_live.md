# MG21 Native Backend Integration — Current Working State Handoff

This document captures the **current working state** of the diyHue + MG21 native Zigbee integration, including:

- what was changed
- which files are currently modified
- the exact behavior that now works
- the remaining known issues
- the exact command to generate a diff file with the installed changes

---

## Current working behavior

The system now does all of the following:

- discovers Zigbee bulbs through the MG21 native backend
- classifies the Philips bulbs as `Extended color light`
- supports entertainment streaming end-to-end
- exposes native lights and groups through diyHue API responses
- renders the diyHue **Lights** page correctly
- renders the diyHue **Groups** page correctly
- uses the **native backend** as the effective source of truth for lights/groups in diyHue API compatibility mode

What is **still not fixed**:

- the coordinator (`Silicon Labs EZSP`) still appears as a light
- lamp names are still raw manufacturer/model names
- `mg21_daemon.py` still has to be started manually after every container restart

---

## Files currently changed

These are the files that are currently modified relative to the original running container state:

```text
/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/adapters/mg21_bellows.py
/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/service.py
/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/diyhue_bridge.py
/opt/hue-emulator/ext/mg21-native/mg21_daemon.py
/opt/hue-emulator/ext/mg21-native/bridge_runtime.py
/opt/hue-emulator/flaskUI/restful.py
/opt/hue-emulator/flaskUI/core/views.py
/opt/hue-emulator/config/lights.yaml
/opt/hue-emulator/config/groups.yaml
/opt/hue-emulator/config/native_backend.sqlite
```

---

## What was changed

### 1. MG21 adapter changes
File:
`/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/adapters/mg21_bellows.py`

Changes:
- endpoint selection changed from checking `out_clusters` to checking `in_clusters`
- capability inference changed from `out_clusters` to `in_clusters`
- unicast command dispatch changed from `_get_out_cluster(...)` to `_get_in_cluster(...)`

Why:
- the bulbs expose controllable clusters in `in_clusters`
- without this, entertainment dispatch failed with `KeyError: 6`

---

### 2. Scheduler bug fix
File:
`/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/service.py`

Changes:
- fixed falsy scheduler replacement bug
- fixed falsy entertainment replacement bug

Effective change:
```python
self._scheduler = scheduler if scheduler is not None else RealtimeScheduler(...)
self._entertainment = entertainment if entertainment is not None else EntertainmentService(...)
```

Why:
- `RealtimeScheduler` defines `__len__`
- an empty scheduler is falsy
- the old code silently replaced the passed scheduler with a new one
- that caused frames to be queued into one scheduler and flushed from another

Result:
- entertainment frames now flush correctly

---

### 3. Native → diyHue compatibility shaping
File:
`/opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/diyhue_bridge.py`

Changes:
- added helper `_archetype_for_light(...)`
- added helper `_productid_for_light(...)`

Light payload now includes:
- `productid`
- `swversion`
- richer `config`:
  - `archetype`
  - `function`
  - `direction`
  - `startup`
- `protocol`
- `protocol_cfg.ip`
- `protocol_cfg.uid`
- `uniqueid`

Group payload now includes:
- `sensors`
- `recycle`
- full default `action`
- entertainment-specific fields:
  - `class`
  - `configuration_type`
  - `locations`
  - `stream.proxymode`
  - `stream.proxynode`
  - `stream.active`
  - `stream.owner`

Why:
- diyHue UI expected legacy diyHue-style fields
- lights page crashed earlier on missing `protocol_cfg.ip`
- icons were poor without `config.archetype`
- group page needed richer group payload structure

---

### 4. MG21 daemon changes
File:
`/opt/hue-emulator/ext/mg21-native/mg21_daemon.py`

Changes:
- added `GET /state` endpoint

`/state` returns:
- `lights`
- `groups`
- `metrics`

Why:
- used as an internal inspection endpoint
- used by diyHue compatibility layer via `bridge_runtime.py`

Note:
- temporary debug fields were added earlier and later removed
- current file only retains the real `/state` route change

---

### 5. Bridge runtime helper
File:
`/opt/hue-emulator/ext/mg21-native/bridge_runtime.py`

Changes:
- added helper:
```python
def get_native_state_sync(config_path: str) -> dict[str, Any]:
    return _get("/state")
```

Why:
- allows diyHue Flask layer to fetch native MG21 state synchronously

---

### 6. diyHue API merge logic
File:
`/opt/hue-emulator/flaskUI/restful.py`

Changes:
- import now includes `get_native_state_sync`
- `ResourceElements.get()` merges native `lights` and `groups`
- `EntireConfig.get()` merges native `lights` and `groups`
- `EntireConfig.get()` preserves **group 0** in the full config response

Why:
- diyHue web UI uses `GET /api/<user>` as its main data source
- native lights/groups had to be merged into that payload
- group page broke when group `0` disappeared from the full config response

Key final logic:
```python
if resource == "groups" or resource_id != "0":
    result[resource][resource_id] = bridgeConfig[resource][resource_id].getV1Api().copy()
...
result["lights"].update(native.get("lights") or {})
result["groups"].update(native.get("groups") or {})
```

---

### 7. diyHue lights endpoint
File:
`/opt/hue-emulator/flaskUI/core/views.py`

Changes:
- `/lights` now returns native lights when native backend mode is enabled

Current logic:
```python
def get_lights():
    cfg_path = "/opt/hue-emulator/config/config.yaml"
    if native_backend_enabled(cfg_path):
        native = get_native_state_sync(cfg_path) or {}
        return native.get("lights", {})
    ...
```

Important:
- a temporary `_native_ui_lights()` helper was tried and later removed
- current final state does **not** include that helper

---

### 8. Cleared stale Zigbee2MQTT diyHue state
Files:
- `/opt/hue-emulator/config/lights.yaml`
- `/opt/hue-emulator/config/groups.yaml`

Current contents:
```yaml
{}
```

Why:
- removes old Zigbee2MQTT diyHue-managed light/group objects
- prevents legacy MQTT resources from conflicting with native MG21-backed resources

---

### 9. Native backend DB fixes
File:
`/opt/hue-emulator/config/native_backend.sqlite`

Changes:
- fixed native group membership
- fixed entertainment area membership format

Current effective values:

`groups_tbl.members_json` for group `1`:
```json
["2","3","4"]
```

`entertainment_areas.members_json` for area `1`:
```json
[
  {"light_id":"2","x":0.0,"y":0.0,"z":0.0},
  {"light_id":"3","x":1.0,"y":0.0,"z":0.0},
  {"light_id":"4","x":2.0,"y":0.0,"z":0.0}
]
```

Why:
- the native entertainment group originally targeted the wrong members
- this fixed streaming to the actual three bulbs

---

## Current required runtime step

After every `docker restart`, the MG21 daemon still has to be started manually.

Use:

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

---

## Exact command to create a diff file with all installed code changes

This command writes a **single diff file** on the Raspberry Pi host at:

`/home/pi/mg21_current_state.diff`

It compares each modified file against the newest `.bak.*` backup that exists beside it.

```bash
sudo sh -lc '
OUT=/home/pi/mg21_current_state.diff
: > "$OUT"
for f in \
  /opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/adapters/mg21_bellows.py \
  /opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/service.py \
  /opt/mg21-venv/lib/python3.13/site-packages/diyhue_native_zigbee/core/diyhue_bridge.py \
  /opt/hue-emulator/ext/mg21-native/mg21_daemon.py \
  /opt/hue-emulator/ext/mg21-native/bridge_runtime.py \
  /opt/hue-emulator/flaskUI/restful.py \
  /opt/hue-emulator/flaskUI/core/views.py
do
  bak=$(ls -1t "$f".bak.* 2>/dev/null | head -n1)
  {
    echo "===== $f ====="
    echo "BACKUP: $bak"
    if [ -n "$bak" ]; then
      diff -u "$bak" "$f" || true
    else
      echo "No backup found"
    fi
    echo
  } >> "$OUT"
done
echo "Wrote $OUT"
ls -l "$OUT"
'
```

If you also want the YAML differences appended into the same file, run this after the previous command:

```bash
sudo sh -lc '
OUT=/home/pi/mg21_current_state.diff
{
  echo "===== /opt/hue-emulator/config/lights.yaml ====="
  bak=$(ls -1t /opt/hue-emulator/config/lights.yaml.pre-native-cleanup.* /opt/hue-emulator/config/lights.yaml.bak.* 2>/dev/null | head -n1)
  echo "BACKUP: $bak"
  [ -n "$bak" ] && diff -u "$bak" /opt/hue-emulator/config/lights.yaml || true
  echo
  echo "===== /opt/hue-emulator/config/groups.yaml ====="
  bak=$(ls -1t /opt/hue-emulator/config/groups.yaml.pre-native-cleanup.* /opt/hue-emulator/config/groups.yaml.bak.* 2>/dev/null | head -n1)
  echo "BACKUP: $bak"
  [ -n "$bak" ] && diff -u "$bak" /opt/hue-emulator/config/groups.yaml || true
  echo
} >> "$OUT"
echo "Appended YAML diffs to $OUT"
'
```

If you want a DB state snapshot file too, run:

```bash
sudo sh -lc '
OUT=/home/pi/mg21_native_db_state.txt
python3 - <<'"'"'PY'"'"' > "$OUT"
import sqlite3
db = "/opt/diyhue/config/native_backend.sqlite"
con = sqlite3.connect(db)
cur = con.cursor()

print("--- groups_tbl ---")
for row in cur.execute("select * from groups_tbl"):
    print(row)

print("--- entertainment_areas ---")
for row in cur.execute("select * from entertainment_areas"):
    print(row)

con.close()
PY
echo "Wrote $OUT"
ls -l "$OUT"
'
```

---

## Minimal reproduction summary

If you only need the shortest possible handoff summary:

1. Patch the **installed** MG21 package in `/opt/mg21-venv`, not only the source tree.
2. In `mg21_bellows.py`:
   - use `in_clusters` for endpoint selection and capability inference
   - use `_get_in_cluster()` for unicast dispatch
3. In `core/service.py`:
   - replace `scheduler or RealtimeScheduler(...)` with explicit `is not None` logic
   - same for `entertainment`
4. In `core/diyhue_bridge.py`:
   - add diyHue compatibility fields to native light payloads
   - add richer entertainment group payload fields
5. In `mg21_daemon.py`:
   - add `/state`
6. In `bridge_runtime.py`:
   - add `get_native_state_sync()`
7. In `restful.py`:
   - merge native lights/groups into `/api/<user>` and `/api/<user>/<resource>`
   - preserve group `0` in full config response
8. In `views.py`:
   - `/lights` returns native lights when native mode is enabled
9. Clear:
   - `/opt/hue-emulator/config/lights.yaml`
   - `/opt/hue-emulator/config/groups.yaml`
10. Fix native DB:
   - entertainment members must be `2,3,4`

---

## Current operational note

The environment is **not yet productionized** because `mg21_daemon.py` is still launched manually after restart. A future cleanup should:

- auto-start the daemon with the container
- hide the coordinator from discovery
- persist human-friendly lamp names

