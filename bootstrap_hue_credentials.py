#!/usr/bin/env python3
import argparse
import json
import socket
import re
import sys
import time
from pathlib import Path

import requests
import urllib3
import yaml

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_DEVICE_TYPE = "hue_disco#raspberrypi"
DEFAULT_DIYHUE_USER = "admin@diyhue.org"
DEFAULT_DIYHUE_PASSWORD = "changeme"


def _looks_like_ip(value):
    value = str(value or '').strip()
    if not value:
        return False
    parts = value.split('.')
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except Exception:
        return False


def _candidate_bridge_ips_from_discovery_meethue(timeout=5):
    try:
        response = requests.get('https://discovery.meethue.com/', timeout=timeout, verify=False)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ip = str(item.get('internalipaddress') or '').strip()
                if _looks_like_ip(ip):
                    yield ip
    except Exception:
        return


def _candidate_bridge_ips_from_ssdp(timeout=3):
    message = '\r\n'.join([
        'M-SEARCH * HTTP/1.1',
        'HOST:239.255.255.250:1900',
        'MAN:"ssdp:discover"',
        'MX:1',
        'ST:upnp:rootdevice',
        '',
        '',
    ]).encode('ascii', 'ignore')
    seen = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.settimeout(timeout)
        sock.sendto(message, ('239.255.255.250', 1900))
        while True:
            try:
                data, _addr = sock.recvfrom(8192)
            except socket.timeout:
                break
            text = data.decode('utf-8', 'ignore')
            location = ''
            for line in text.splitlines():
                if ':' not in line:
                    continue
                key, value = line.split(':', 1)
                if key.strip().lower() == 'location':
                    location = value.strip()
                    break
            if not location:
                continue
            m = re.search(r'https?://([^/:]+)', location, re.I)
            if not m:
                continue
            host = m.group(1).strip()
            if _looks_like_ip(host) and host not in seen:
                seen.add(host)
                yield host
    except Exception:
        return
    finally:
        try:
            sock.close()
        except Exception:
            pass


def discover_bridge_ip(timeout=5):
    seen = set()
    for source in (_candidate_bridge_ips_from_discovery_meethue, _candidate_bridge_ips_from_ssdp):
        for ip in source(timeout=timeout):
            if ip not in seen:
                seen.add(ip)
                yield ip


def first_discovered_bridge_ip(timeout=5):
    for ip in discover_bridge_ip(timeout=timeout):
        return ip
    return None


def reuse_existing_diyhue_whitelist(cfg, device_type=DEFAULT_DEVICE_TYPE):
    diyhue_cfg_path = cfg.get("diyhue_config_path", "/opt/diyhue/config/config.yaml")
    try:
        with open(diyhue_cfg_path, 'r', encoding='utf-8') as fh:
            diy = yaml.safe_load(fh) or {}
    except Exception:
        return False

    whitelist = diy.get('whitelist') or {}
    if not isinstance(whitelist, dict) or not whitelist:
        return False

    preferred = None
    fallback = None
    for username, entry in whitelist.items():
        if not isinstance(entry, dict):
            continue
        if fallback is None:
            fallback = (username, entry)
        if entry.get('name') == device_type:
            preferred = (username, entry)
            break

    selected = preferred or fallback
    if not selected:
        return False

    username, entry = selected
    cfg['app_key'] = username
    cfg['psk_identity'] = username
    if entry.get('client_key'):
        cfg['client_key'] = entry['client_key']
    return True


