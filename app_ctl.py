#!/usr/bin/env python3
import argparse
import json
import time

import yaml

from bootstrap_hue_credentials import bootstrap, resolved_bridge_ip as bootstrap_resolved_bridge_ip


def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def resolved_bridge_ip(cfg):
    return bootstrap_resolved_bridge_ip(cfg) or ''


def enabled_lights(cfg):
    return [light for light in cfg.get('lights', []) if light.get('enabled', True)]


def unresolved_enabled_lights(cfg):
    missing = []
    for light in enabled_lights(cfg):
        if light.get('id') in (None, '', 0, '0'):
            missing.append(light.get('name') or '<unnamed>')
    return missing


def has_ready_credentials(cfg):
    return bool(
        resolved_bridge_ip(cfg)
        and cfg.get('app_key')
        and cfg.get('client_key')
        and (cfg.get('psk_identity') or cfg.get('app_key'))
    )


def has_ready_stream_target(cfg):
    return bool(has_ready_credentials(cfg) and cfg.get('entertainment_group_id') and not unresolved_enabled_lights(cfg))


def readiness_report(cfg):
    missing = []
    if not resolved_bridge_ip(cfg):
        missing.append('bridge_ip')
    if not cfg.get('app_key'):
        missing.append('app_key')
    if not cfg.get('client_key'):
        missing.append('client_key')
    if not (cfg.get('psk_identity') or cfg.get('app_key')):
        missing.append('psk_identity')
    if not cfg.get('entertainment_group_id'):
        missing.append('entertainment_group_id')
    unresolved = unresolved_enabled_lights(cfg)
    if unresolved:
        missing.append('resolved_light_ids')
    report = {
        'ready': not missing,
        'backend_mode': cfg.get('backend_mode'),
        'bridge_ip': resolved_bridge_ip(cfg),
        'entertainment_group_id': cfg.get('entertainment_group_id', ''),
        'enabled_light_count': len(enabled_lights(cfg)),
        'unresolved_light_names': unresolved,
        'missing': missing,
    }
    return report


def wait_ready(path, timeout=120):
    deadline = time.time() + timeout
    last_bootstrap = 0
    while time.time() < deadline:
        cfg = load_config(path)
        if has_ready_stream_target(cfg):
            return 0

        if cfg.get('backend_mode') == 'diyhue' and (cfg.get('auto_register_diyhue', True) or cfg.get('auto_discover_entertainment_group', True) or cfg.get('auto_create_entertainment_group', True)):
            now = time.time()
            if now - last_bootstrap >= 5:
                last_bootstrap = now
                try:
                    bootstrap(path, timeout=min(25, max(5, int(deadline - now))))
                except Exception:
                    pass

        time.sleep(2)
    return 1


def main():
    parser = argparse.ArgumentParser(description='Hue Disco Club control helper.')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('wait-ready', help='Wait until DTLS credentials and a stream target are available.')
    p.add_argument('--config', required=True)
    p.add_argument('--timeout', type=int, default=120)

    p = sub.add_parser('status-ready', help='Print a JSON readiness report and exit non-zero when not ready.')
    p.add_argument('--config', required=True)

    args = parser.parse_args()
    if args.cmd == 'wait-ready':
        raise SystemExit(wait_ready(args.config, args.timeout))
    if args.cmd == 'status-ready':
        report = readiness_report(load_config(args.config))
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if report['ready'] else 1)


if __name__ == '__main__':
    main()
