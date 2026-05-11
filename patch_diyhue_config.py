#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml


def _normalized_entertainment_group_id(value: object) -> str:
    text = str(value or '').strip()
    return text if text else '1'


def _apply_mapping(cfg: dict, args) -> dict:
    mqtt = cfg.setdefault('mqtt', {})
    if args.native_zigbee:
        mqtt['enabled'] = False
        cfg['native_zigbee'] = {
            'adapter_type': args.native_zigbee_adapter,
            'radio': {
                'serial_port': '/dev/ttyUSB0',
                'baudrate': int(args.radio_baudrate),
                'channel': int(args.radio_channel),
                'adapter_type': args.native_zigbee_adapter,
                'database_path': '/opt/hue-emulator/config/native_zigbee.db',
                'state_path': '/opt/hue-emulator/config/native_backend.sqlite',
                'entertainment_group_id': _normalized_entertainment_group_id(args.entertainment_group_id),
                'enable_entertainment': True,
                'entertainment_queue_size': 3,
                'entertainment_max_fps': 15.0,
                'config_path': '/opt/hue-emulator/config/config.yaml',
            },
        }
    else:
        mqtt['enabled'] = True
        mqtt['mqttServer'] = args.mqtt_server
        mqtt['mqttPort'] = int(args.mqtt_port)
        mqtt['mqttUser'] = args.mqtt_user
        mqtt['mqttPassword'] = args.mqtt_password
        mqtt['discoveryPrefix'] = args.discovery_prefix
    cfg.setdefault('homeassistant', {})['enabled'] = False
    if args.timezone:
        cfg['timezone'] = args.timezone
    return cfg


def _patch_yaml_file(path: Path, args) -> bool:
    if not path.exists():
        return False
    try:
        cfg = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception:
        return False
    if not isinstance(cfg, dict) or 'whitelist' not in cfg:
        return False
    before = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    after_obj = _apply_mapping(cfg, args)
    after = yaml.safe_dump(after_obj, sort_keys=False, allow_unicode=True)
    if before != after:
        path.write_text(after, encoding='utf-8')
        return True
    return False


def _patch_json_file(path: Path, args) -> bool:
    if not path.exists():
        return False
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    before = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    after_obj = _apply_mapping(cfg, args)
    after = json.dumps(after_obj, sort_keys=True, separators=(",", ":"))
    if before != after:
        path.write_text(json.dumps(after_obj, indent=2, sort_keys=False) + "\n", encoding='utf-8')
        return True
    return False


def main():
    p = argparse.ArgumentParser(description='Patch diyHue config for MQTT discovery or native MG21 support.')
    p.add_argument('path')
    p.add_argument('--mqtt-server', default='127.0.0.1')
    p.add_argument('--mqtt-port', type=int, default=1883)
    p.add_argument('--mqtt-user', default='')
    p.add_argument('--mqtt-password', default='')
    p.add_argument('--discovery-prefix', default='homeassistant')
    p.add_argument('--timezone', default='')
    p.add_argument('--native-zigbee', action='store_true')
    p.add_argument('--native-zigbee-adapter', choices=('bellows', 'zstack', 'deconz'), default='bellows')
    p.add_argument('--radio-baudrate', type=int, default=115200)
    p.add_argument('--radio-channel', type=int, default=20)
    p.add_argument('--entertainment-group-id', default='1')
    args = p.parse_args()

    cfg_path = Path(args.path)
    changed = False
    changed = _patch_json_file(cfg_path, args) or changed
    changed = _patch_yaml_file(cfg_path, args) or changed
    changed = _patch_yaml_file(cfg_path.with_suffix('.yaml'), args) or changed
    changed = _patch_yaml_file(cfg_path.with_suffix('.yml'), args) or changed
    print('CHANGED' if changed else 'OK')


if __name__ == '__main__':
    main()
