#!/usr/bin/env python3
import argparse
import subprocess
import sys
import textwrap
import time

INTERNAL_PATCHER = r"""
from pathlib import Path
import re

changed = False

def patch_restful():
    global changed
    p = Path('/opt/hue-emulator/flaskUI/restful.py')
    s = p.read_text()
    original = s

    # 0001: add explicit stream handling for group action if missing.
    if 'if resource == "groups" and "stream" in putDict and "active" in putDict["stream"]:' not in s:
        m = re.search(r'^(\s*)elif param == "action":\s*# state is applied to a (?:light|group)\n', s, re.MULTILINE)
        if m:
            indent = m.group(1)
            block = (
                f'{indent}elif param == "action":  # state is applied to a group\n'
                f'{indent}    if resource == "groups" and "stream" in putDict and "active" in putDict["stream"]:\n'
                f'{indent}        group = bridgeConfig["groups"][resourceid]\n'
                f'{indent}        active = bool(putDict["stream"]["active"])\n'
                f'{indent}        group.stream["active"] = active\n'
                f'{indent}        group.stream["proxymode"] = "auto"\n'
                f'{indent}        group.stream["proxynode"] = "/bridge"\n'
                f'{indent}        if active:\n'
                f'{indent}            group.stream["owner"] = username\n'
                f'{indent}            logging.info("start hue entertainment")\n'
                f'{indent}            Thread(target=entertainmentService, args=[\n'
                f'{indent}                   group, bridgeConfig["apiUsers"][username]], daemon=True).start()\n'
                f'{indent}        else:\n'
                f'{indent}            group.stream["owner"] = None\n'
                f'{indent}            logging.info("stop hue entertainent")\n'
                f'{indent}            Popen(["killall", "openssl"])\n'
                f'{indent}        return [{{"success": {{f"/groups/{{resourceid}}/action/stream": {{"active": active}}}}}}]\n'
            )
            start, end = m.span()
            s = s[:start] + block + s[end:]

    # 0002: only start entertainmentService on inactive -> active transition.
    if 'was_active = bool(group.stream.get("active"))' not in s:
        pattern = re.compile(
            r'(?P<indent>\s*)active = bool\(putDict\["stream"\]\["active"\]\)\n'
            r'(?P=indent)group\.stream\["active"\] = active\n'
            r'(?P=indent)group\.stream\["proxymode"\] = "auto"\n'
            r'(?P=indent)group\.stream\["proxynode"\] = "/bridge"\n'
            r'(?P=indent)if active:\n'
            r'(?P=indent)    group\.stream\["owner"\] = username\n'
            r'(?P=indent)    logging\.info\("start hue entertainment"\)\n'
            r'(?P=indent)    Thread\(target=entertainmentService, args=\[\n'
            r'(?P=indent)           group, bridgeConfig\["apiUsers"\]\[username\]\], daemon=True\)\.start\(\)\n',
            re.MULTILINE,
        )
        m = pattern.search(s)
        if m:
            indent = m.group('indent')
            repl = (
                f'{indent}active = bool(putDict["stream"]["active"])\n'
                f'{indent}was_active = bool(group.stream.get("active"))\n'
                f'{indent}group.stream["active"] = active\n'
                f'{indent}group.stream["proxymode"] = "auto"\n'
                f'{indent}group.stream["proxynode"] = "/bridge"\n'
                f'{indent}if active:\n'
                f'{indent}    group.stream["owner"] = username\n'
                f'{indent}    if not was_active:\n'
                f'{indent}        logging.info("start hue entertainment")\n'
                f'{indent}        Thread(target=entertainmentService, args=[\n'
                f'{indent}               group, bridgeConfig["apiUsers"][username]], daemon=True).start()\n'
                f'{indent}    else:\n'
                f'{indent}        logging.info("hue entertainment already active; skipping")\n'
            )
            s = s[:m.start()] + repl + s[m.end():]

    if s != original:
        p.write_text(s)
        changed = True
        print('patched', p)
    else:
        print('ok', p)


def patch_entertainment():
    global changed
    p = Path('/opt/hue-emulator/services/entertainment.py')
    s = p.read_text()
    original = s

    # 0003a: guard invalid sync frame size.
    old_sync = '                      p.stdout.read(frameBites - 9) # sync streaming bytes\n                      init = True\n'
    new_sync = (
        '                      if frameBites > 9:\n'
        '                          p.stdout.read(frameBites - 9) # sync streaming bytes\n'
        '                          init = True\n'
        '                      else:\n'
        '                          logging.warning("Invalid frameBites during sync: %s", frameBites)\n'
        '                          frameBites = 10\n'
        '                          frameID = 1\n'
        '                          initMatchBytes = 0\n'
        '                          continue\n'
    )
    if old_sync in s and 'Invalid frameBites during sync' not in s:
        s = s.replace(old_sync, new_sync, 1)

    # 0003b: guard HueStream v2 out-of-range light index.
    old_v2 = '                              light = lights_v2[data[i]]["light"]\n'
    new_v2 = (
        '                              if i >= len(data) or data[i] >= len(lights_v2):\n'
        '                                  logging.warning(\n'
        '                                      "HueStream v2 light index out of range: offset=%s index=%s lights=%s len=%s",\n'
        '                                      i, data[i] if i < len(data) else None, len(lights_v2), len(data))\n'
        '                                  break\n'
        '                              light = lights_v2[data[i]]["light"]\n'
    )
    if old_v2 in s and 'HueStream v2 light index out of range' not in s:
        s = s.replace(old_v2, new_v2, 1)

    # 0003c: force MQTT entertainment payloads to be full-state ON/OFF payloads
    # and avoid skipSimilarFrames partial updates that break Zigbee2MQTT handoff.
    if '_last_mqtt_payload = {}' not in s:
        s = s.replace(
            'YeelightConnections = {}\n',
            'YeelightConnections = {}\n_last_mqtt_payload = {}\n',
            1,
        )

    old_mqtt = (
        '                        elif proto == "mqtt":\n'
        '                            operation = skipSimilarFrames(light.id_v1, light.state["xy"], light.state["bri"])\n'
        '                            if operation == 1:\n'
        '                                mqttLights.append({"topic": light.protocol_cfg["command_topic"], "payload": json.dumps({"brightness": light.state["bri"], "transition": 0.2})})\n'
        '                            elif operation == 2:\n'
        '                                mqttLights.append({"topic": light.protocol_cfg["command_topic"], "payload": json.dumps({"color": {"x": light.state["xy"][0], "y": light.state["xy"][1]}, "transition": 0.15})})\n'
    )
    new_mqtt = (
        '                        elif proto == "mqtt":\n'
        '                            if not light.state.get("on", True):\n'
        '                                payload = json.dumps({"state": "OFF", "transition": 0})\n'
        '                            else:\n'
        '                                payload = json.dumps({\n'
        '                                    "state": "ON",\n'
        '                                    "brightness": int(light.state["bri"]),\n'
        '                                    "color": {"x": float(light.state["xy"][0]), "y": float(light.state["xy"][1])},\n'
        '                                    "transition": 0\n'
        '                                })\n'
        '                            key = str(light.id_v1)\n'
        '                            if _last_mqtt_payload.get(key) != payload:\n'
        '                                _last_mqtt_payload[key] = payload\n'
        '                                mqttLights.append({"topic": light.protocol_cfg["command_topic"], "payload": payload})\n'
    )
    if old_mqtt in s and '_last_mqtt_payload.get(key) != payload' not in s:
        s = s.replace(old_mqtt, new_mqtt, 1)

    # 0004a: on native MG21 group action stream=true, ensure diyHue has an
    # EntertainmentConfiguration object so entertainmentService can start.
    old_native_group_block = (
        '                    handle_group_action_sync(_mg21_cfg_path, resourceid, putDict, controller_id=username)\n'
        '                    if "stream" in putDict and "active" in putDict["stream"]:\n'
        '                        group = bridgeConfig.get("groups", {}).get(resourceid)\n'
        '                        active = bool(putDict["stream"]["active"])\n'
        '                        if group is not None and hasattr(group, "stream"):\n'
        '                            group.stream["active"] = active\n'
        '                            group.stream["owner"] = username if active else None\n'
        '                            group.stream["proxymode"] = "auto"\n'
        '                            group.stream["proxynode"] = "/bridge"\n'
        '                        return [{"success": {f"/groups/{resourceid}/action/stream": {"active": active}}}]\n'
    )
    new_native_group_block = (
        '                    handle_group_action_sync(_mg21_cfg_path, resourceid, putDict, controller_id=username)\n'
        '                    if "stream" in putDict and "active" in putDict["stream"]:\n'
        '                        active = bool(putDict["stream"]["active"])\n'
        '                        native_group = _mg21_native_groups().get(str(resourceid)) or {}\n'
        '                        group = bridgeConfig.get("groups", {}).get(resourceid)\n'
        '                        if group is None and native_group:\n'
        '                            data = {"id_v1": str(resourceid), "name": native_group.get("name", f"Group {resourceid}"), "type": native_group.get("type", "Entertainment"), "configuration_type": native_group.get("configuration_type", "screen")}\n'
        '                            group = EntertainmentConfiguration.EntertainmentConfiguration(data)\n'
        '                            bridgeConfig.setdefault("groups", {})[str(resourceid)] = group\n'
        '                            for lid in (native_group.get("lights") or []):\n'
        '                                light_obj = bridgeConfig.get("lights", {}).get(str(lid))\n'
        '                                if light_obj is not None:\n'
        '                                    group.add_light(light_obj)\n'
        '                            for lid, loc in (native_group.get("locations") or {}).items():\n'
        '                                light_obj = bridgeConfig.get("lights", {}).get(str(lid))\n'
        '                                if light_obj is not None and isinstance(loc, list) and len(loc) >= 3:\n'
        '                                    group.locations[light_obj] = [{"x": loc[0], "y": loc[1], "z": loc[2]}]\n'
        '                        if group is not None:\n'
        '                            if not hasattr(group, "stream") or group.stream is None:\n'
        '                                group.stream = {}\n'
        '                            was_active = bool(group.stream.get("active"))\n'
        '                            group.stream["active"] = active\n'
        '                            group.stream["owner"] = username if active else None\n'
        '                            group.stream["proxymode"] = "auto"\n'
        '                            group.stream["proxynode"] = "/bridge"\n'
        '                            if active and not was_active:\n'
        '                                logging.info("start hue entertainment")\n'
        '                                Thread(target=entertainmentService, args=[group, bridgeConfig["apiUsers"][username]], daemon=True).start()\n'
        '                            elif not active:\n'
        '                                logging.info("stop hue entertainent")\n'
        '                                Popen(["killall", "openssl"])\n'
        '                        return [{"success": {f"/groups/{resourceid}/action/stream": {"active": active}}}]\n'
    )
    if old_native_group_block in s and 'group = EntertainmentConfiguration.EntertainmentConfiguration(data)' not in s:
        s = s.replace(old_native_group_block, new_native_group_block, 1)

    # 0003d: remove transition smoothing from MQTT, Yeelight and generic V1 entertainment updates.
    s = s.replace('"transition": 0.2', '"transition": 0')
    s = s.replace('"transition": 0.15', '"transition": 0')
    s = s.replace('c.command("set_rgb", [(r * 65536) + (g * 256) + b, "smooth", 200])',
                  'c.command("set_rgb", [(r * 65536) + (g * 256) + b, "sudden", 0])')
    s = s.replace('c.command("set_bright", [int(light.state["bri"] / 2.55), "smooth", 200])',
                  'c.command("set_bright", [int(light.state["bri"] / 2.55), "sudden", 0])')
    s = s.replace('light.setV1State({"bri": light.state["bri"], "transitiontime": 3})',
                  'light.setV1State({"bri": light.state["bri"], "transitiontime": 0})')
    s = s.replace('light.setV1State({"xy": light.state["xy"], "transitiontime": 3})',
                  'light.setV1State({"xy": light.state["xy"], "transitiontime": 0})')

    if s != original:
        p.write_text(s)
        changed = True
        print('patched', p)
    else:
        print('ok', p)

patch_restful()
patch_entertainment()
print('CHANGED=' + ('1' if changed else '0'))
"""


