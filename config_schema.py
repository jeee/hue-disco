from copy import deepcopy
from pathlib import Path
import json
import time

import yaml

LEGACY_ROOT_KEYS = {
    'sample_rate', 'block_size', 'sensitivity', 'min_interval_ms', 'energy_decay',
    'beat_prediction_ms', 'beat_subdivision', 'change_every_beats', 'accent_every_beats',
    'hue_step', 'strobe_seconds', 'audio_device'
}

VALID_RENDER_MODES = {'calibration', 'color_only', 'pulse_only', 'hybrid', 'beat_chase'}
VALID_INTENSITY_MODES = {'fixed', 'audio', 'adaptive'}
VALID_GRID_BEHAVIORS = {'continuous', 'gated', 'adaptive'}
VALID_GROUP_ROLES = {'ambient', 'main', 'accent', 'background', 'percussive', 'melodic', 'custom'}
VALID_STROBE_SYNC = {'free', 'beat_locked'}


def _as_float(value, default, low=None, high=None):
    try:
        value = float(value)
    except Exception:
        value = float(default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _as_int(value, default, low=None, high=None):
    try:
        value = int(float(value))
    except Exception:
        value = int(default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _normalize_palette(values):
    out = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        if not text.startswith('#'):
            text = '#' + text
        if len(text) == 7:
            out.append(text.upper())
    return out


def _split_csv_palette(value):
    return _normalize_palette([part.strip() for part in str(value or '').split(',') if part.strip()])


def _profile_defaults_template():
    return {
        'render_mode': 'hybrid',
        'intensity_mode': 'adaptive',
        'grid_behavior': 'adaptive',
        'base_brightness': 110,
        'peak_brightness': 170,
        'pulse_attack_ms': 80,
        'pulse_hold_ms': 120,
        'pulse_decay_ms': 260,
        'pulse_mix': 0.45,
        'pulse_intensity': 0.7,
        'color_motion': 0.35,
        'color_step_scale': 0.5,
        'change_every_beats': 1,
        'beat_subdivision': 1,
        'accent_every_beats': 4,
        'accent_multiplier': 1.08,
        'min_pulse_level': 0.18,
        'max_pulse_level': 1.0,
        'audio_intensity_gain': 1.0,
        'audio_gate_threshold': 0.12,
        'group_activity': 1.0,
        'palette_colors': [],
        'palette_bias': 'mixed',
        'static_color': '',
        'chase_off_brightness': 0,
    }


PROFILE_DEFAULTS = _profile_defaults_template()

DEFAULTS = {
    'api_base_path': '/api',
    'backend_mode': 'diyhue',
    'bridge_ip': '',
    'app_key': '',
    'client_key': '',
    'psk_identity': '',
    'entertainment_group_id': '',
    'auto_register_diyhue': True,
    'auto_discover_entertainment_group': True,
    'auto_create_entertainment_group': True,
    'entertainment_group_name': 'Hue Disco Area',
    'sample_rate': 44100,
    'block_size': 1024,
    'sensitivity': 1.12,
    'min_interval_ms': 140,
    'energy_decay': 0.92,
    'beat_mode': 'phase_locked',
    'render_mode': 'hybrid',
    'detector_backend': 'native',
    'bpm_min': 100,
    'bpm_max': 165,
    'beat_band_low_hz': 45,
    'beat_band_high_hz': 135,
    'beat_prediction_ms': 0,
    'beat_subdivision': 1,
    'change_every_beats': 1,
    'accent_every_beats': 4,
    'hue_step': 18,
    'strobe_seconds': 3,
    'strobe_on_ms': 20,
    'strobe_off_ms': 80,
    'strobe_sync_mode': 'free',
    'max_transition_ms': 80,
    'default_brightness': 180,
    'audio_device': None,
    'intensity_mode': 'adaptive',
    'grid_behavior': 'adaptive',
    'min_pulse_level': 0.18,
    'max_pulse_level': 1.0,
    'audio_intensity_gain': 1.0,
    'audio_gate_threshold': 0.12,
    'active_profile': 'lounge',
    'render_fps': 20,
}


def _default_builtin_profiles():
    presets = {
        'lounge': dict(description='Soft warm hybrid motion for living-room listening.', defaults=dict(base_brightness=110, peak_brightness=170, pulse_attack_ms=80, pulse_hold_ms=120, pulse_decay_ms=260, pulse_mix=0.38, pulse_intensity=0.70, color_motion=0.35, color_step_scale=0.50, accent_every_beats=4, accent_multiplier=1.08, palette_colors=['#FFB26B', '#FFD166', '#F26CA7', '#7BDFF2'], intensity_mode='adaptive', grid_behavior='adaptive', render_mode='hybrid')),
        'house': dict(description='Steady dance pulse with stronger motion and 4-beat accents.', defaults=dict(base_brightness=115, peak_brightness=185, pulse_attack_ms=60, pulse_hold_ms=90, pulse_decay_ms=180, pulse_mix=0.55, pulse_intensity=0.85, color_motion=0.50, color_step_scale=0.70, accent_every_beats=4, accent_multiplier=1.12, palette_colors=['#00C8FF', '#7B61FF', '#FF3CAC', '#FFE600'], intensity_mode='adaptive', grid_behavior='continuous', render_mode='hybrid')),
        'disco': dict(description='Colorful classic disco profile with punchy accents.', defaults=dict(base_brightness=120, peak_brightness=210, pulse_attack_ms=45, pulse_hold_ms=85, pulse_decay_ms=160, pulse_mix=0.68, pulse_intensity=0.95, color_motion=0.72, color_step_scale=0.95, accent_every_beats=4, accent_multiplier=1.16, palette_colors=['#FF0040', '#00C8FF', '#FFE600', '#65FF7A'], intensity_mode='audio', grid_behavior='continuous', render_mode='hybrid')),
        'techno': dict(description='Sharper pulse-forward profile that tolerates continuous beat-grid pulsing.', defaults=dict(base_brightness=100, peak_brightness=220, pulse_attack_ms=30, pulse_hold_ms=70, pulse_decay_ms=120, pulse_mix=0.82, pulse_intensity=1.00, color_motion=0.55, color_step_scale=0.80, accent_every_beats=4, accent_multiplier=1.18, palette_colors=['#00C8FF', '#7B61FF', '#FF0040', '#FFE600'], intensity_mode='adaptive', grid_behavior='continuous', render_mode='hybrid')),
        'chill': dict(description='Low-motion, soft-response profile for quieter music.', defaults=dict(base_brightness=105, peak_brightness=155, pulse_attack_ms=110, pulse_hold_ms=120, pulse_decay_ms=320, pulse_mix=0.22, pulse_intensity=0.50, color_motion=0.28, color_step_scale=0.35, accent_every_beats=8, accent_multiplier=1.05, palette_colors=['#00C8FF', '#7B61FF', '#F26CA7', '#FFE600'], intensity_mode='adaptive', grid_behavior='gated', render_mode='hybrid')),
        'party': dict(description='High-energy profile for obvious dancefloor response.', defaults=dict(base_brightness=120, peak_brightness=225, pulse_attack_ms=35, pulse_hold_ms=80, pulse_decay_ms=140, pulse_mix=0.78, pulse_intensity=1.00, color_motion=0.88, color_step_scale=1.10, accent_every_beats=4, accent_multiplier=1.18, palette_colors=['#FF0040', '#00C8FF', '#FFE600', '#65FF7A', '#7B61FF'], intensity_mode='audio', grid_behavior='continuous', render_mode='hybrid')),
        'warm-up': dict(description='Gentle build-up profile before stronger dance lighting.', defaults=dict(base_brightness=105, peak_brightness=165, pulse_attack_ms=90, pulse_hold_ms=110, pulse_decay_ms=240, pulse_mix=0.32, pulse_intensity=0.62, color_motion=0.40, color_step_scale=0.45, accent_every_beats=4, accent_multiplier=1.07, palette_colors=['#FF9F1C', '#FFB000', '#00C8FF', '#2EC4B6'], intensity_mode='adaptive', grid_behavior='adaptive', render_mode='hybrid')),
        'jazz': dict(description='Subtle hybrid profile with softer accents and restrained motion.', defaults=dict(base_brightness=95, peak_brightness=150, pulse_attack_ms=100, pulse_hold_ms=140, pulse_decay_ms=300, pulse_mix=0.18, pulse_intensity=0.45, color_motion=0.22, color_step_scale=0.28, accent_every_beats=8, accent_multiplier=1.04, palette_colors=['#F4D35E', '#EE964B', '#F95738', '#7B61FF'], intensity_mode='adaptive', grid_behavior='gated', render_mode='hybrid')),
        'cuban': dict(description='More playful motion with stronger sync to current audio.', defaults=dict(base_brightness=110, peak_brightness=185, pulse_attack_ms=65, pulse_hold_ms=100, pulse_decay_ms=180, pulse_mix=0.48, pulse_intensity=0.82, color_motion=0.62, color_step_scale=0.65, accent_every_beats=2, accent_multiplier=1.10, palette_colors=['#F94144', '#F3722C', '#F9C74F', '#90BE6D'], intensity_mode='audio', grid_behavior='adaptive', render_mode='hybrid')),
        'rock': dict(description='Punchy but less color-driven than disco or party.', defaults=dict(base_brightness=115, peak_brightness=205, pulse_attack_ms=40, pulse_hold_ms=90, pulse_decay_ms=170, pulse_mix=0.62, pulse_intensity=0.92, color_motion=0.42, color_step_scale=0.52, accent_every_beats=4, accent_multiplier=1.14, palette_colors=['#F94144', '#F3722C', '#577590', '#7B61FF'], intensity_mode='audio', grid_behavior='adaptive', render_mode='hybrid')),
        'pop': dict(description='Bright pop-style hybrid pulse with lively color motion.', defaults=dict(base_brightness=112, peak_brightness=195, pulse_attack_ms=55, pulse_hold_ms=95, pulse_decay_ms=170, pulse_mix=0.58, pulse_intensity=0.88, color_motion=0.70, color_step_scale=0.78, accent_every_beats=4, accent_multiplier=1.12, palette_colors=['#FF5D8F', '#7B61FF', '#00C8FF', '#FFE066'], intensity_mode='audio', grid_behavior='adaptive', render_mode='hybrid')),
    }
    builtins = {}
    for name, item in presets.items():
        builtins[name] = {
            'name': name,
            'builtin': True,
            'description': item['description'],
            'profile_defaults': item['defaults'],
            'light_groups': [],
        }
    return builtins


def _merge_legacy_hue_disco(data):
    legacy = data.get('hue_disco') if isinstance(data, dict) else None
    if isinstance(legacy, dict):
        for key in LEGACY_ROOT_KEYS:
            if key in legacy and key not in data:
                data[key] = legacy[key]
    return data


def _normalize_profile_defaults(data):
    merged = deepcopy(PROFILE_DEFAULTS)
    merged.update(data or {})
    merged['render_mode'] = merged['render_mode'] if merged['render_mode'] in VALID_RENDER_MODES else 'hybrid'
    merged['intensity_mode'] = merged['intensity_mode'] if merged['intensity_mode'] in VALID_INTENSITY_MODES else 'adaptive'
    merged['grid_behavior'] = merged['grid_behavior'] if merged['grid_behavior'] in VALID_GRID_BEHAVIORS else 'adaptive'
    merged['base_brightness'] = _as_int(merged.get('base_brightness'), 110, 0, 255)
    merged['peak_brightness'] = _as_int(merged.get('peak_brightness'), 170, merged['base_brightness'], 255)
    merged['pulse_attack_ms'] = _as_int(merged.get('pulse_attack_ms'), 80, 0, 5000)
    merged['pulse_hold_ms'] = _as_int(merged.get('pulse_hold_ms'), 120, 0, 5000)
    merged['pulse_decay_ms'] = _as_int(merged.get('pulse_decay_ms'), 260, 1, 5000)
    merged['pulse_mix'] = _as_float(merged.get('pulse_mix'), 0.45, 0.0, 1.0)
    merged['pulse_intensity'] = _as_float(merged.get('pulse_intensity'), 0.7, 0.0, 2.0)
    merged['color_motion'] = _as_float(merged.get('color_motion'), 0.35, 0.0, 2.0)
    merged['color_step_scale'] = _as_float(merged.get('color_step_scale'), 0.5, 0.01, 3.0)
    merged['change_every_beats'] = _as_int(merged.get('change_every_beats'), 1, 1, 32)
    merged['beat_subdivision'] = _as_int(merged.get('beat_subdivision'), 1, 1, 8)
    merged['accent_every_beats'] = _as_int(merged.get('accent_every_beats'), 4, 1, 32)
    merged['accent_multiplier'] = _as_float(merged.get('accent_multiplier'), 1.08, 0.5, 3.0)
    merged['min_pulse_level'] = _as_float(merged.get('min_pulse_level'), 0.18, 0.0, 1.5)
    merged['max_pulse_level'] = _as_float(merged.get('max_pulse_level'), 1.0, merged['min_pulse_level'], 1.5)
    merged['audio_intensity_gain'] = _as_float(merged.get('audio_intensity_gain'), 1.0, 0.0, 5.0)
    merged['audio_gate_threshold'] = _as_float(merged.get('audio_gate_threshold'), 0.12, 0.0, 1.0)
    merged['group_activity'] = _as_float(merged.get('group_activity'), 1.0, 0.0, 2.0)
    merged['chase_off_brightness'] = _as_float(merged.get('chase_off_brightness'), 0.0, 0.0, 100.0)
    merged['palette_colors'] = _normalize_palette(merged.get('palette_colors') or [])
    merged['palette_bias'] = str(merged.get('palette_bias') or 'mixed')
    static_color = str(merged.get('static_color') or '').strip()
    merged['static_color'] = _normalize_palette([static_color])[0] if static_color else ''
    return merged


def _normalize_group(idx, group, defaults, valid_light_ids, used_light_ids):
    g = deepcopy(group or {})
    gid = str(g.get('id') or f'group_{idx + 1}')
    role = str(g.get('role') or 'custom')
    if role not in VALID_GROUP_ROLES:
        role = 'custom'
    light_ids = []
    for value in (g.get('light_ids') or g.get('members') or []):
        lid = str(value).strip()
        if not lid or lid not in valid_light_ids or lid in used_light_ids:
            continue
        used_light_ids.add(lid)
        light_ids.append(lid)
    overrides = _normalize_profile_defaults({**defaults, **(g.get('overrides') or {})})
    return {
        'id': gid,
        'name': str(g.get('name') or gid.replace('_', ' ').title()),
        'role': role,
        'enabled': bool(g.get('enabled', True)),
        'light_ids': light_ids,
        'overrides': overrides,
    }


def _normalize_profile(name, profile, lights):
    profile = deepcopy(profile or {})
    profile_defaults = _normalize_profile_defaults(profile.get('profile_defaults') or {})
    lights = lights or []
    valid_light_ids = {str(light.get('id')) for light in lights if isinstance(light, dict) and light.get('id') not in (None, '', 0, '0')}
    used_light_ids = set()
    groups = profile.get('light_groups') or profile.get('groups') or []
    normalized_groups = [_normalize_group(idx, group, profile_defaults, valid_light_ids, used_light_ids) for idx, group in enumerate(groups)]
    normalized_groups = [group for group in normalized_groups if group['enabled']]
    if not normalized_groups and valid_light_ids:
        normalized_groups = [{
            'id': 'all_lights',
            'name': 'All lights',
            'role': 'main',
            'enabled': True,
            'light_ids': sorted(valid_light_ids),
            'overrides': deepcopy(profile_defaults),
        }]
    return {
        'name': str(profile.get('name') or name),
        'builtin': bool(profile.get('builtin', False)),
        'description': str(profile.get('description') or ''),
        'profile_defaults': profile_defaults,
        'light_groups': normalized_groups,
    }



BACKUP_TOP_LEVEL_KEYS = [
    "api_base_path", "backend_mode", "bridge_ip", "app_key", "client_key", "psk_identity",
    "entertainment_group_id", "auto_register_diyhue", "auto_discover_entertainment_group",
    "auto_create_entertainment_group", "entertainment_group_name", "sample_rate", "block_size",
    "sensitivity", "min_interval_ms", "energy_decay", "beat_mode", "render_mode",
    "detector_backend", "bpm_min", "bpm_max", "beat_band_low_hz", "beat_band_high_hz",
    "intensity_mode", "min_pulse_level", "max_pulse_level", "audio_intensity_gain",
    "audio_gate_threshold", "grid_behavior", "render_fps", "beat_prediction_ms",
    "beat_subdivision", "change_every_beats", "accent_every_beats", "hue_step",
    "strobe_seconds", "strobe_on_ms", "strobe_off_ms", "strobe_sync_mode", "audio_device",
    "mqtt", "web", "limits", "lights", "groups", "profiles", "active_profile",
    "strobe_presets", "diyhue_config_path", "z2m_devices_path", "disco_autostart"
]


def export_settings_backup(cfg):
    cfg = load_config_dict(cfg)
    settings = {}
    for key in BACKUP_TOP_LEVEL_KEYS:
        if key in cfg:
            settings[key] = deepcopy(cfg[key])
    return {
        "format": "hue_disco_settings_backup",
        "backup_version": 1,
        "created_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "contains_secrets": True,
        "notes": "This backup may contain bridge keys, passwords, and session secrets. Store it securely.",
        "settings": settings,
        "raw_config_yaml": yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
    }


def parse_backup_payload(text):
    last_error = None
    for loader in (json.loads, yaml.safe_load):
        try:
            data = loader(text)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            last_error = exc
    if last_error:
        raise ValueError(f'Unsupported backup file: {last_error}')
    raise ValueError('Unsupported backup file')


def import_settings_backup(current_cfg, payload):
    data = deepcopy(payload or {})
    if data.get('format') == 'hue_disco_settings_backup' and isinstance(data.get('settings'), dict):
        data = deepcopy(data.get('settings') or {})
    elif 'settings' in data and isinstance(data.get('settings'), dict):
        data = deepcopy(data.get('settings') or {})
    merged = deepcopy(current_cfg or {})
    for key in BACKUP_TOP_LEVEL_KEYS:
        if key in data:
            merged[key] = deepcopy(data[key])
    return load_config_dict(merged)


def load_config_dict(data):
    data = deepcopy(data or {})
    data = _merge_legacy_hue_disco(data)
    merged = dict(DEFAULTS)
    merged.update(data)
    if not merged.get('psk_identity') and merged.get('app_key'):
        merged['psk_identity'] = merged['app_key']
    if not merged.get('bridge_ip') and merged.get('backend_mode') == 'diyhue':
        merged['bridge_ip'] = '127.0.0.1'
    merged.setdefault('mqtt', {'enabled': True, 'host': 'localhost', 'port': 1883, 'topic_prefix': 'hue_disco'})
    merged.setdefault('web', {})
    merged.setdefault('limits', {})  # legacy fallback-only support
    merged.setdefault('lights', [])
    merged.setdefault('groups', [])
    merged['render_fps'] = _as_int(merged.get('render_fps'), 20, 5, 40)
    merged['render_mode'] = str(merged.get('render_mode') or 'hybrid')
    if merged['render_mode'] == 'normal':
        merged['render_mode'] = 'hybrid'
    merged['limits']['global_allowed_colors'] = _normalize_palette(merged['limits'].get('global_allowed_colors') or [])
    presets = []
    for preset in (merged.get('strobe_presets') or []):
        if not isinstance(preset, dict):
            continue
        presets.append({
            'name': str(preset.get('name') or f'Preset {len(presets) + 1}'),
            'duration': _as_float(preset.get('duration'), merged.get('strobe_seconds', 3), 0.1, 60.0),
            'on_ms': _as_int(preset.get('on_ms'), merged.get('strobe_on_ms', 20), 10, 2000),
            'off_ms': _as_int(preset.get('off_ms'), merged.get('strobe_off_ms', 80), 10, 4000),
            'sync_mode': str(preset.get('sync_mode') or merged.get('strobe_sync_mode', 'free')) if str(preset.get('sync_mode') or merged.get('strobe_sync_mode', 'free')) in VALID_STROBE_SYNC else 'free',
        })
    if not presets:
        presets = [{'name': 'Quick strobe', 'duration': 3, 'on_ms': _as_int(merged.get('strobe_on_ms'), 20, 10, 2000), 'off_ms': _as_int(merged.get('strobe_off_ms'), 80, 10, 4000), 'sync_mode': str(merged.get('strobe_sync_mode') or 'free')}]
    merged['strobe_presets'] = presets

    builtins = _default_builtin_profiles()
    user_profiles = merged.get('profiles') or {}
    if isinstance(user_profiles, list):
        user_profiles = {str(item.get('name')): item for item in user_profiles if isinstance(item, dict) and item.get('name')}
    normalized_profiles = {}
    for name, profile in builtins.items():
        normalized_profiles[name] = _normalize_profile(name, profile, merged.get('lights', []))
    for name, profile in (user_profiles or {}).items():
        if not name:
            continue
        normalized_profiles[str(name)] = _normalize_profile(str(name), profile, merged.get('lights', []))
    merged['profiles'] = normalized_profiles
    active = str(merged.get('active_profile') or 'lounge')
    if active not in normalized_profiles and normalized_profiles:
        active = next(iter(normalized_profiles.keys()))
    merged['active_profile'] = active
    return merged


def load_config(path):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    data = _merge_legacy_hue_disco(data)
    merged = dict(DEFAULTS)
    merged.update(data)
    if not merged.get('psk_identity') and merged.get('app_key'):
        merged['psk_identity'] = merged['app_key']
    if not merged.get('bridge_ip') and merged.get('backend_mode') == 'diyhue':
        merged['bridge_ip'] = '127.0.0.1'
    merged.setdefault('mqtt', {'enabled': True, 'host': 'localhost', 'port': 1883, 'topic_prefix': 'hue_disco'})
    merged.setdefault('web', {})
    merged.setdefault('limits', {})  # legacy fallback-only support
    merged.setdefault('lights', [])
    merged.setdefault('groups', [])
    merged['render_fps'] = _as_int(merged.get('render_fps'), 20, 5, 40)
    merged['render_mode'] = str(merged.get('render_mode') or 'hybrid')
    if merged['render_mode'] == 'normal':
        merged['render_mode'] = 'hybrid'
    merged['limits']['global_allowed_colors'] = _normalize_palette(merged['limits'].get('global_allowed_colors') or [])
    presets = []
    for preset in (merged.get('strobe_presets') or []):
        if not isinstance(preset, dict):
            continue
        presets.append({
            'name': str(preset.get('name') or f'Preset {len(presets) + 1}'),
            'duration': _as_float(preset.get('duration'), merged.get('strobe_seconds', 3), 0.1, 60.0),
            'on_ms': _as_int(preset.get('on_ms'), merged.get('strobe_on_ms', 20), 10, 2000),
            'off_ms': _as_int(preset.get('off_ms'), merged.get('strobe_off_ms', 80), 10, 4000),
            'sync_mode': str(preset.get('sync_mode') or merged.get('strobe_sync_mode', 'free')) if str(preset.get('sync_mode') or merged.get('strobe_sync_mode', 'free')) in VALID_STROBE_SYNC else 'free',
        })
    if not presets:
        presets = [{'name': 'Quick strobe', 'duration': 3, 'on_ms': _as_int(merged.get('strobe_on_ms'), 20, 10, 2000), 'off_ms': _as_int(merged.get('strobe_off_ms'), 80, 10, 4000), 'sync_mode': str(merged.get('strobe_sync_mode') or 'free')}]
    merged['strobe_presets'] = presets

    builtins = _default_builtin_profiles()
    user_profiles = merged.get('profiles') or {}
    if isinstance(user_profiles, list):
        user_profiles = {str(item.get('name')): item for item in user_profiles if isinstance(item, dict) and item.get('name')}
    normalized_profiles = {}
    for name, profile in builtins.items():
        normalized_profiles[name] = _normalize_profile(name, profile, merged.get('lights', []))
    for name, profile in (user_profiles or {}).items():
        if not name:
            continue
        normalized_profiles[str(name)] = _normalize_profile(str(name), profile, merged.get('lights', []))
    merged['profiles'] = normalized_profiles
    active = str(merged.get('active_profile') or 'lounge')
    if active not in normalized_profiles and normalized_profiles:
        active = next(iter(normalized_profiles.keys()))
    merged['active_profile'] = active
    return merged


def save_config(path, cfg):
    cfg = deepcopy(cfg)
    if not cfg.get('psk_identity') and cfg.get('app_key'):
        cfg['psk_identity'] = cfg['app_key']
    cfg.pop('hue_disco', None)
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding='utf-8')
