#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
import time

CONTAINER = "diyhue"
EXT_DIR = "/opt/hue-emulator/ext/mg21-native"


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def dexec(script: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["docker", "exec", "-i", CONTAINER, "sh", "-lc", script], check=check, capture=capture)


def install_env() -> None:
    script = textwrap.dedent(
        """
        set -e
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y python3-venv python3-pip gcc libffi-dev libssl-dev pkg-config curl
        if [ ! -x /opt/mg21-venv/bin/python ]; then
            python3 -m venv /opt/mg21-venv
        fi
        /opt/mg21-venv/bin/pip install -U pip setuptools wheel
        /opt/mg21-venv/bin/pip install aiohttp bellows zigpy zigpy-znp zigpy-deconz pyserial pyyaml voluptuous click click-log aiosqlite attrs crccheck cryptography frozendict jsonschema pyserial-asyncio-fast typing_extensions
        /opt/mg21-venv/bin/pip install --force-reinstall /opt/hue-emulator/ext/mg21-native/diyhue_mg21_native_zigbee
        """
    ).strip()
    dexec(script)


def backup_legacy_yaml() -> None:
    script = textwrap.dedent(
        """
        set -e
        ts=$(date +%Y%m%d%H%M%S)
        for name in lights.yaml groups.yaml; do
            path="/opt/hue-emulator/config/${name}"
            if [ -f "$path" ]; then
                cp "$path" "$path.pre-native-safe-backup.$ts"
            fi
        done
        """
    ).strip()
    dexec(script)


def write_bridge_runtime() -> None:
    content = """from __future__ import annotations

import json
import urllib.request
from typing import Any

BASE = \"http://127.0.0.1:9123\"


def native_backend_enabled(config_path: str) -> bool:
    try:
        import yaml
        with open(config_path, \"r\", encoding=\"utf-8\") as f:
            data = yaml.safe_load(f) or {}
        native = ((data or {}).get(\"native_zigbee\") or {})
        mode = (native.get(\"mode\") or \"native_zigbee\").strip().lower()
        return bool(native) and mode in (\"native_mg21\", \"native_zigbee\")
    except Exception:
        return False


def _get(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        body = resp.read().decode(\"utf-8\")
        return json.loads(body) if body else {}


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(\"utf-8\"),
        headers={\"Content-Type\": \"application/json\"},
        method=\"POST\",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode(\"utf-8\")
        return json.loads(body) if body else {\"ok\": True}


def get_native_state_sync(config_path: str) -> dict[str, Any]:
    return _get(\"/state\")


def handle_group_action_sync(config_path: str, group_id: str, payload: dict[str, Any], controller_id: str = \"diyhue\") -> None:
    _post(\"/group_action\", {
        \"group_id\": str(group_id),
        \"payload\": payload,
        \"controller_id\": controller_id,
    })


def permit_join_sync(config_path: str, seconds: int = 60) -> None:
    _post(\"/permit_join\", {\"seconds\": int(seconds)})


def handle_light_state_sync(config_path: str, light_id: str, light_ref: str, payload: dict[str, Any]) -> None:
    _post(\"/light_state\", {
        \"light_id\": str(light_id),
        \"light_ref\": str(light_ref),
        \"payload\": payload,
    })
"""
    dexec(f"cat > {EXT_DIR}/bridge_runtime.py <<'INNERPY'\n{content}INNERPY\nchmod 0644 {EXT_DIR}/bridge_runtime.py")