def run(cmd, *, input_text=None, check=True):
    return subprocess.run(cmd, input=input_text, text=True, capture_output=True, check=check)


def wait_for_container(container: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ps = run([
            '/usr/bin/docker', 'inspect', '-f', '{{.State.Running}}', container
        ], check=False)
        if ps.returncode == 0 and ps.stdout.strip() == 'true':
            return
        time.sleep(1)
    raise SystemExit(f'Container {container} did not become ready within {timeout}s')


def main():
    ap = argparse.ArgumentParser(description='Apply persistent diyHue runtime patches inside the container.')
    ap.add_argument('--container', required=True)
    ap.add_argument('--timeout', type=int, default=30)
    ap.add_argument('--restart-on-change', action='store_true')
    args = ap.parse_args()

    wait_for_container(args.container, args.timeout)
    res = run(['/usr/bin/docker', 'exec', '-i', args.container, 'python3', '-'], input_text=INTERNAL_PATCHER, check=False)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    if res.returncode != 0:
        raise SystemExit(res.returncode)

    changed = 'CHANGED=1' in res.stdout
    if changed and args.restart_on_change:
        restart = run(['/usr/bin/docker', 'restart', args.container], check=False)
        sys.stdout.write(restart.stdout)
        sys.stderr.write(restart.stderr)
        if restart.returncode != 0:
            raise SystemExit(restart.returncode)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