def sync_discovered_lights(cfg, bridge_ip, app_key):
    lights = None
    prefer_v2 = wants_v2_api(cfg)

    if not prefer_v2:
        try:
            v1 = list_lights_v1(bridge_ip, app_key)
            if isinstance(v1, dict) and v1:
                v1 = {k: v for k, v in v1.items() if is_probably_real_light_v1(v)}
                if v1:
                    lights = [
                        {
                            'id': str(lid),
                            'name': str((data or {}).get('name') or f'light_{lid}')
                        }
                        for lid, data in v1.items()
                    ]
        except Exception:
            lights = None

    if not lights:
        try:
            v2 = list_lights_v2(bridge_ip, app_key)
            extracted = []
            for item in (v2 or []):
                if not isinstance(item, dict):
                    continue
                name = str(((item.get('metadata') or {}).get('name')) or '')
                if prefer_v2:
                    rid = str(item.get('id') or '').strip()
                    if not rid:
                        continue
                    extracted.append({
                        'id': rid,
                        'name': name or f'light_{rid}',
                    })
                    continue
                id_v1 = str(item.get('id_v1') or '')
                if not id_v1.startswith('/lights/'):
                    continue
                lid = id_v1.split('/lights/', 1)[1].strip()
                if not lid:
                    continue
                extracted.append({
                    'id': lid,
                    'name': name or f'light_{lid}',
                })
            if extracted:
                lights = extracted
        except Exception:
            lights = None

    if not lights and prefer_v2:
        try:
            v1 = list_lights_v1(bridge_ip, app_key)
            if isinstance(v1, dict) and v1:
                v1 = {k: v for k, v in v1.items() if is_probably_real_light_v1(v)}
                if v1:
                    lights = [
                        {
                            'id': str(lid),
                            'name': str((data or {}).get('name') or f'light_{lid}')
                        }
                        for lid, data in v1.items()
                    ]
        except Exception:
            lights = None

    if not lights:
        return False

    existing = cfg.get('lights') or []
    existing_by_id = {
        str(item.get('id')): item
        for item in existing
        if isinstance(item, dict) and item.get('id') not in (None, '', 0, '0')
    }

    merged = []
    changed = False
    for item in lights:
        lid = str(item.get('id'))
        name = str(item.get('name') or f'light_{lid}')
        current = dict(existing_by_id.get(lid, {}))
        if not current:
            current = {
                'id': lid,
                'name': name,
                'enabled': True,
                'max_brightness': 180,
            }
            changed = True
        else:
            if current.get('id') in (None, '', 0, '0'):
                current['id'] = lid
                changed = True
            if current.get('name') != name and name:
                current['name'] = name
                changed = True
            current.setdefault('enabled', True)
            current.setdefault('max_brightness', 180)
        merged.append(current)

    if merged and (changed or not existing):
        cfg['lights'] = merged
        return True
    return False

    whitelist = diy.get('whitelist') or {}
    for username, entry in whitelist.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('name') == device_type and entry.get('client_key'):
            cfg['app_key'] = username
            cfg['client_key'] = entry['client_key']
            cfg['psk_identity'] = username
            return True
    return False


def load_config(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


def save_config(path, cfg):
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding='utf-8')


def resolved_bridge_ip(cfg, override=None):
    if override:
        return override
    if cfg.get('bridge_ip'):
        return cfg['bridge_ip']
    if cfg.get('backend_mode') == 'diyhue':
        return '127.0.0.1'
    discovered = first_discovered_bridge_ip(timeout=5)
    if discovered:
        return discovered
    return None


def diyhue_session(bridge_ip, username=DEFAULT_DIYHUE_USER, password=DEFAULT_DIYHUE_PASSWORD):
    sess = requests.Session()
    for scheme in ('http', 'https'):
        login_url = f"{scheme}://{bridge_ip}/login"
        try:
            resp = sess.get(login_url, verify=False, timeout=8)
            if resp.status_code >= 400:
                continue
            m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
            data = {'email': username, 'password': password}
            if m:
                data['csrf_token'] = m.group(1)
            post = sess.post(login_url, data=data, verify=False, timeout=8, allow_redirects=True)
            if post.status_code < 500:
                try:
                    sess.get(f"{scheme}://{bridge_ip}/", verify=False, timeout=5)
                except Exception:
                    pass
                return sess, scheme
        except Exception:
            continue
    return None, None


def activate_link_button_web(sess, scheme, bridge_ip):
    for path in ('/hue/linkbutton?username=&password=&action=Activate', '/#linkbutton'):
        try:
            resp = sess.get(f"{scheme}://{bridge_ip}{path}", verify=False, timeout=8, allow_redirects=True)
            if resp.status_code < 500:
                return True
        except Exception:
            continue
    return False