def write_daemon() -> None:
    content = """from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web

VENV_SITE = \"/opt/mg21-venv/lib/python3.13/site-packages\"
if VENV_SITE not in sys.path and Path(VENV_SITE).exists():
    sys.path.insert(0, VENV_SITE)

DIYHUE_ROOT = \"/opt/hue-emulator\"
if DIYHUE_ROOT not in sys.path and Path(DIYHUE_ROOT).exists():
    sys.path.insert(0, DIYHUE_ROOT)

from diyhue_native_zigbee.integration.hueemulator3 import start_backend_for_diyhue

CONFIG_PATH = \"/opt/hue-emulator/config/config.yaml\"
backend = None
backend_task = None

log = logging.getLogger(\"mg21_daemon\")
logging.basicConfig(level=logging.WARNING)

async def ensure_backend():
    global backend, backend_task
    if backend is not None:
        return backend
    if backend_task is None:
        backend_task = asyncio.create_task(start_backend_for_diyhue(CONFIG_PATH))
    backend = await backend_task
    return backend

async def health(request):
    b = await ensure_backend()
    return web.json_response({\"ok\": True, \"backend\": type(b).__name__})

async def state(request):
    b = await ensure_backend()
    return web.json_response({
        \"lights\": b.diyhue_lights_payload(),
        \"groups\": b.diyhue_groups_payload(),
        \"metrics\": b.metrics_payload(),
    })

def _norm_ieee(ref: str) -> str:
    ref = (ref or \"\").strip().lower()
    if ref.startswith(\"0x\") and len(ref) == 18:
        h = ref[2:]
        return \":\".join(h[i:i+2] for i in range(0, 16, 2))
    return ref.replace(\"-\", \":\").lower()

def _find_device(app, ref: str):
    want = _norm_ieee(ref)
    for dev in getattr(app, \"devices\", {}).values():
        ieee = str(getattr(dev, \"ieee\", \"\")).lower()
        if ieee == want:
            return dev
    return None

def _pick_endpoint(dev):
    eps = getattr(dev, \"endpoints\", {}) or {}
    if 11 in eps:
        return eps[11]
    for epid, ep in eps.items():
        if int(epid) != 242:
            return ep
    return None

def _ensure_native_group(backend, group_id: str, username: str) -> None:
    try:
        repo = backend.service._repository
        repo.get_group(int(group_id))
        return
    except Exception:
        pass
    import json
    import urllib.request
    from diyhue_native_zigbee.core.service import Group as NativeGroup
    url = f\"http://127.0.0.1/api/{username}/groups/{group_id}\"
    with urllib.request.urlopen(url, timeout=10) as resp:
        group_obj = json.loads(resp.read().decode(\"utf-8\"))
    members = set(str(x) for x in (group_obj.get(\"lights\") or []))
    repo = backend.service._repository
    repo.save_group(NativeGroup(group_id=int(group_id), name=group_obj.get(\"name\", f\"Group {group_id}\"), members=members))
    backend.service.ensure_entertainment_area(int(group_id))

async def group_action(request):
    b = await ensure_backend()
    payload = await request.json()
    group_id = str(payload[\"group_id\"])
    action = payload[\"payload\"]
    if isinstance(action, dict) and \"stream\" in action and \"active\" in action[\"stream\"]:
        _ensure_native_group(b, group_id, payload.get(\"controller_id\", \"diyhue\"))
    controller_id = payload.get(\"controller_id\", \"diyhue\")
    try:
        await b.handle_group_action(group_id, action, controller_id=controller_id)
    except Exception as ex:
        msg = str(ex)
        if \"already owned by\" in msg and isinstance(action, dict) and action.get(\"stream\", {}).get(\"active\") is True:
            return web.json_response({\"ok\": True, \"note\": \"stream already active\"})
        if \"is owned by\" in msg and isinstance(action, dict) and action.get(\"stream\", {}).get(\"active\") is False:
            return web.json_response({\"ok\": True, \"note\": \"stream already inactive\"})
        raise
    return web.json_response({\"ok\": True})

async def entertainment_frame(request):
    b = await ensure_backend()
    payload = await request.json()
    area_id = str(payload[\"area_id\"])
    rgb_by_light = {str(k): (int(v[0]), int(v[1]), int(v[2])) for k, v in (payload.get(\"rgb_by_light\") or {}).items()}
    b.handle_entertainment_frame(area_id, rgb_by_light)
    await b.flush_entertainment()
    return web.json_response({\"ok\": True, \"lights\": len(rgb_by_light)})

async def permit_join(request):
    b = await ensure_backend()
    payload = await request.json()
    seconds = int(payload.get(\"seconds\", 60))
    cand = getattr(b, \"permit_join\", None)
    if callable(cand):
        await cand(seconds)
        return web.json_response({\"ok\": True, \"seconds\": seconds})
    adapter = getattr(b, \"adapter\", None)
    cand = getattr(adapter, \"permit_join\", None) if adapter is not None else None
    if callable(cand):
        await cand(seconds)
        return web.json_response({\"ok\": True, \"seconds\": seconds})
    raise web.HTTPInternalServerError(text=\"No permit_join method available\")

async def light_state(request):
    b = await ensure_backend()
    payload = await request.json()
    adapter = getattr(b, \"adapter\", None)
    app = getattr(adapter, \"_application\", None) or getattr(adapter, \"application\", None)
    if app is None:
        raise web.HTTPInternalServerError(text=\"No zigbee application available\")
    light_ref = str(payload.get(\"light_ref\") or payload.get(\"light_id\") or \"\")
    put = payload.get(\"payload\") or {}
    dev = _find_device(app, light_ref)
    if dev is None:
        raise web.HTTPNotFound(text=f\"Native device not found for {light_ref}\")
    ep = _pick_endpoint(dev)
    if ep is None:
        raise web.HTTPInternalServerError(text=f\"No usable endpoint for {light_ref}\")
    onoff = getattr(ep, \"in_clusters\", {}).get(6)
    level = getattr(ep, \"in_clusters\", {}).get(8)
    color = getattr(ep, \"in_clusters\", {}).get(768)
    if \"on\" in put:
        if onoff is None:
            raise web.HTTPInternalServerError(text=\"OnOff cluster unavailable\")
        await onoff.command(1 if bool(put[\"on\"]) else 0)
    if \"bri\" in put:
        if level is None:
            raise web.HTTPInternalServerError(text=\"Level cluster unavailable\")
        bri = max(1, min(254, int(put[\"bri\"])))
        await level.command(4, bri, 0)
    if \"xy\" in put:
        if color is None:
            raise web.HTTPInternalServerError(text=\"Color cluster unavailable\")
        x = max(0, min(65535, int(float(put[\"xy\"][0]) * 65535)))
        y = max(0, min(65535, int(float(put[\"xy\"][1]) * 65535)))
        await color.command(7, x, y, 0)
    return web.json_response({\"ok\": True, \"light_ref\": light_ref, \"payload\": put})

async def create_app():
    app = web.Application()
    app.router.add_get(\"/health\", health)
    app.router.add_get(\"/state\", state)
    app.router.add_post(\"/group_action\", group_action)
    app.router.add_post(\"/light_state\", light_state)
    app.router.add_post(\"/entertainment_frame\", entertainment_frame)
    app.router.add_post(\"/permit_join\", permit_join)
    return app

def main():
    web.run_app(create_app(), host=\"127.0.0.1\", port=9123)

if __name__ == \"__main__\":
    main()
"""
    dexec(f"cat > {EXT_DIR}/mg21_daemon.py <<'INNERPY'\n{content}INNERPY\nchmod 0755 {EXT_DIR}/mg21_daemon.py")