def press_linkbutton_with_known_user(bridge_ip, app_key):
    if not app_key:
        return False
    payload = {'linkbutton': True}
    for scheme in ('http', 'https'):
        url = f"{scheme}://{bridge_ip}/api/{app_key}/config"
        try:
            response = requests.put(url, json=payload, verify=False, timeout=5)
            if response.status_code >= 400:
                continue
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    success = item.get('success') if isinstance(item, dict) else None
                    if isinstance(success, dict) and '/config/linkbutton' in success:
                        return True
        except Exception:
            continue
    return False


def register_user(bridge_ip, device_type=DEFAULT_DEVICE_TYPE):
    last_error = None
    payload = {"devicetype": device_type, "generateclientkey": True}

    def _candidate_urls():
        # Modern Hue bridges prefer HTTPS and some will redirect HTTP in a way that
        # turns POST into GET when clients follow redirects automatically.
        yield f"https://{bridge_ip}/api"
        yield f"https://{bridge_ip}/api/"
        yield f"http://{bridge_ip}/api"
        yield f"http://{bridge_ip}/api/"

    for url in _candidate_urls():
        try:
            response = requests.post(
                url,
                json=payload,
                verify=False,
                timeout=5,
                allow_redirects=False,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code in (301, 302, 303, 307, 308):
                location = str(response.headers.get('Location') or '').strip()
                if location:
                    # Replay the POST ourselves to avoid requests converting POST->GET
                    # for legacy redirect codes such as 301/302/303.
                    redirected = requests.post(
                        location,
                        json=payload,
                        verify=False,
                        timeout=5,
                        allow_redirects=False,
                        headers={"Content-Type": "application/json"},
                    )
                    redirected.raise_for_status()
                    return redirected.json()
                last_error = RuntimeError(f'Hue bridge redirected registration request from {url} but did not provide a Location header')
                continue

            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError('Registration request failed')


def describe_registration_error(error_obj, bridge_ip=''):
    if isinstance(error_obj, dict):
        typ = error_obj.get('type')
        desc = str(error_obj.get('description') or '').strip()
        address = str(error_obj.get('address') or '').strip()
        if typ == 101:
            base = f'Press the physical link button on the Hue bridge at {bridge_ip or "the configured IP"} and try again.'
            if desc:
                base += f' Bridge said: {desc}.'
            return base
        detail = desc or 'Unknown Hue bridge registration error'
        if address:
            detail += f' (address {address})'
        return detail
    return str(error_obj) if error_obj not in (None, '') else 'Unknown Hue bridge registration error'


def _list_entertainment_configurations_v2(bridge_ip, app_key):
    for scheme in ('http', 'https'):
        url = f"{scheme}://{bridge_ip}/clip/v2/resource/entertainment_configuration"
        try:
            response = requests.get(url, headers={"hue-application-key": app_key}, verify=False, timeout=5)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data', []) if isinstance(payload, dict) else []
            if data:
                return data
        except Exception:
            continue
    return []


def _extract_v1_group_id_from_v2_item(item):
    id_v1 = str((item or {}).get('id_v1') or '').strip()
    if '/groups/' in id_v1:
        return id_v1.rsplit('/groups/', 1)[-1].strip('/')
    return ''


def discover_entertainment_group_v2(bridge_ip, app_key, prefer_v1_id=False, desired_name=''):
    desired_name = normalize_name(desired_name)
    data = _list_entertainment_configurations_v2(bridge_ip, app_key)
    if not data:
        return ''
    ordered = []
    if desired_name:
        ordered.extend([item for item in data if normalize_name(((item.get('metadata') or {}).get('name'))) == desired_name])
    ordered.extend([item for item in data if item not in ordered])
    for item in ordered:
        if prefer_v1_id:
            gid = _extract_v1_group_id_from_v2_item(item)
            if gid:
                return gid
        rid = str(item.get('id') or '').strip()
        if rid:
            return rid
    return ''


def discover_entertainment_group_v1(bridge_ip, app_key):
    url = f"http://{bridge_ip}/api/{app_key}/groups"
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        for gid, group in payload.items():
            if str(group.get('type', '')).lower() == 'entertainment':
                return str(gid)
    return ''




def wants_v2_api(cfg):
    mode = str((cfg or {}).get('backend_mode', '') or '').strip().lower()
    api_base_path = str((cfg or {}).get('api_base_path', '') or '').strip().lower()
    if mode in ('official_bridge_v2', 'real_bridge_v2'):
        return True
    return api_base_path.startswith('/clip/v2')

def discover_entertainment_group(bridge_ip, app_key, cfg=None):
    cfg = cfg or {}
    backend_mode = str(cfg.get('backend_mode', '')).lower()
    desired_name = str(cfg.get('entertainment_group_name') or 'Hue Disco Area')
    hybrid_real_bridge = backend_mode == 'official_bridge'
    if hybrid_real_bridge:
        order = (
            lambda ip, key: discover_entertainment_group_v2(ip, key, prefer_v1_id=not wants_v2_api(cfg), desired_name=desired_name),
            lambda ip, key: discover_entertainment_group_v1(ip, key),
            lambda ip, key: discover_entertainment_group_v2(ip, key, prefer_v1_id=False, desired_name=desired_name),
        )
    elif backend_mode == 'diyhue' or not wants_v2_api(cfg):
        order = (discover_entertainment_group_v1, lambda ip, key: discover_entertainment_group_v2(ip, key, desired_name=desired_name))
    else:
        order = (lambda ip, key: discover_entertainment_group_v2(ip, key, desired_name=desired_name), discover_entertainment_group_v1)
    for fn in order:
        try:
            value = fn(bridge_ip, app_key)
            if value:
                return value
        except Exception:
            continue
    return ''


def entertainment_group_has_members(bridge_ip, app_key, gid):
    if not gid:
        return False
    gid = str(gid).strip()
    try:
        response = requests.get(f"http://{bridge_ip}/api/{app_key}/groups", timeout=5)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and gid.isdigit():
            group = payload.get(gid, {})
            lights = group.get('lights', []) if isinstance(group, dict) else []
            locations = group.get('locations', {}) if isinstance(group, dict) else {}
            return bool(lights) or bool(locations)
    except Exception:
        pass
    try:
        for item in _list_entertainment_configurations_v2(bridge_ip, app_key):
            if str(item.get('id') or '').strip() == gid or _extract_v1_group_id_from_v2_item(item) == gid:
                channels = item.get('channels', []) if isinstance(item, dict) else []
                return bool(channels)
    except Exception:
        pass
    return False


def list_lights_v1(bridge_ip, app_key):
    url = f"http://{bridge_ip}/api/{app_key}/lights"
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def list_lights_v2(bridge_ip, app_key):
    for scheme in ('http', 'https'):
        url = f"{scheme}://{bridge_ip}/clip/v2/resource/light"
        try:
            response = requests.get(url, headers={"hue-application-key": app_key}, verify=False, timeout=5)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data', []) if isinstance(payload, dict) else []
            if data:
                return data
        except Exception:
            continue
    return []


def is_probably_real_light_v1(data):
    if not isinstance(data, dict):
        return False
    text = ' '.join(str(data.get(k, '')) for k in ('name', 'type', 'modelid', 'manufacturername')).strip().lower()
    blocked = ('coordinator', 'adapter', 'dongle', 'ezsp', 'mg21')
    return not any(word in text for word in blocked)

def normalize_name(name):
    return str(name or '').strip().lower()


def enabled_lights(cfg):
    return [light for light in cfg.get('lights', []) if light.get('enabled', True)]


def unresolved_enabled_lights(cfg):
    return [light for light in enabled_lights(cfg) if light.get('id') in (None, '', 0, '0')]


def _candidate_bridge_lights(cfg, bridge_ip, app_key):
    mode = str(cfg.get('backend_mode') or '').strip().lower()
    candidates = []
    seen = set()

    def add(light_id, name):
        lid = str(light_id or '').strip()
        if not lid or lid in seen:
            return
        seen.add(lid)
        candidates.append({'id': lid, 'name': str(name or '').strip()})

    if mode == 'official_bridge' and wants_v2_api(cfg):
        for item in list_lights_v2(bridge_ip, app_key):
            if not isinstance(item, dict):
                continue
            meta = item.get('metadata', {}) if isinstance(item.get('metadata'), dict) else {}
            add(item.get('id'), meta.get('name'))
        if candidates:
            return candidates

    try:
        lights_v1 = list_lights_v1(bridge_ip, app_key)
    except Exception:
        lights_v1 = {}
    for lid, data in (lights_v1 or {}).items():
        if is_probably_real_light_v1(data):
            add(lid, data.get('name'))

    if candidates or mode != 'official_bridge' or not wants_v2_api(cfg):
        return candidates

    for item in list_lights_v2(bridge_ip, app_key):
        if not isinstance(item, dict):
            continue
        meta = item.get('metadata', {}) if isinstance(item.get('metadata'), dict) else {}
        add(item.get('id'), meta.get('name'))
    return candidates


def resolve_config_lights(cfg, bridge_ip, app_key):
    changed = False
    candidates = _candidate_bridge_lights(cfg, bridge_ip, app_key)
    if not candidates:
        return False

    by_name = {normalize_name(item.get('name')): item.get('id') for item in candidates if normalize_name(item.get('name'))}
    assigned_ids = {str(light.get('id')) for light in cfg.get('lights', []) if light.get('id') not in (None, '', 0, '0')}
    unresolved = []

    for light in cfg.get('lights', []):
        current = light.get('id')
        if current not in (None, '', 0, '0'):
            continue
        resolved = by_name.get(normalize_name(light.get('name')))
        if resolved and resolved not in assigned_ids:
            light['id'] = int(resolved) if str(resolved).isdigit() else resolved
            assigned_ids.add(str(resolved))
            changed = True
        else:
            unresolved.append(light)

    available = [item for item in candidates if str(item.get('id')) not in assigned_ids]
    unresolved_enabled = [light for light in unresolved if light.get('enabled', True)]
    if unresolved_enabled and len(unresolved_enabled) == len(available):
        for light, item in zip(unresolved_enabled, available):
            resolved = item.get('id')
            light['id'] = int(resolved) if str(resolved).isdigit() else resolved
            light['bridge_name'] = item.get('name') or light.get('bridge_name') or ''
            assigned_ids.add(str(resolved))
            changed = True
    return changed


def create_entertainment_group_v1(bridge_ip, app_key, cfg):
    light_ids = []
    for light in cfg.get('lights', []):
        lid = light.get('id')
        if lid in (None, '', 0, '0'):
            continue
        light_ids.append(str(lid))
    if not light_ids:
        return ''

    url = f"http://{bridge_ip}/api/{app_key}/groups"
    payload = {
        'name': cfg.get('entertainment_group_name', 'Hue Disco Area'),
        'type': 'Entertainment',
        'class': 'Other',
        'lights': light_ids,
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            for item in data:
                success = item.get('success') if isinstance(item, dict) else None
                if isinstance(success, dict):
                    for key, value in success.items():
                        if key.endswith('/groups'):
                            return str(value)
                        if '/groups/' in key:
                            return key.rsplit('/', 1)[-1]
        created = discover_entertainment_group_v1(bridge_ip, app_key)
        if created:
            return created
    except Exception:
        pass
    if str((cfg or {}).get('backend_mode') or '').strip().lower() == 'official_bridge':
        return create_entertainment_group_v2(bridge_ip, app_key, cfg)
    return ''


def create_entertainment_group_v2(bridge_ip, app_key, cfg):
    lights = list_lights_v2(bridge_ip, app_key)
    if not lights:
        return ''

    allowed = {normalize_name(l.get('name')) for l in cfg.get('lights', []) if normalize_name(l.get('name'))}
    light_refs = []
    for item in lights:
        meta = item.get('metadata', {}) if isinstance(item, dict) else {}
        name = normalize_name(meta.get('name'))
        if allowed and name not in allowed:
            continue
        rid = item.get('id')
        if rid:
            light_refs.append({'rid': rid, 'rtype': 'light'})
    if not light_refs and lights:
        for item in lights:
            rid = item.get('id')
            if rid:
                light_refs.append({'rid': rid, 'rtype': 'light'})
    if not light_refs:
        return ''

    payload = {
        'type': 'entertainment_configuration',
        'metadata': {'name': cfg.get('entertainment_group_name', 'Hue Disco Area')},
        'configuration_type': '3dspace',
        'lights': light_refs,
    }
    for scheme in ('http', 'https'):
        url = f"{scheme}://{bridge_ip}/clip/v2/resource/entertainment_configuration"
        try:
            response = requests.post(url, headers={'hue-application-key': app_key}, json=payload, verify=False, timeout=8)
            if response.status_code >= 400:
                continue
            data = response.json()
            if isinstance(data, dict):
                created = data.get('data', [])
                if created and isinstance(created[0], dict):
                    rid = created[0].get('rid') or created[0].get('id')
                    if rid:
                        return str(rid)
        except Exception:
            continue
    return ''


def trigger_scan(bridge_ip):
    for scheme in ('http', 'https'):
        for path in ('/scan', '/api/config', '/'):  # /scan is the main action, others wake the app if needed
            try:
                requests.get(f"{scheme}://{bridge_ip}{path}", verify=False, timeout=5)
                return True
            except Exception:
                continue
    return False


def register_with_diyhue_login_flow(cfg, bridge_ip, timeout, device_type):
    sess, scheme = diyhue_session(
        bridge_ip,
        username=cfg.get('diyhue_web_username') or DEFAULT_DIYHUE_USER,
        password=cfg.get('diyhue_web_password') or DEFAULT_DIYHUE_PASSWORD,
    )
    if not sess:
        return None, 'Could not log in to diyHue web UI'

    activate_link_button_web(sess, scheme, bridge_ip)
    app_key = cfg.get('app_key', '')
    if app_key:
        press_linkbutton_with_known_user(bridge_ip, app_key)

    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            activate_link_button_web(sess, scheme, bridge_ip)
            response = sess.post(
                f"{scheme}://{bridge_ip}/api",
                json={'devicetype': device_type, 'generateclientkey': True},
                verify=False,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            if data and isinstance(data, list) and 'success' in data[0]:
                return data[0]['success'], None
            if data and isinstance(data, list) and 'error' in data[0]:
                last_error = describe_registration_error(data[0]['error'], bridge_ip=bridge_ip)
                if isinstance(last_error, dict) and int(last_error.get('type', 0)) == 101:
                    activate_link_button_web(sess, scheme, bridge_ip)
                    if app_key:
                        press_linkbutton_with_known_user(bridge_ip, app_key)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    return None, last_error


def try_populate_lights_and_group(cfg, bridge_ip, app_key, deadline):
    changed = False
    last_detail = ''
    while time.time() < deadline:
        if str((cfg or {}).get('backend_mode') or '').lower() == 'official_bridge' and not wants_v2_api(cfg):
            gid = str(cfg.get('entertainment_group_id') or '').strip()
            if gid and not gid.isdigit():
                cfg['entertainment_group_id'] = ''
                changed = True
        trigger_scan(bridge_ip)
        if resolve_config_lights(cfg, bridge_ip, app_key):
            changed = True
            save_config_args = True
        else:
            save_config_args = False

        if sync_discovered_lights(cfg, bridge_ip, app_key):
            changed = True
            save_config_args = True

        unresolved = unresolved_enabled_lights(cfg)
        if not cfg.get('entertainment_group_id') and cfg.get('auto_discover_entertainment_group', True):
            gid = discover_entertainment_group(bridge_ip, app_key, cfg)
            if gid:
                cfg['entertainment_group_id'] = gid
                changed = True
                save_config_args = True

        if cfg.get('entertainment_group_id') and not entertainment_group_has_members(bridge_ip, app_key, cfg.get('entertainment_group_id')):
            cfg['entertainment_group_id'] = ''
            changed = True
            save_config_args = True

        if not cfg.get('entertainment_group_id') and cfg.get('auto_create_entertainment_group', True) and not unresolved:
            mode = str(cfg.get('backend_mode', '')).lower()
            if mode == 'official_bridge':
                created = create_entertainment_group_v2(bridge_ip, app_key, cfg) or create_entertainment_group_v1(bridge_ip, app_key, cfg)
            elif mode == 'diyhue' or not wants_v2_api(cfg):
                created = create_entertainment_group_v1(bridge_ip, app_key, cfg) or (create_entertainment_group_v2(bridge_ip, app_key, cfg) if wants_v2_api(cfg) else '')
            else:
                created = create_entertainment_group_v2(bridge_ip, app_key, cfg) or create_entertainment_group_v1(bridge_ip, app_key, cfg)
            if created:
                cfg['entertainment_group_id'] = created
                changed = True
                save_config_args = True

        if save_config_args:
            save_config(CONFIG_PATH_HOLDER[0], cfg)

        if not unresolved and cfg.get('entertainment_group_id'):
            return changed, ''

        if unresolved:
            last_detail = 'Waiting for diyHue to discover lights: ' + ', '.join(str(x.get('name') or '<unnamed>') for x in unresolved)
        elif not cfg.get('entertainment_group_id'):
            last_detail = 'Lights are known, but no entertainment group exists yet'
        time.sleep(3)

    return changed, last_detail


CONFIG_PATH_HOLDER = ['']


def bootstrap(config_path, bridge_ip=None, device_type=DEFAULT_DEVICE_TYPE, timeout=90, pairing_wait_seconds=45, pairing_retry_interval=3):
    CONFIG_PATH_HOLDER[0] = config_path
    cfg = load_config(config_path)
    bridge_ip = resolved_bridge_ip(cfg, bridge_ip)
    if bridge_ip and not cfg.get('bridge_ip'):
        cfg['bridge_ip'] = bridge_ip
    if not bridge_ip:
        raise RuntimeError('Missing bridge_ip. Set it explicitly for official_bridge or use backend_mode=diyhue.')

    need_creds = not cfg.get('app_key') or not cfg.get('client_key')
    last_error = None

    if need_creds and cfg.get('backend_mode') == 'diyhue':
        success, last_error = register_with_diyhue_login_flow(cfg, bridge_ip, timeout, device_type)
        if success:
            cfg['bridge_ip'] = bridge_ip
            cfg['app_key'] = success['username']
            cfg['client_key'] = success.get('clientkey', cfg.get('client_key', ''))
            cfg['psk_identity'] = cfg.get('psk_identity') or cfg['app_key']
            need_creds = False

    deadline = time.time() + timeout
    printed_pairing_banner = False
    pair_deadline = time.time() + max(0, int(pairing_wait_seconds or 0))
    retry_sleep = max(1, int(pairing_retry_interval or 3))
    while need_creds and time.time() < deadline:
        try:
            data = register_user(bridge_ip, device_type=device_type)
            if data and isinstance(data, list) and 'success' in data[0]:
                success = data[0]['success']
                cfg['bridge_ip'] = bridge_ip
                cfg['app_key'] = success['username']
                cfg['client_key'] = success.get('clientkey', cfg.get('client_key', ''))
                cfg['psk_identity'] = cfg.get('psk_identity') or cfg['app_key']
                if printed_pairing_banner and cfg.get('backend_mode') == 'official_bridge':
                    print(f'[bootstrap] Bridge pairing succeeded for {bridge_ip}.', file=sys.stderr)
                need_creds = False
                break
            if data and isinstance(data, list) and 'error' in data[0]:
                err = data[0]['error']
                last_error = describe_registration_error(err, bridge_ip=bridge_ip)
                if isinstance(err, dict) and int(err.get('type', 0)) == 101 and cfg.get('backend_mode') == 'official_bridge':
                    remaining = max(0, int(pair_deadline - time.time()))
                    if not printed_pairing_banner:
                        print(
                            f'[bootstrap] Press the physical Hue bridge button now. Waiting up to {max(0, int(pairing_wait_seconds or 0))} seconds and retrying every {retry_sleep} seconds for bridge {bridge_ip}.',
                            file=sys.stderr,
                        )
                        printed_pairing_banner = True
                    else:
                        print(
                            f'[bootstrap] Link button still not pressed. Retrying for another {remaining} seconds...',
                            file=sys.stderr,
                        )
                    if time.time() >= pair_deadline:
                        break
        except Exception as exc:
            last_error = str(exc)
        time.sleep(retry_sleep)

    if need_creds and cfg.get('backend_mode') == 'diyhue':
        if reuse_existing_diyhue_whitelist(cfg, device_type=device_type):
            need_creds = False

    if need_creds:
        raise RuntimeError(f'Could not register Hue application credentials automatically: {last_error}')

    if not cfg.get('psk_identity'):
        cfg['psk_identity'] = cfg['app_key']

    if cfg.get('backend_mode') == 'diyhue':
        try:
            sess, scheme = diyhue_session(
                bridge_ip,
                username=cfg.get('diyhue_web_username') or DEFAULT_DIYHUE_USER,
                password=cfg.get('diyhue_web_password') or DEFAULT_DIYHUE_PASSWORD,
            )
            if sess:
                activate_link_button_web(sess, scheme, bridge_ip)
        except Exception:
            pass

    if cfg.get('app_key') and cfg.get('backend_mode') == 'diyhue':
        press_linkbutton_with_known_user(bridge_ip, cfg['app_key'])
        if not cfg.get('entertainment_group_id'):
            gid = discover_entertainment_group_v1(bridge_ip, cfg['app_key']) or discover_entertainment_group(bridge_ip, cfg['app_key'], cfg)
            if gid:
                cfg['entertainment_group_id'] = gid

    save_config(config_path, cfg)

    changed, wait_detail = try_populate_lights_and_group(cfg, bridge_ip, cfg['app_key'], deadline)

    # Force-refresh saved lights/groups after bootstrap so config.yaml matches
    # the currently discovered bridge state and entertainment area.
    try:
        sync_discovered_lights(cfg, bridge_ip, cfg['app_key'])
    except Exception:
        pass

    try:
        gid = str(cfg.get('entertainment_group_id') or '').strip()
        if str((cfg or {}).get('backend_mode') or '').lower() == 'official_bridge' and not wants_v2_api(cfg) and gid and not gid.isdigit():
            gid = ''
            cfg['entertainment_group_id'] = ''
        if gid:
            cfg['groups'] = [{'id': gid, 'name': str(cfg.get('entertainment_group_name') or 'Entertainment Area')}]
        else:
            cfg['groups'] = []
    except Exception:
        pass
    if changed:
        save_config(config_path, cfg)

    if not cfg.get('entertainment_group_id') or unresolved_enabled_lights(cfg):
        detail_parts = []
        if unresolved_enabled_lights(cfg):
            detail_parts.append('unresolved lights: ' + ', '.join(str(x.get('name') or '<unnamed>') for x in unresolved_enabled_lights(cfg)))
        if not cfg.get('entertainment_group_id'):
            detail_parts.append('entertainment_group_id is still empty')
        if wait_detail:
            detail_parts.append(wait_detail)
        raise RuntimeError('; '.join(detail_parts))

    save_config(config_path, cfg)
    return cfg


def main():
    parser = argparse.ArgumentParser(description='Register Hue/diyHue credentials and auto-fill the config file.')
    parser.add_argument('config_path')
    parser.add_argument('bridge_ip', nargs='?')
    parser.add_argument('--device-type', default=DEFAULT_DEVICE_TYPE)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--pairing-wait-seconds', type=int, default=45)
    parser.add_argument('--pairing-retry-interval', type=int, default=3)
    args = parser.parse_args()

    try:
        cfg = bootstrap(
            args.config_path,
            bridge_ip=args.bridge_ip,
            device_type=args.device_type,
            timeout=args.timeout,
            pairing_wait_seconds=args.pairing_wait_seconds,
            pairing_retry_interval=args.pairing_retry_interval,
        )
    except Exception as exc:
        print(json.dumps({'status': 'error', 'detail': str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({
        'status': 'ok',
        'bridge_ip': cfg.get('bridge_ip'),
        'app_key': cfg.get('app_key'),
        'client_key': cfg.get('client_key'),
        'psk_identity': cfg.get('psk_identity'),
        'entertainment_group_id': cfg.get('entertainment_group_id', ''),
    }))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