def patch_restful() -> None:
    script = r"""
python3 - <<'INNERPY'
from pathlib import Path

p = Path('/opt/hue-emulator/flaskUI/restful.py')
s = p.read_text()

import_block = '''import sys
_MG21_EXT = "/opt/hue-emulator/ext/mg21-native"
if _MG21_EXT not in sys.path:
    sys.path.insert(0, _MG21_EXT)
from bridge_runtime import native_backend_enabled, get_native_state_sync, handle_group_action_sync, permit_join_sync, handle_light_state_sync
'''
if 'from bridge_runtime import native_backend_enabled, get_native_state_sync, handle_group_action_sync, permit_join_sync, handle_light_state_sync' not in s:
    markers = [
        'from werkzeug.security import generate_password_hash\n',
        'import traceback\n',
        'from flask import request, jsonify\n',
    ]
    inserted = False
    for marker in markers:
        if marker in s:
            s = s.replace(marker, marker + import_block, 1)
            inserted = True
            break
    if not inserted:
        s = import_block + '\n' + s

if '_mg21_orig_elementparam_put = ElementParam.put' not in s:
    patch = '''

# --- MG21 native backend monkeypatches ---
try:
    _mg21_cfg_path = "/opt/hue-emulator/config/config.yaml"

    def _mg21_native_payload():
        try:
            return get_native_state_sync(_mg21_cfg_path) or {}
        except Exception:
            logging.exception("native MG21 state fetch failed")
            return {}

    def _mg21_native_lights():
        native = _mg21_native_payload()
        lights = native.get("lights") or {}
        return lights if isinstance(lights, dict) else {}

    def _mg21_native_groups():
        native = _mg21_native_payload()
        groups = native.get("groups") or {}
        return groups if isinstance(groups, dict) else {}

    def _mg21_extract_light_ref(resourceid, light_obj=None, native_obj=None):
        candidates = []
        if isinstance(native_obj, dict):
            proto_cfg = native_obj.get("protocol_cfg") if isinstance(native_obj.get("protocol_cfg"), dict) else {}
            candidates.extend([
                native_obj.get("ieee"),
                native_obj.get("uniqueid"),
                proto_cfg.get("uid"),
                native_obj.get("name"),
            ])
        if light_obj is not None:
            candidates.extend([
                getattr(light_obj, "ieee", None),
                getattr(light_obj, "uniqueid", None),
                getattr(light_obj, "name", None),
            ])
            proto_cfg = getattr(light_obj, "protocol_cfg", None)
            if isinstance(proto_cfg, dict):
                candidates.append(proto_cfg.get("uid"))
        candidates.append(resourceid)
        for raw in candidates:
            value = str(raw or "").strip()
            if not value:
                continue
            if value.endswith("-0b"):
                value = value[:-3]
            if value.startswith("0x") and len(value) == 18:
                value = ':'.join(value[2:][i:i+2] for i in range(0, 16, 2))
            flat = value.replace(':', '').replace('-', '')
            if len(flat) == 16 and all(ch in '0123456789abcdefABCDEF' for ch in flat):
                return value.replace('-', ':').lower()
        return str(resourceid)

    _mg21_orig_resourceelements_get = ResourceElements.get
    def _mg21_resourceelements_get(self, username, resource):
        result = _mg21_orig_resourceelements_get(self, username, resource)
        if native_backend_enabled(_mg21_cfg_path) and resource in ["lights", "groups"]:
            native = _mg21_native_payload()
            if isinstance(result, dict):
                result.update(native.get(resource) or {})
        return result
    ResourceElements.get = _mg21_resourceelements_get

    _mg21_orig_entireconfig_get = EntireConfig.get
    def _mg21_entireconfig_get(self, username):
        result = _mg21_orig_entireconfig_get(self, username)
        if native_backend_enabled(_mg21_cfg_path):
            native = _mg21_native_payload()
            result.setdefault("lights", {})
            result.setdefault("groups", {})
            result["lights"].update(native.get("lights") or {})
            if "0" not in result["groups"] and "groups" in bridgeConfig and "0" in bridgeConfig["groups"]:
                try:
                    result["groups"]["0"] = bridgeConfig["groups"]["0"].getV1Api().copy()
                except Exception:
                    pass
            result["groups"].update(native.get("groups") or {})
        return result
    EntireConfig.get = _mg21_entireconfig_get

    _mg21_orig_elementparam_put = ElementParam.put
    def _mg21_elementparam_put(self, username, resource, resourceid, param):
        putDict = request.get_json(force=True)
        if resource == "lights" and param == "state" and native_backend_enabled(_mg21_cfg_path):
            native_obj = _mg21_native_lights().get(str(resourceid))
            if native_obj is not None:
                try:
                    light = bridgeConfig.get(resource, {}).get(resourceid)
                    light_ref = _mg21_extract_light_ref(resourceid, light_obj=light, native_obj=native_obj)
                    handle_light_state_sync(_mg21_cfg_path, resourceid, light_ref, putDict)
                    responseList = []
                    responseLocation = "/" + resource + "/" + resourceid + "/" + param + "/"
                    for key, value in putDict.items():
                        responseList.append({"success": {responseLocation + key: value}})
                    return responseList
                except Exception as ex:
                    logging.exception("native MG21 light state failed")
                    return [{"error": {"type": 901, "address": f"/lights/{resourceid}/state", "description": str(ex)}}]
        elif param == "action" and resource == "groups" and native_backend_enabled(_mg21_cfg_path):
            native_group_ids = set(_mg21_native_groups().keys())
            if str(resourceid) in native_group_ids or (isinstance(putDict, dict) and ("stream" in putDict or "native_zigbee" in putDict)):
                try:
                    if "native_zigbee" in putDict and "permit_join" in putDict["native_zigbee"]:
                        seconds = int(putDict["native_zigbee"]["permit_join"])
                        permit_join_sync(_mg21_cfg_path, seconds)
                        return [{"success": {"/native_zigbee/permit_join": seconds}}]
                    handle_group_action_sync(_mg21_cfg_path, resourceid, putDict, controller_id=username)
                    if "stream" in putDict and "active" in putDict["stream"]:
                        active = bool(putDict["stream"]["active"])
                        native_group = _mg21_native_groups().get(str(resourceid)) or {}
                        groups_obj = bridgeConfig.get("groups", {})
                        group = groups_obj.get(resourceid) or groups_obj.get(str(resourceid))
                        if group is None and native_group:
                            data = {
                                "id_v1": str(resourceid),
                                "name": native_group.get("name", f"Group {resourceid}"),
                                "type": native_group.get("type", "Entertainment"),
                                "configuration_type": native_group.get("configuration_type", "screen"),
                            }
                            group = EntertainmentConfiguration.EntertainmentConfiguration(data)
                            bridgeConfig.setdefault("groups", {})[str(resourceid)] = group
                            for lid in (native_group.get("lights") or []):
                                light_obj = bridgeConfig.get("lights", {}).get(str(lid))
                                if light_obj is not None:
                                    group.add_light(light_obj)
                            for lid, loc in (native_group.get("locations") or {}).items():
                                light_obj = bridgeConfig.get("lights", {}).get(str(lid))
                                if light_obj is not None and isinstance(loc, list) and len(loc) >= 3:
                                    group.locations[light_obj] = [{"x": loc[0], "y": loc[1], "z": loc[2]}]
                        if group is not None:
                            if not hasattr(group, "stream") or group.stream is None:
                                group.stream = {}
                            was_active = bool(group.stream.get("active"))
                            group.stream["active"] = active
                            group.stream["owner"] = username if active else None
                            group.stream["proxymode"] = "auto"
                            group.stream["proxynode"] = "/bridge"
                            if active and not was_active:
                                logging.info("start hue entertainment")
                                Thread(target=entertainmentService, args=[group, bridgeConfig["apiUsers"][username]], daemon=True).start()
                            elif active:
                                logging.info("hue entertainment already active; skipping")
                            else:
                                logging.info("stop hue entertainent")
                                Popen(["killall", "openssl"])
                        return [{"success": {f"/groups/{resourceid}/action/stream": {"active": active}}}]
                    return [{"success": {f"/groups/{resourceid}/action": putDict}}]
                except Exception as ex:
                    logging.exception("native MG21 group action failed")
                    return [{"error": {"type": 901, "address": f"/groups/{resourceid}/action", "description": str(ex)}}]
        return _mg21_orig_elementparam_put(self, username, resource, resourceid, param)
    ElementParam.put = _mg21_elementparam_put
except Exception:
    logging.exception("Failed to install MG21 monkeypatches")
'''
    s += patch
p.write_text(s)
print('patched', p)
INNERPY
"""
    dexec(script)


def patch_views() -> None:
    script = r"""
python3 - <<'INNERPY'
from pathlib import Path
import re

p = Path('/opt/hue-emulator/flaskUI/core/views.py')
s = p.read_text()

import_block = '''import sys
_MG21_EXT = "/opt/hue-emulator/ext/mg21-native"
if _MG21_EXT not in sys.path:
    sys.path.insert(0, _MG21_EXT)
from bridge_runtime import native_backend_enabled, get_native_state_sync
'''
if 'from bridge_runtime import native_backend_enabled, get_native_state_sync' not in s:
    markers = [
        'from flask import render_template\n',
        'from flask import jsonify\n',
        'from flask import request\n',
    ]
    inserted = False
    for marker in markers:
        if marker in s:
            s = s.replace(marker, marker + import_block, 1)
            inserted = True
            break
    if not inserted:
        s = import_block + '\n' + s

if '_mg21_original_get_lights = get_lights' not in s:
    route_pattern = re.compile(r"@core\.route\('/lights'\)\ndef get_lights\(\):\n(?:    .*\n)+?", re.M)
    replacement = '''@core.route('/lights')
def get_lights():
    cfg_path = "/opt/hue-emulator/config/config.yaml"
    if native_backend_enabled(cfg_path):
        native = get_native_state_sync(cfg_path) or {}
        return native.get("lights", {})
    return bridgeConfig.json_config["lights"]

'''
    if route_pattern.search(s):
        s = route_pattern.sub(replacement, s, count=1)
    else:
        s += '''

try:
    _mg21_original_get_lights = get_lights
    def get_lights():
        cfg_path = "/opt/hue-emulator/config/config.yaml"
        if native_backend_enabled(cfg_path):
            native = get_native_state_sync(cfg_path) or {}
            return native.get("lights", {})
        return _mg21_original_get_lights()
    try:
        core.view_functions['get_lights'] = get_lights
    except Exception:
        pass
except Exception:
    pass
'''
p.write_text(s)
print('patched', p)
INNERPY
"""
    dexec(script)


def patch_entertainment_service() -> None:
    script = r"""
python3 - <<'INNERPY'
from pathlib import Path

p = Path('/opt/hue-emulator/services/entertainment.py')
s = p.read_text()
if 'nativeEntertainment = {}' in s:
    print('already patched', p)
    raise SystemExit(0)

s = s.replace('import socket, json, uuid\n', 'import socket, json, uuid\nimport urllib.request\n', 1)
s = s.replace('                mqttLights = []\n                wledLights = {}\n', '                mqttLights = []\n                nativeEntertainment = {}\n                wledLights = {}\n', 1)

old = '''                        elif proto == "mqtt":
                            if not light.state.get("on", True):
                                payload = json.dumps({"state": "OFF", "transition": 0})
                            else:
                                payload = json.dumps({
                                    "state": "ON",
                                    "brightness": int(light.state["bri"]),
                                    "color": {"x": float(light.state["xy"][0]), "y": float(light.state["xy"][1])},
                                    "transition": 0
                                })
                            key = str(light.id_v1)
                            if _last_mqtt_payload.get(key) != payload:
                                _last_mqtt_payload[key] = payload
                                mqttLights.append({"topic": light.protocol_cfg["command_topic"], "payload": payload})
'''
new = '''                        elif proto == "mqtt":
                            try:
                                import yaml
                                with open("/opt/hue-emulator/config/config.yaml", "r", encoding="utf-8") as f:
                                    cfg = yaml.safe_load(f) or {}
                                native = cfg.get("native_zigbee") or {}
                                mode = ((native.get("mode") or "native_zigbee")).strip().lower()
                            except Exception:
                                native = {}
                                mode = ""
                            if native and mode in ("native_mg21", "native_zigbee"):
                                nativeEntertainment[str(light.id_v1)] = [r, g, b]
                            else:
                                if not light.state.get("on", True):
                                    payload = json.dumps({"state": "OFF", "transition": 0})
                                else:
                                    payload = json.dumps({
                                        "state": "ON",
                                        "brightness": int(light.state["bri"]),
                                        "color": {"x": float(light.state["xy"][0]), "y": float(light.state["xy"][1])},
                                        "transition": 0
                                    })
                                key = str(light.id_v1)
                                if _last_mqtt_payload.get(key) != payload:
                                    _last_mqtt_payload[key] = payload
                                    mqttLights.append({"topic": light.protocol_cfg["command_topic"], "payload": payload})
'''
marker = '''                    if len(mqttLights) != 0:
                        auth = None
                        if bridgeConfig["config"]["mqtt"]["mqttUser"] != "" and bridgeConfig["config"]["mqtt"]["mqttPassword"] != "":
                            auth = {'username':bridgeConfig["config"]["mqtt"]["mqttUser"], 'password':bridgeConfig["config"]["mqtt"]["mqttPassword"]}
                        publish.multiple(mqttLights, hostname=bridgeConfig["config"]["mqtt"]["mqttServer"], port=bridgeConfig["config"]["mqtt"]["mqttPort"], auth=auth)
'''
insert = '''                    if len(mqttLights) != 0:
                        auth = None
                        if bridgeConfig["config"]["mqtt"]["mqttUser"] != "" and bridgeConfig["config"]["mqtt"]["mqttPassword"] != "":
                            auth = {'username':bridgeConfig["config"]["mqtt"]["mqttUser"], 'password':bridgeConfig["config"]["mqtt"]["mqttPassword"]}
                        publish.multiple(mqttLights, hostname=bridgeConfig["config"]["mqtt"]["mqttServer"], port=bridgeConfig["config"]["mqtt"]["mqttPort"], auth=auth)
                    if len(nativeEntertainment) != 0:
                        req = urllib.request.Request(
                            "http://127.0.0.1:9123/entertainment_frame",
                            data=json.dumps({"area_id": str(group.id_v1), "rgb_by_light": nativeEntertainment}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            resp.read()
'''
if old not in s:
    raise SystemExit('mqtt block not found')
if marker not in s:
    raise SystemExit('post-mqtt marker not found')
s = s.replace(old, new, 1)
s = s.replace(marker, insert, 1)
p.write_text(s)
print('patched', p)
INNERPY
"""
    dexec(script, check=False)


def start_daemon() -> None:
    script = r"""
python3 - <<'INNERPY'
import os, signal
me = os.getpid()
ppid = os.getppid()
for pid in sorted(p for p in os.listdir('/proc') if p.isdigit()):
    ipid = int(pid)
    if ipid in (1, me, ppid):
        continue
    try:
        cmd = open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\x00', b' ').decode().strip()
    except Exception:
        continue
    if cmd.endswith('mg21_daemon.py') or ' /opt/hue-emulator/ext/mg21-native/mg21_daemon.py' in cmd:
        try:
            os.kill(ipid, signal.SIGKILL)
        except Exception:
            pass
INNERPY
nohup /opt/mg21-venv/bin/python /opt/hue-emulator/ext/mg21-native/mg21_daemon.py >/tmp/mg21-daemon.log 2>&1 &
"""
    dexec(script)


def health_check() -> None:
    for _ in range(30):
        cp = dexec('curl -fsS http://127.0.0.1:9123/health', check=False, capture=True)
        if cp.returncode == 0:
            print(cp.stdout.strip())
            return
        time.sleep(2)
    logs = dexec('tail -n 120 /tmp/mg21-daemon.log || true', check=False, capture=True)
    sys.stderr.write(logs.stdout)
    raise SystemExit('MG21 daemon health check failed')


def main() -> int:
    global CONTAINER
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', default='diyhue')
    parser.add_argument('--restart-on-change', action='store_true')
    args = parser.parse_args()
    CONTAINER = args.container

    install_env()
    backup_legacy_yaml()
    write_bridge_runtime()
    write_daemon()
    patch_restful()
    patch_views()
    patch_entertainment_service()
    start_daemon()
    health_check()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
