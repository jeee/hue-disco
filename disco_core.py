import colorsys
import json
import math
import queue
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
import sounddevice as sd

try:
    import btrack_beat_tracker as btrack_bt
except Exception:
    btrack_bt = None

try:
    from beat_plugin_loader import create_plugin_tracker, plugin_statuses
except Exception:
    create_plugin_tracker = None
    plugin_statuses = None

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

try:
    import aubio
except Exception:
    aubio = None

from bootstrap_hue_credentials import bootstrap, resolved_bridge_ip as bootstrap_resolved_bridge_ip
from config_schema import PROFILE_DEFAULTS, load_config, save_config
from dtls_hue_stream import OpenSSLDTLSHueStream


@dataclass
class RuntimeState:
    mode: str = 'stopped'
    backend_mode: str = 'official_bridge'
    bridge_ip: str = ''
    entertainment_group_id: str = ''
    sample_rate: int = 44100
    block_size: int = 1024
    sensitivity: float = 1.12
    min_interval_ms: int = 140
    energy_decay: float = 0.92
    hue_step: int = 18
    beat_prediction_ms: int = 0
    beat_subdivision: int = 1
    change_every_beats: int = 1
    accent_every_beats: int = 4
    beat_mode: str = 'phase_locked'
    render_mode: str = 'hybrid'
    detector_backend: str = 'native'
    bpm_min: int = 100
    bpm_max: int = 165
    beat_band_low_hz: int = 45
    beat_band_high_hz: int = 135
    audio_device: Optional[str] = None
    strobe_seconds: int = 3
    strobe_on_ms: int = 20
    strobe_off_ms: int = 80
    strobe_sync_mode: str = 'free'
    intensity_mode: str = 'adaptive'
    grid_behavior: str = 'adaptive'
    active_profile: str = 'lounge'
    profiles: Dict[str, Dict] = field(default_factory=dict)
    strobe_presets: List[Dict] = field(default_factory=list)
    global_allowed_colors: List[str] = field(default_factory=list)
    lights: List[Dict] = field(default_factory=list)
    render_fps: int = 20
    last_error: str = ''
    bpm_estimate: float = 0.0
    bpm_confidence: float = 0.0
    phase_confidence: float = 0.0
    beat_count: int = 0
    last_energy: float = 0.0
    last_flux: float = 0.0
    last_band_energy: float = 0.0
    last_onset_score: float = 0.0
    last_interval_ms: float = 0.0
    last_trigger: float = 0.0
    last_colors: Dict[str, Dict[str, int]] = field(default_factory=dict)
    stream_active: bool = False
    last_render_source: str = ''


class MQTTReporter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        mc = cfg.get('mqtt', {})
        self.enabled = bool(mc.get('enabled', False)) and mqtt is not None
        self.topic_prefix = mc.get('topic_prefix', 'hue_disco')
        if self.enabled:
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                self.client.connect(mc.get('host', 'localhost'), int(mc.get('port', 1883)), 5)
                self.client.loop_start()
            except Exception:
                self.client = None
                self.enabled = False

    def publish(self, topic, payload):
        if self.enabled and self.client:
            try:
                self.client.publish(f"{self.topic_prefix}/{topic}", json.dumps(payload))
            except Exception:
                pass


class DiscoEngine:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.state = RuntimeState()
        self.audio_q = queue.Queue(maxsize=8)
        self.stream = None
        self.mqtt = MQTTReporter(self.cfg)
        self.running = False
        self.worker = None
        self.input_stream = None
        self.lock = threading.Lock()
        self.last_bootstrap_attempt = 0.0
        self.last_emit_time = 0.0
        self.last_detected_beat = 0.0
        self.last_onset_trigger = 0.0
        self.beat_intervals: List[float] = []
        self.phase_period = 0.0
        self.phase_anchor = 0.0
        self.phase_confidence = 0.0
        self.phase_last_index = -1
        self.energy_avg = 0.0
        self.flux_avg = 0.0
        self.band_energy_avg = 0.0
        self.prev_mag = None
        self.current_hue = 0.0
        self.override_until = 0.0
        self.override_rgb = (255, 255, 255)
        self.current_metrics = {'rms': 0.0, 'flux': 0.0, 'band_energy': 0.0, 'onset_score': 0.0}
        self.btrack_audio_buffer = np.zeros(0, dtype=np.float32)
        self.btrack_buffer_seconds = 2.0
        self.btrack_last_abs_time = 0.0
        self.btrack_eval_interval_s = 0.50
        self.btrack_last_eval = 0.0
        self.beattracker = None
        self.beattracker_input_rate = int(self.cfg.get('sample_rate', 44100))
        self.detector_init_error = ''
        self.detector_statuses = {}
        self.aubio_onset = None
        self.reload()

    def _resolved_bridge_ip(self, cfg=None):
        cfg = cfg or self.cfg
        return bootstrap_resolved_bridge_ip(cfg) or ''

    def _enabled_lights(self):
        lights = (self.cfg or {}).get('lights') or []
        return [light for light in lights if isinstance(light, dict) and light.get('enabled', True)]

    def _resolved_light_id(self, light):
        lid = light.get('id')
        if lid in (None, '', 0, '0'):
            return None
        return str(lid)

    def _unresolved_lights(self):
        return [light.get('name') or '<unnamed>' for light in self._enabled_lights() if self._resolved_light_id(light) is None]

    def _refresh_stream(self):
        self.stream = OpenSSLDTLSHueStream(
            self._resolved_bridge_ip(self.cfg),
            self.cfg.get('psk_identity') or self.cfg.get('app_key', ''),
            self.cfg.get('client_key', ''),
            self.cfg.get('entertainment_group_id', ''),
            api_app_key=self.cfg.get('app_key', ''),
            api_base_path=self.cfg.get('api_base_path', '/api'),
        )

    def _detector_statuses(self):
        statuses = {
            'native': {
                'name': 'native',
                'display_name': 'Native',
                'available': True,
                'message': 'Built in lightweight detector.',
            },
            'aubio': {
                'name': 'aubio',
                'display_name': 'aubio-ledfx',
                'available': aubio is not None,
                'message': 'Available' if aubio is not None else 'Python module aubio is not installed or failed to import.',
            },
            'btrack': {
                'name': 'btrack',
                'display_name': 'BTrack',
                'available': btrack_bt is not None,
                'message': 'Available' if btrack_bt is not None else 'Python module btrack_beat_tracker is not installed or failed to import.',
            },
        }
        plugin_map = {}
        if plugin_statuses is not None:
            try:
                plugin_map = plugin_statuses()
            except Exception as exc:
                plugin_map = {'_loader': {'available': False, 'message': str(exc)}}
        else:
            plugin_map = {'_loader': {'available': False, 'message': 'beat_plugin_loader could not be imported.'}}

        fresh = plugin_map.get('fresh_flux_pll') or {}
        loader_error = plugin_map.get('_loader', {}).get('message', 'Plugin is missing or failed to load.')
        statuses['beattracker'] = {
            'name': 'beattracker',
            'display_name': 'Native Flux PLL BeatTracker',
            'available': bool(fresh.get('available')),
            'message': fresh.get('message') or loader_error,
        }
        beatnet = plugin_map.get('beatnet') or {}
        statuses['beatnet'] = {
            'name': 'beatnet',
            'display_name': 'BeatNet neural BeatTracker',
            'available': bool(beatnet.get('available')),
            'message': beatnet.get('message') or 'BeatNet plugin is missing or failed to load.',
        }
        selected = str(self.cfg.get('detector_backend', 'native'))
        if selected == 'native_flux_pll':
            selected = 'beattracker'
        if selected in statuses and self.detector_init_error:
            statuses[selected] = dict(statuses[selected])
            statuses[selected]['available'] = False
            statuses[selected]['message'] = self.detector_init_error
        return statuses

    def _init_detectors(self):
        self.beattracker = None
        self.beattracker_input_rate = int(self.cfg.get('sample_rate', 44100))
        self.detector_init_error = ''
        if aubio is not None and str(self.cfg.get('detector_backend', 'native')) == 'aubio':
            try:
                self.aubio_onset = aubio.onset(
                    'specflux',
                    int(self.cfg.get('block_size', 1024)),
                    int(self.cfg.get('block_size', 1024)),
                    int(self.cfg.get('sample_rate', 44100)),
                )
                self.aubio_onset.set_silence(-40)
            except Exception:
                self.aubio_onset = None
        else:
            self.aubio_onset = None

        detector_backend = str(self.cfg.get('detector_backend', 'native'))
        beat_plugin_name = None
        if detector_backend in ('beattracker', 'native_flux_pll'):
            beat_plugin_name = 'fresh_flux_pll'
        elif detector_backend == 'beatnet':
            beat_plugin_name = 'beatnet'

        if beat_plugin_name:
            if create_plugin_tracker is None:
                self.detector_init_error = 'BeatTracker detector backend is not available: beat_plugin_loader could not be imported.'
                self._mark_error(self.detector_init_error)
                return
            try:
                source_rate = int(self.cfg.get('sample_rate', 44100))
                plugin_rate = 22050 if source_rate == 44100 else source_rate
                self.beattracker_input_rate = plugin_rate
                self.beattracker = create_plugin_tracker(beat_plugin_name, {
                    'sample_rate': plugin_rate,
                    'hop_s': max(0.001, int(self.cfg.get('block_size', 1024)) / float(max(1, source_rate))),
                    'bpm_min': float(self.cfg.get('bpm_min', 100)),
                    'bpm_max': float(self.cfg.get('bpm_max', 165)),
                    'silence_rms': 0.0060,
                    'accept_phase_s': max(0.010, int(self.cfg.get('min_interval_ms', 140)) / 1000.0 * 0.35),
                    'flash_lead_s': int(self.cfg.get('beat_prediction_ms', 0)) / 1000.0,
                    'beatnet_model': int(self.cfg.get('beatnet_model', 2)),
                })
            except Exception as exc:
                self.beattracker = None
                self.detector_init_error = f'BeatTracker detector backend failed to initialize: {exc}'
                self._mark_error(self.detector_init_error)

    def reload(self):
        self.cfg = load_config(self.config_path)
        self._init_detectors()
        self.detector_statuses = self._detector_statuses()
        self.mqtt = MQTTReporter(self.cfg)
        if self.cfg.get('app_key') and self.cfg.get('client_key'):
            self._clear_error()
        if self.detector_init_error:
            self._mark_error(self.detector_init_error)
        with self.lock:
            self.state.backend_mode = self.cfg.get('backend_mode')
            self.state.bridge_ip = self._resolved_bridge_ip(self.cfg)
            self.state.entertainment_group_id = self.cfg.get('entertainment_group_id')
            self.state.sample_rate = int(self.cfg.get('sample_rate', 44100))
            self.state.block_size = int(self.cfg.get('block_size', 1024))
            self.state.sensitivity = float(self.cfg.get('sensitivity', 1.12))
            self.state.min_interval_ms = int(self.cfg.get('min_interval_ms', 140))
            self.state.energy_decay = float(self.cfg.get('energy_decay', 0.92))
            self.state.hue_step = int(self.cfg.get('hue_step', 18))
            self.state.beat_prediction_ms = int(self.cfg.get('beat_prediction_ms', 0))
            self.state.beat_subdivision = max(1, int(self.cfg.get('beat_subdivision', 1)))
            self.state.change_every_beats = max(1, int(self.cfg.get('change_every_beats', 1)))
            self.state.accent_every_beats = max(1, int(self.cfg.get('accent_every_beats', 4)))
            self.state.beat_mode = str(self.cfg.get('beat_mode', 'phase_locked'))
            self.state.render_mode = str(self.cfg.get('render_mode', 'hybrid'))
            self.state.detector_backend = str(self.cfg.get('detector_backend', 'native'))
            self.state.bpm_min = int(self.cfg.get('bpm_min', 100))
            self.state.bpm_max = int(self.cfg.get('bpm_max', 165))
            self.state.beat_band_low_hz = int(self.cfg.get('beat_band_low_hz', 45))
            self.state.beat_band_high_hz = int(self.cfg.get('beat_band_high_hz', 135))
            self.state.audio_device = self.cfg.get('audio_device')
            self.state.strobe_seconds = int(self.cfg.get('strobe_seconds', 3))
            self.state.strobe_on_ms = int(self.cfg.get('strobe_on_ms', 20))
            self.state.strobe_off_ms = int(self.cfg.get('strobe_off_ms', 80))
            self.state.strobe_sync_mode = str(self.cfg.get('strobe_sync_mode', 'free'))
            self.state.intensity_mode = str(self.cfg.get('intensity_mode', 'adaptive'))
            self.state.grid_behavior = str(self.cfg.get('grid_behavior', 'adaptive'))
            self.state.active_profile = str(self.cfg.get('active_profile', 'lounge'))
            self.state.profiles = deepcopy(self.cfg.get('profiles', {}))
            self.state.strobe_presets = deepcopy(self.cfg.get('strobe_presets', []))
            self.state.global_allowed_colors = list(self.cfg.get('limits', {}).get('global_allowed_colors', []))
            self.state.lights = deepcopy(self.cfg.get('lights') or [])
            self.state.render_fps = int(self.cfg.get('render_fps', 20))
        self._refresh_stream()

    def _mark_error(self, message):
        self.state.last_error = str(message)
        self.mqtt.publish('state', {'mode': self.state.mode, 'error': self.state.last_error})

    def _clear_error(self):
        self.state.last_error = ''

    def _attempt_bootstrap(self, force=False, timeout=20):
        if self.cfg.get('backend_mode') not in ('diyhue', 'official_bridge'):
            return False
        if self.cfg.get('app_key') and self.cfg.get('client_key'):
            self._clear_error()
            return False
        now = time.time()
        if not force and (now - self.last_bootstrap_attempt) < 10:
            return False
        self.last_bootstrap_attempt = now
        try:
            bootstrap(self.config_path, timeout=timeout)
            self.reload()
            return True
        except Exception as exc:
            if not (self.cfg.get('app_key') and self.cfg.get('client_key')):
                self._mark_error(exc)
            return False

    def _has_runtime_prerequisites(self):
        if not self._resolved_bridge_ip(self.cfg):
            return False, 'Missing bridge_ip'
        if not self.cfg.get('app_key'):
            return False, 'Missing app_key'
        if not self.cfg.get('client_key'):
            return False, 'Missing client_key'
        if self._unresolved_lights():
            return False, 'Lights not discovered yet: ' + ', '.join(self._unresolved_lights())
        if not self.cfg.get('entertainment_group_id'):
            return False, 'Missing entertainment_group_id'
        return True, ''

    def _set_stream_active(self, active: bool):
        if str(self.cfg.get('backend_mode', '')).lower() != 'diyhue':
            self.state.stream_active = bool(active)
            return
        app_key = self.cfg.get('app_key', '')
        group_id = str(self.cfg.get('entertainment_group_id', '')).strip()
        bridge_ip = self._resolved_bridge_ip(self.cfg)
        if not app_key or not group_id or not bridge_ip:
            self.state.stream_active = False
            return
        url = f'http://{bridge_ip}/api/{app_key}/groups/{group_id}/action'
        response = requests.put(url, json={'stream': {'active': bool(active)}}, timeout=5)
        response.raise_for_status()
        self.state.stream_active = bool(active)

    def _ensure_input_stream(self):
        if self.input_stream is not None:
            return
        self.input_stream = sd.InputStream(
            device=self.cfg.get('audio_device'),
            channels=1,
            samplerate=int(self.cfg.get('sample_rate', 44100)),
            blocksize=int(self.cfg.get('block_size', 1024)),
            callback=self._audio_callback,
        )
        self.input_stream.start()

    def _hex_to_rgb(self, value: str) -> Tuple[int, int, int]:
        text = str(value or '').strip().lstrip('#')
        if len(text) != 6:
            return (255, 255, 255)
        try:
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return (255, 255, 255)

    def _system_palette(self):
        # Legacy last-resort fallback only. Normal room styling should come from profiles/light groups.
        return ['#FF0040', '#00C8FF', '#FFE600', '#65FF7A']

    def _active_profile(self):
        profiles = self.cfg.get('profiles', {})
        active = self.cfg.get('active_profile')
        if active in profiles:
            return profiles[active]
        return next(iter(profiles.values()), {'name': 'fallback', 'profile_defaults': deepcopy(PROFILE_DEFAULTS), 'light_groups': []})

    def _build_profile_groups(self):
        profile = deepcopy(self._active_profile())
        defaults = deepcopy(PROFILE_DEFAULTS)
        defaults.update(profile.get('profile_defaults') or {})
        groups = []
        assigned = set()
        for idx, group in enumerate(profile.get('light_groups', [])):
            settings = deepcopy(defaults)
            settings.update(group.get('overrides') or {})
            light_ids = [str(value) for value in group.get('light_ids', []) if str(value)]
            assigned.update(light_ids)
            groups.append({
                'id': str(group.get('id') or f'group_{idx + 1}'),
                'name': str(group.get('name') or f'Group {idx + 1}'),
                'role': str(group.get('role') or 'custom'),
                'light_ids': light_ids,
                'settings': settings,
            })
        unassigned = [self._resolved_light_id(light) for light in self._enabled_lights() if self._resolved_light_id(light) and self._resolved_light_id(light) not in assigned]
        if unassigned:
            groups.append({'id': 'fallback', 'name': 'Fallback', 'role': 'main', 'light_ids': unassigned, 'settings': deepcopy(defaults)})
        return defaults, groups

    def _palette_for_light(self, light, group_settings, profile_defaults):
        light_allowed = light.get('allowed_colors') or []
        if light_allowed:
            return light_allowed
        if group_settings.get('static_color'):
            return [group_settings['static_color']]
        group_palette = group_settings.get('palette_colors') or []
        if group_palette:
            return group_palette
        profile_palette = profile_defaults.get('palette_colors') or []
        if profile_palette:
            return profile_palette
        return self._system_palette()

    def _bias_palette(self, palette, bias):
        if not palette:
            return palette
        bias = str(bias or 'mixed').lower()
        if bias not in {'warm', 'cool', 'mixed', 'vivid', 'pastel', 'deep'}:
            return palette
        scored = []
        for color in palette:
            r, g, b = self._hex_to_rgb(color)
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            warmth = 1.0 - min(abs(h - 0.08), abs(h - 1.08))
            coolness = 1.0 - min(abs(h - 0.55), abs(h + 0.45))
            if bias == 'warm':
                score = warmth + (v * 0.15)
            elif bias == 'cool':
                score = coolness + (v * 0.15)
            elif bias == 'vivid':
                score = s + v * 0.1
            elif bias == 'pastel':
                score = (1.0 - s) + v * 0.2
            elif bias == 'deep':
                score = (s * 0.6) + ((1.0 - v) * 0.4)
            else:
                score = 0.0
            scored.append((score, color))
        if bias == 'mixed':
            return palette
        return [color for _, color in sorted(scored, reverse=True)]

    def _group_color(self, group_index, light, group_settings, profile_defaults):
        palette = self._bias_palette(self._palette_for_light(light, group_settings, profile_defaults), group_settings.get('palette_bias') or profile_defaults.get('palette_bias'))
        if palette:
            motion = max(0.0, float(group_settings.get('color_motion', 0.35)))
            step_scale = max(0.01, float(group_settings.get('color_step_scale', 0.5)))
            change_every = max(1, int(group_settings.get('change_every_beats', profile_defaults.get('change_every_beats', 1))))
            beat_index = max(0, self.state.beat_count // change_every)
            motion_phase = (time.time() * 0.18 * motion) + (self.current_hue * step_scale)
            idx = int((beat_index * step_scale + motion_phase + group_index * 0.23) * len(palette)) % len(palette)
            return self._hex_to_rgb(palette[idx])
        r, g, b = colorsys.hsv_to_rgb((self.current_hue + group_index * 0.11) % 1.0, 1.0, 1.0)
        return int(r * 255), int(g * 255), int(b * 255)

    def _normalize_audio_strength(self, audio_metrics):
        rms = max(0.0, min(1.0, float(audio_metrics.get('rms', 0.0)) * 10.0))
        flux = max(0.0, min(1.0, float(audio_metrics.get('flux', 0.0)) * 18.0))
        band_energy = max(0.0, min(1.0, float(audio_metrics.get('band_energy', 0.0)) * 0.25))
        onset_score = max(0.0, min(1.0, float(audio_metrics.get('onset_score', 0.0))))
        return max(rms * 0.36, flux * 0.70, band_energy * 0.58, onset_score * 0.85)

    def _has_live_audio(self, audio_metrics):
        return (
            float(audio_metrics.get('rms', 0.0) or 0.0) >= 0.012
            or float(audio_metrics.get('flux', 0.0) or 0.0) >= 0.010
            or float(audio_metrics.get('band_energy', 0.0) or 0.0) >= 0.10
        )

    def _compute_intensity(self, settings, audio_metrics):
        mode = str(settings.get('intensity_mode') or self.cfg.get('intensity_mode', 'adaptive'))
        min_level = max(0.0, min(1.5, float(settings.get('min_pulse_level', self.cfg.get('min_pulse_level', 0.18)))))
        max_level = max(min_level, min(1.5, float(settings.get('max_pulse_level', self.cfg.get('max_pulse_level', 1.0)))))
        gain = max(0.0, float(settings.get('audio_intensity_gain', self.cfg.get('audio_intensity_gain', 1.0))))
        gate = max(0.0, float(settings.get('audio_gate_threshold', self.cfg.get('audio_gate_threshold', 0.12))))
        audio_value = max(0.0, min(1.2, self._normalize_audio_strength(audio_metrics) * gain))
        if mode == 'fixed':
            intensity = max_level
        elif mode == 'audio':
            intensity = 0.0 if audio_value < gate else min(max_level, audio_value)
        else:
            intensity = min(max_level, max(min_level, min_level + (audio_value * (max_level - min_level))))
            if audio_value < gate:
                intensity = min_level * max(0.0, audio_value / max(gate, 1e-6))
        activity = max(0.0, min(2.0, float(settings.get('group_activity', 1.0))))
        return max(0.0, min(1.5, intensity * activity))

    def _grid_allows_pulse(self, settings, audio_metrics):
        if not self._has_live_audio(audio_metrics):
            return False
        behavior = str(settings.get('grid_behavior') or self.cfg.get('grid_behavior', 'adaptive'))
        gate = max(0.0, float(settings.get('audio_gate_threshold', self.cfg.get('audio_gate_threshold', 0.12))))
        evidence = self._normalize_audio_strength(audio_metrics)
        if behavior == 'continuous':
            return True
        if behavior == 'gated':
            return evidence >= gate
        return evidence >= (gate * 0.35)

    def _apply_brightness(self, rgb, brightness):
        scale = max(0.0, min(1.0, brightness / 255.0))
        return tuple(int(max(0, min(255, channel * scale))) for channel in rgb)

    def _pulse_envelope(self, settings, pulse_anchor, now):
        if pulse_anchor <= 0:
            return 0.0
        attack = max(1.0, float(settings.get('pulse_attack_ms', 80))) / 1000.0
        hold = max(0.0, float(settings.get('pulse_hold_ms', 120))) / 1000.0
        decay = max(1.0, float(settings.get('pulse_decay_ms', 260))) / 1000.0
        age = max(0.0, now - pulse_anchor)
        if age <= attack:
            return age / attack
        if age <= attack + hold:
            return 1.0
        tail_age = age - attack - hold
        if tail_age >= decay:
            return 0.0
        return max(0.0, 1.0 - (tail_age / decay))

    def _make_profile_payload(self, audio_metrics, now=None):
        now = now or time.time()
        profile_defaults, groups = self._build_profile_groups()
        chase_indices = [idx for idx, group in enumerate(groups) if str(group['settings'].get('render_mode') or '').lower() == 'beat_chase']
        active_chase_idx = None
        if chase_indices:
            chase_durations = []
            for chase_idx in chase_indices:
                chase_settings = groups[chase_idx]['settings']
                chase_durations.append(max(1, int(chase_settings.get('change_every_beats', profile_defaults.get('change_every_beats', 1)))))
            cycle_len = max(1, sum(chase_durations))
            beat_pos = max(0, int(self.state.beat_count) - 1) % cycle_len
            cursor = 0
            for chase_idx, duration in zip(chase_indices, chase_durations):
                cursor += duration
                if beat_pos < cursor:
                    active_chase_idx = chase_idx
                    break
        payload = []
        for idx, group in enumerate(groups):
            settings = group['settings']
            render_mode = str(settings.get('render_mode') or self.cfg.get('render_mode', 'hybrid'))
            chase_active = render_mode == 'beat_chase' and (idx == active_chase_idx)
            base_brightness = max(0.0, min(255.0, float(settings.get('base_brightness', profile_defaults.get('base_brightness', 110)))))
            peak_brightness = max(base_brightness, min(255.0, float(settings.get('peak_brightness', profile_defaults.get('peak_brightness', 170)))))
            pulse_mix = max(0.0, min(1.0, float(settings.get('pulse_mix', profile_defaults.get('pulse_mix', 0.45)))))
            pulse_intensity = max(0.0, min(2.0, float(settings.get('pulse_intensity', profile_defaults.get('pulse_intensity', 0.7)))))
            intensity = self._compute_intensity(settings, audio_metrics)
            envelope = self._pulse_envelope(settings, self.state.last_trigger, now)
            accent_every = max(1, int(settings.get('accent_every_beats', profile_defaults.get('accent_every_beats', 4))))
            accent_multiplier = float(settings.get('accent_multiplier', profile_defaults.get('accent_multiplier', 1.08)))
            accent = accent_multiplier if self.state.beat_count and self.state.beat_count % accent_every == 0 else 1.0
            pulse_strength = max(0.0, min(1.5, intensity * envelope * pulse_intensity * accent))
            for light in self._enabled_lights():
                lid = self._resolved_light_id(light)
                if lid is None or lid not in group['light_ids']:
                    continue
                color_rgb = self._group_color(idx, light, settings, profile_defaults)
                base_rgb = self._apply_brightness(color_rgb, base_brightness)
                dynamic_brightness = base_brightness + ((peak_brightness - base_brightness) * min(1.0, pulse_strength))
                if render_mode == 'beat_chase' and not chase_active:
                    off_pct = max(0.0, min(100.0, float(settings.get('chase_off_brightness', profile_defaults.get('chase_off_brightness', 0.0))))) / 100.0
                    final_rgb = self._apply_brightness(color_rgb, base_brightness * off_pct)
                elif render_mode == 'beat_chase' and chase_active:
                    final_rgb = self._apply_brightness(color_rgb, peak_brightness)
                elif render_mode == 'color_only':
                    final_rgb = self._apply_brightness(color_rgb, max(base_brightness, peak_brightness * 0.75))
                elif render_mode == 'pulse_only':
                    mono = int(max(0, min(255, dynamic_brightness)))
                    final_rgb = (mono, mono, mono)
                else:
                    pulse_white = self._apply_brightness((255, 255, 255), dynamic_brightness * pulse_mix * max(0.2, pulse_strength))
                    color_layer_strength = 1.0 if render_mode in {'hybrid', 'beat_chase'} else 0.0
                    if render_mode in {'hybrid', 'beat_chase'}:
                        color_layer_strength = max(0.2, 1.0 - (pulse_mix * 0.45))
                    color_layer = self._apply_brightness(base_rgb, dynamic_brightness * color_layer_strength)
                    final_rgb = tuple(min(255, color_layer[i] + pulse_white[i]) for i in range(3))
                cap = int(light.get('max_brightness', self.cfg.get('default_brightness', 180)))
                final_rgb = self._apply_brightness(final_rgb, cap)
                payload.append({'id': lid, 'rgb': final_rgb})
        return payload

    def _make_payload(self, rgb, ignore_brightness=False):
        payload = []
        for light in self._enabled_lights():
            lid = self._resolved_light_id(light)
            if lid is None:
                continue
            scaled = tuple(int(max(0, min(255, channel))) for channel in rgb)
            if not ignore_brightness:
                max_brightness = int(light.get('max_brightness', self.cfg.get('default_brightness', 180)))
                scaled = self._apply_brightness(scaled, max_brightness)
            payload.append({'id': lid, 'rgb': scaled})
        return payload

    def _audio_callback(self, indata, frames, time_info, status):
        mono = np.mean(indata, axis=1).astype(np.float32)
        try:
            self.audio_q.put_nowait(mono.copy())
        except queue.Full:
            try:
                self.audio_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.audio_q.put_nowait(mono.copy())
            except queue.Full:
                pass

    def _update_last_colors(self, payload):
        self.state.last_colors = {str(item['id']): {'r': item['rgb'][0], 'g': item['rgb'][1], 'b': item['rgb'][2]} for item in payload}

    def _emit_payload(self, payload, source, extra=None):
        if not payload:
            return
        self.stream.send(payload)
        self.last_emit_time = time.time()
        self._update_last_colors(payload)
        self.state.last_render_source = source
        data = {
            'source': source,
            'bpm': self.state.bpm_estimate,
            'bpm_confidence': self.state.bpm_confidence,
            'phase_confidence': round(self.phase_confidence, 3),
            'profile': self.cfg.get('active_profile'),
            'rgb': payload[0]['rgb'] if payload else None,
        }
        if extra:
            data.update(extra)
        self.mqtt.publish('beat', data)

    def _emit_calibration_pulse(self, source, extra=None, level=1.0):
        level = max(0.0, min(1.0, float(level)))
        white = int(round(255.0 * (0.18 + (0.82 * level))))
        hold_s = 0.030 + (0.070 * level)

        payload_on = self._make_payload((white, white, white), ignore_brightness=True)
        if payload_on:
            self.stream.send(payload_on)
            self._update_last_colors(payload_on)
            time.sleep(hold_s)

        payload_off = self._make_payload((0, 0, 0), ignore_brightness=True)
        if payload_off:
            self.stream.send(payload_off)
            self._update_last_colors(payload_off)

        self.last_emit_time = time.time()
        self.state.last_render_source = source
        data = {
            'source': source,
            'bpm': self.state.bpm_estimate,
            'bpm_confidence': self.state.bpm_confidence,
            'phase_confidence': round(self.phase_confidence, 3),
            'profile': self.cfg.get('active_profile'),
            'rgb': (white, white, white),
            'level': round(level, 3),
        }
        if extra:
            data.update(extra)
        self.mqtt.publish('beat', data)

    def start(self):
        if self.running:
            self.override_until = 0.0
            self.state.mode = 'disco'
            self.mqtt.publish('state', {'mode': self.state.mode})
            return
        self.running = True
        self.state.mode = 'starting'
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.mqtt.publish('state', {'mode': self.state.mode})

    def stop(self):
        self.running = False
        self.state.mode = 'stopped'
        self.state.stream_active = False
        if self.input_stream:
            try:
                self.input_stream.stop()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
        try:
            self._set_stream_active(False)
        except Exception:
            pass
        self.mqtt.publish('state', {'mode': 'stopped'})

    def _update_bpm_from_trigger(self, triggered_at):
        if self.last_onset_trigger > 0:
            interval = triggered_at - self.last_onset_trigger
            self.state.last_interval_ms = round(interval * 1000.0, 1)
            min_bpm = int(self.cfg.get('bpm_min', 100))
            max_bpm = int(self.cfg.get('bpm_max', 165))
            if interval > 0:
                candidates = [60.0 / interval]
                if interval > 0.35:
                    candidates.append(120.0 / interval)
                candidates = [bpm for bpm in candidates if min_bpm <= bpm <= max_bpm]
                if candidates:
                    target = self.state.bpm_estimate or ((min_bpm + max_bpm) / 2.0)
                    bpm = min(candidates, key=lambda item: abs(item - target))
                    corrected_interval = 60.0 / bpm

                    pending = list(getattr(self, '_pending_trigger_intervals', []))
                    pending.append(corrected_interval)
                    pending = pending[-2:]
                    self._pending_trigger_intervals = pending

                    if len(pending) >= 2:
                        avg_pending = float(np.mean(pending))
                        spread_pending = float(np.std(pending))
                        pending_conf = max(0.0, 1.0 - min(1.0, spread_pending / max(0.001, avg_pending * 0.12)))

                        existing_period = float(self.phase_period or 0.0)
                        close_to_existing = (
                            existing_period <= 0.0
                            or abs(avg_pending - existing_period) <= (existing_period * 0.12)
                        )

                        if pending_conf >= 0.55 and close_to_existing:
                            self.beat_intervals.append(avg_pending)
                            self.beat_intervals = self.beat_intervals[-10:]
                            avg = float(np.mean(self.beat_intervals))
                            self.state.bpm_estimate = round(60.0 / avg, 1)
                            spread = float(np.std(self.beat_intervals)) if len(self.beat_intervals) > 1 else spread_pending
                            conf = max(0.0, 1.0 - min(1.0, spread / max(0.001, avg * 0.18)))
                            conf = max(conf, pending_conf)
                            self.state.bpm_confidence = round(conf, 3)
                            self.phase_period = avg
                            if self.phase_anchor <= 0:
                                self.phase_anchor = triggered_at
                            else:
                                predicted_beats = round((triggered_at - self.phase_anchor) / max(avg, 1e-6))
                                self.phase_anchor = triggered_at - (predicted_beats * avg)
                            self.phase_last_index = math.floor((triggered_at - self.phase_anchor) / max(avg, 1e-6))
                            self.phase_confidence = max(self.phase_confidence * 0.94, conf)
                            self.state.phase_confidence = round(self.phase_confidence, 3)
                        else:
                            existing_lock_ok = (
                                self.phase_period > 0.0
                                and float(self.state.bpm_estimate or 0.0) > 0.0
                                and (
                                    float(self.state.bpm_confidence or 0.0) >= 0.20
                                    or float(self.phase_confidence or 0.0) >= 0.20
                                )
                            )

                            if existing_lock_ok:
                                self.state.bpm_confidence = round(max(0.0, float(self.state.bpm_confidence or 0.0) * 0.97), 3)
                                self.phase_confidence = max(0.0, float(self.phase_confidence or 0.0) * 0.97)
                                self.state.phase_confidence = round(self.phase_confidence, 3)
                            else:
                                self.state.bpm_confidence = 0.0
                                self.state.phase_confidence = 0.0
                                self.phase_confidence = 0.0
                                self.state.bpm_estimate = 0.0
                                self.phase_period = 0.0
                                self.phase_anchor = 0.0
                                self.phase_last_index = -1
                                self._next_phase_fire_at = 0.0
                                self._next_phase_fire_period = 0.0
        self.last_onset_trigger = triggered_at

    def _should_emit_frame(self, now):
        fps = max(5, int(self.cfg.get('render_fps', 20)))
        min_gap = 1.0 / fps
        return (now - self.last_emit_time) >= min_gap

    def _detect_audio_metrics(self, mono):
        now = time.time()
        self._detector_forced_trigger = False
        rms = float(np.sqrt(np.mean(np.square(mono))))
        spec = np.abs(np.fft.rfft(mono))
        flux = 0.0
        if self.prev_mag is not None:
            flux = float(np.mean(np.maximum(0, spec - self.prev_mag)))
        self.prev_mag = spec
        freqs = np.fft.rfftfreq(len(mono), d=1.0 / max(1, int(self.cfg.get('sample_rate', 44100))))
        low_hz = float(self.cfg.get('beat_band_low_hz', 45))
        high_hz = float(self.cfg.get('beat_band_high_hz', 135))
        mask = (freqs >= low_hz) & (freqs <= high_hz)
        band_energy = float(np.mean(spec[mask])) if np.any(mask) else float(np.mean(spec))
        decay = float(self.cfg.get('energy_decay', 0.92))
        self.energy_avg = (self.energy_avg * decay) + (rms * (1.0 - decay))
        self.flux_avg = (self.flux_avg * decay) + (flux * (1.0 - decay))
        self.band_energy_avg = (self.band_energy_avg * decay) + (band_energy * (1.0 - decay))
        onset_score = max(
            rms / max(1e-6, self.energy_avg * max(0.25, float(self.cfg.get('sensitivity', 1.12)))),
            flux / max(1e-6, self.flux_avg * max(0.25, float(self.cfg.get('sensitivity', 1.12)))),
            band_energy / max(1e-6, self.band_energy_avg * max(0.25, float(self.cfg.get('sensitivity', 1.12)))),
        )
        if self.aubio_onset is not None:
            try:
                aubio_hit = float(self.aubio_onset(mono.astype(np.float32)))
                if aubio_hit > 0:
                    onset_score = max(onset_score, 1.18)
            except Exception:
                pass
        metrics = {'rms': rms, 'flux': flux, 'band_energy': band_energy, 'onset_score': max(0.0, min(1.5, onset_score - 0.5))}

        if str(self.cfg.get('detector_backend', 'native')) == 'btrack' and btrack_bt is not None:
            try:
                sr = int(self.cfg.get('sample_rate', 44100)) // 2
                mono_f32 = mono.astype(np.float32, copy=False)
                mono_ds = mono_f32[::2]

                self.btrack_audio_buffer = np.concatenate((self.btrack_audio_buffer, mono_ds))
                max_samples = max(sr * 2, int(sr * float(getattr(self, 'btrack_buffer_seconds', 2.0))))
                if len(self.btrack_audio_buffer) > max_samples:
                    self.btrack_audio_buffer = self.btrack_audio_buffer[-max_samples:]

                live_gate = (
                    metrics['rms'] >= 0.008
                    or metrics['band_energy'] >= 0.35
                    or metrics['flux'] >= 0.04
                )

                should_eval_btrack = (
                    live_gate
                    and len(self.btrack_audio_buffer) >= int(sr * 2.0)
                    and (now - float(getattr(self, 'btrack_last_eval', 0.0) or 0.0)) >= float(getattr(self, 'btrack_eval_interval_s', 0.50))
                )

                if should_eval_btrack:
                    self.btrack_last_eval = now
                    beats = btrack_bt.detect_beats(self.btrack_audio_buffer)
                    if len(beats) > 0:
                        buffer_duration = len(self.btrack_audio_buffer) / float(sr)
                        last_beat_rel = float(beats[-1]) * 2.0  # compensate for downsampling
                        tail_age = max(0.0, buffer_duration - last_beat_rel)
                        beat_abs = now - tail_age

                        if (
                            tail_age <= 0.12
                            and (beat_abs - float(getattr(self, 'btrack_last_abs_time', 0.0) or 0.0)) >= 0.18
                        ):
                            self.btrack_last_abs_time = beat_abs
                            onset_score = max(onset_score, 1.35)
                            metrics['onset_score'] = max(metrics['onset_score'], 1.20)
            except Exception:
                pass

        if str(self.cfg.get('detector_backend', 'native')) in ('beattracker', 'native_flux_pll', 'beatnet') and self.beattracker is not None:
            try:
                bt_mono = mono.astype(np.float32, copy=False)
                if int(self.cfg.get('sample_rate', 44100)) == 44100 and int(getattr(self, 'beattracker_input_rate', 44100)) == 22050:
                    bt_mono = bt_mono[::2]
                update = self.beattracker.process_block(bt_mono, now=now)
                self.state.bpm_estimate = round(float(update.bpm or self.state.bpm_estimate or 0.0), 1)
                self.state.bpm_confidence = round(max(float(self.state.bpm_confidence or 0.0), float(update.confidence or 0.0)), 3)
                self.phase_confidence = max(float(self.phase_confidence or 0.0), float(update.confidence or 0.0))
                self.state.phase_confidence = round(self.phase_confidence, 3)
                if update.bpm and update.bpm > 0:
                    self.phase_period = 60.0 / float(update.bpm)
                    if self.phase_anchor <= 0.0:
                        self.phase_anchor = float(update.beat_time or update.peak_time or now)
                if (update.accepted_peak or update.beat_due) and self._has_live_audio(metrics):
                    self._detector_forced_trigger = True
                    onset_score = max(onset_score, 1.35)
                    metrics['onset_score'] = max(metrics['onset_score'], 1.20)
            except Exception as exc:
                self._mark_error(f'BeatTracker detector backend failed while processing audio: {exc}')

        self.current_metrics = metrics
        self.state.last_energy = round(rms, 4)
        self.state.last_flux = round(flux, 4)
        self.state.last_band_energy = round(band_energy, 4)
        self.state.last_onset_score = round(metrics['onset_score'], 4)
        return now, onset_score, metrics

    def _handle_trigger(self, now):
        self.state.last_trigger = now
        self.last_detected_beat = now
        self.state.beat_count += 1
        if self.state.beat_count % max(1, int(self.cfg.get('change_every_beats', 1))) == 0:
            self.current_hue = (self.current_hue + (float(self.cfg.get('hue_step', 18)) / 360.0)) % 1.0

    def _worker(self):
        while self.running:
            try:
                self.reload()
                ready, reason = self._has_runtime_prerequisites()
                if not ready:
                    self.state.mode = 'waiting'
                    self._mark_error(reason)
                    self._attempt_bootstrap(force=False, timeout=15)
                    time.sleep(2)
                    continue
                self._clear_error()
                self._ensure_input_stream()
                self._set_stream_active(True)
                self.stream.connect()
                self.state.mode = 'disco'
                while self.running:
                    try:
                        mono = self.audio_q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    now, onset_score_raw, metrics = self._detect_audio_metrics(mono)
                    if self.state.mode in ('manual_on', 'manual_off') and time.time() >= float(getattr(self, 'override_until', 0.0) or 0.0):
                        self.state.mode = 'disco'
                    predicted_now = now + (int(self.cfg.get('beat_prediction_ms', 0)) / 1000.0)
                    min_interval = int(self.cfg.get('min_interval_ms', 140)) / 1000.0
                    triggered = False
                    current_rms = float(metrics.get('rms', 0.0) or 0.0)
                    current_flux = float(metrics.get('flux', 0.0) or 0.0)
                    current_band = float(metrics.get('band_energy', 0.0) or 0.0)

                    has_live_audio = self._has_live_audio(metrics)
                    strong_live_trigger = has_live_audio and (
                        onset_score_raw >= 1.0
                        and current_rms >= 0.006
                        and current_flux >= 0.010
                        and current_band >= 0.18
                    ) or (has_live_audio and bool(getattr(self, '_detector_forced_trigger', False)))

                    if strong_live_trigger:
                        self._last_live_beat_evidence = predicted_now

                    if strong_live_trigger and (now - self.last_onset_trigger) >= min_interval:
                        triggered = True
                        self._update_bpm_from_trigger(predicted_now)
                    beat_mode = str(self.cfg.get('beat_mode', 'phase_locked'))
                    render_mode = str(self.cfg.get('render_mode', 'hybrid'))
                    render_source = 'frame'
                    if beat_mode == 'onset' and triggered:
                        self._handle_trigger(predicted_now)
                        render_source = 'onset'
                        if render_mode == 'calibration':
                            self._emit_calibration_pulse('calibration', metrics)
                            continue
                    elif beat_mode == 'phase_locked' and self.phase_period > 0 and self.phase_confidence >= 0.20:
                        beat_subdivision = max(1, int(self.cfg.get('beat_subdivision', 1)))
                        sub_period = max(self.phase_period / beat_subdivision, 1e-6)
                        next_fire = getattr(self, '_next_phase_fire_at', 0.0)
                        next_period = getattr(self, '_next_phase_fire_period', 0.0)

                        if (
                            next_fire <= 0.0
                            or abs(next_period - sub_period) > (sub_period * 0.20)
                            or next_fire < (predicted_now - sub_period)
                            or next_fire > (predicted_now + (sub_period * 2.0))
                        ):
                            current_index = math.floor((predicted_now - self.phase_anchor) / sub_period)
                            next_fire = self.phase_anchor + ((current_index + 1) * sub_period)
                            self._next_phase_fire_at = next_fire
                            self._next_phase_fire_period = sub_period

                        if predicted_now >= self._next_phase_fire_at:
                            fire_at = self._next_phase_fire_at
                            self._next_phase_fire_at = fire_at + sub_period
                            self._next_phase_fire_period = sub_period
                            profile_defaults, _ = self._build_profile_groups()

                            if render_mode == 'calibration':
                                last_trigger = float(self.last_onset_trigger or 0.0)
                                grace_window = max(min_interval * 1.4, sub_period * 2.0)
                                has_recent_real_trigger = (
                                    last_trigger > 0.0 and (predicted_now - last_trigger) <= grace_window
                                )

                                current_onset = float(metrics.get('onset_score', 0.0) or 0.0)
                                current_band = float(metrics.get('band_energy', 0.0) or 0.0)
                                current_flux = float(metrics.get('flux', 0.0) or 0.0)

                                weak_current_support = (
                                    current_onset >= 0.08
                                    or current_band >= 0.10
                                    or current_flux >= 0.015
                                )

                                lock_ok = (
                                    self.phase_period > 0.0
                                    and self.phase_confidence >= 0.20
                                    and float(self.state.bpm_confidence or 0.0) >= 0.20
                                )

                                should_flash = has_recent_real_trigger and weak_current_support and lock_ok

                                if should_flash:
                                    self._handle_trigger(fire_at)
                                    render_source = 'phase_locked'
                                    flash_level = max(
                                        0.12,
                                        min(1.0, max(current_onset, min(1.0, current_band / 2.5)))
                                    )
                                    self._emit_calibration_pulse(
                                        'calibration_mainbeat_grace',
                                        metrics,
                                        level=flash_level,
                                    )
                                else:
                                    self.state.last_render_source = 'calibration_suppressed_after_grace'
                                continue

                            if self._grid_allows_pulse(profile_defaults, metrics):
                                self._handle_trigger(fire_at)
                                render_source = 'phase_locked'
                        else:
                            self.state.bpm_confidence = max(0.0, round(self.state.bpm_confidence * 0.995, 3))
                            self.phase_confidence = max(0.0, self.phase_confidence * 0.997)
                            self.state.phase_confidence = round(self.phase_confidence, 3)
                    stale_trigger_age = float((now - self.last_onset_trigger) * 1000.0) if self.last_onset_trigger > 0 else 0.0

                    if (
                        self.phase_period > 0.0
                        and (
                            (
                                float(self.state.bpm_confidence or 0.0) < 0.08
                                and float(self.phase_confidence or 0.0) < 0.15
                            )
                            or stale_trigger_age > 4000.0
                        )
                    ):
                        self.state.bpm_estimate = 0.0
                        self.state.bpm_confidence = 0.0
                        self.phase_period = 0.0
                        self.phase_anchor = 0.0
                        self.phase_confidence = 0.0
                        self.state.phase_confidence = 0.0
                        self.phase_last_index = -1
                        self._next_phase_fire_at = 0.0
                        self._next_phase_fire_period = 0.0

                    if render_mode != 'calibration' and self._should_emit_frame(now) and (has_live_audio or render_source != 'frame'):
                        payload = self._make_profile_payload(metrics, now=now)
                        if payload:
                            self._emit_payload(payload, render_source, metrics)
            except Exception as exc:
                self._mark_error(exc)
                self.state.stream_active = False
                try:
                    if self.stream:
                        self.stream.close()
                except Exception:
                    pass
                time.sleep(3)

    def _send_override(self, rgb, mode):
        self.reload()
        ready, reason = self._has_runtime_prerequisites()
        if not ready:
            self._attempt_bootstrap(force=True, timeout=15)
            self.reload()
            ready, reason = self._has_runtime_prerequisites()
            if not ready:
                self.state.mode = 'waiting'
                self._mark_error(reason)
                return False
        self.override_rgb = rgb
        self.override_until = time.time() + 1.5
        self.state.mode = mode
        payload = self._make_payload(self.override_rgb)
        if not payload:
            self._mark_error('No resolved light IDs available for streaming')
            self.state.mode = 'waiting'
            return False
        self._set_stream_active(True)
        self.stream.connect()
        self.stream.send(payload)
        self._update_last_colors(payload)
        self.mqtt.publish('state', {'mode': mode})
        return True

    def lights_on(self):
        self._send_override((255, 255, 255), 'manual_on')

    def lights_off(self):
        self._send_override((0, 0, 0), 'manual_off')

    def _effective_strobe_timings(self, on_ms, off_ms, sync_mode=None):
        sync_mode = sync_mode or self.cfg.get('strobe_sync_mode', 'free')
        on_ms = max(10, int(on_ms))
        off_ms = max(10, int(off_ms))
        if sync_mode != 'beat_locked':
            return on_ms, off_ms
        bpm = float(self.state.bpm_estimate or ((int(self.cfg.get('bpm_min', 100)) + int(self.cfg.get('bpm_max', 165))) / 2.0))
        beat_ms = max(250.0, 60000.0 / max(1.0, bpm))
        preferred_cycle = max(20.0, on_ms + off_ms)
        best = None
        max_flashes = min(8, max(1, int(beat_ms / max(20.0, on_ms + 20))))
        for flashes in range(1, max_flashes + 1):
            cycle = beat_ms / flashes
            adjusted_off = max(20.0, cycle - on_ms)
            error = abs((on_ms + off_ms) - cycle)
            candidate = (error, flashes, int(round(adjusted_off)))
            if best is None or candidate < best:
                best = candidate
        return on_ms, best[2] if best else off_ms

    def strobe(self, seconds=None, on_ms=None, off_ms=None, sync_mode=None):
        seconds = float(seconds or self.cfg.get('strobe_seconds', 3))
        ready, reason = self._has_runtime_prerequisites()
        if not ready:
            self._attempt_bootstrap(force=True, timeout=15)
            self.reload()
            ready, reason = self._has_runtime_prerequisites()
            if not ready:
                self.state.mode = 'waiting'
                self._mark_error(reason)
                return False
        self.state.mode = 'strobe'
        self._set_stream_active(True)
        self.stream.connect()
        end = time.time() + seconds
        on_ms, off_ms = self._effective_strobe_timings(on_ms or self.cfg.get('strobe_on_ms', 20), off_ms or self.cfg.get('strobe_off_ms', 80), sync_mode=sync_mode)
        while time.time() < end:
            payload_on = self._make_payload((255, 255, 255), ignore_brightness=True)
            with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                f.write(f"payload_on len={len(payload_on) if payload_on else 0}\n")
            if payload_on:
                with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                    f.write("before send on\n")
                self.stream.send(payload_on)
                with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                    f.write("after send on\n")
                self._update_last_colors(payload_on)
            time.sleep(on_ms / 1000.0)
            payload_off = self._make_payload((0, 0, 0), ignore_brightness=True)
            with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                f.write(f"payload_off len={len(payload_off) if payload_off else 0}\n")
            if payload_off:
                with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                    f.write("before send off\n")
                self.stream.send(payload_off)
                with open("/tmp/hue-disco-strobe.log", "a", encoding="utf-8") as f:
                    f.write("after send off\n")
                self._update_last_colors(payload_off)
            time.sleep(off_ms / 1000.0)
        self.state.mode = 'disco' if self.running else 'stopped'
        return True

    def run_strobe_preset(self, name):
        for preset in self.cfg.get('strobe_presets', []):
            if str(preset.get('name')) == str(name):
                return self.strobe(
                    seconds=float(preset.get('duration', self.cfg.get('strobe_seconds', 3))),
                    on_ms=int(preset.get('on_ms', self.cfg.get('strobe_on_ms', 20))),
                    off_ms=int(preset.get('off_ms', self.cfg.get('strobe_off_ms', 80))),
                    sync_mode=str(preset.get('sync_mode', self.cfg.get('strobe_sync_mode', 'free'))),
                )
        return False

    def set_active_profile(self, name):
        cfg = load_config(self.config_path)
        profiles = cfg.get('profiles', {}) or {}
        if isinstance(profiles, list):
            profiles = {str(item.get('name')): item for item in profiles if isinstance(item, dict) and item.get('name')}
        if name not in profiles:
            return False
        cfg['active_profile'] = name
        save_config(self.config_path, cfg)
        self.reload()
        return True

    def get_live_state(self):
        return {
            'mode': self.state.mode,
            'backend_mode': self.state.backend_mode,
            'detector_backend': self.cfg.get('detector_backend', 'native'),
            'detector_statuses': self.detector_statuses,
            'beat_mode': self.state.beat_mode,
            'render_mode': self.state.render_mode,
            'active_profile': self.state.active_profile,
            'stream_active': self.state.stream_active,
            'bpm': self.state.bpm_estimate,
            'bpm_estimate': self.state.bpm_estimate,
            'bpm_confidence': self.state.bpm_confidence,
            'phase_confidence': self.state.phase_confidence,
            'phase_period_ms': round(self.phase_period * 1000.0, 1) if self.phase_period > 0 else 0.0,
            'phase_anchor_age_ms': round((time.time() - self.phase_anchor) * 1000.0, 1) if self.phase_anchor > 0 else 0.0,
            'next_phase_fire_in_ms': round(max(0.0, (getattr(self, '_next_phase_fire_at', 0.0) - time.time()) * 1000.0), 1) if getattr(self, '_next_phase_fire_at', 0.0) > 0 else 0.0,
            'last_onset_trigger_age_ms': round((time.time() - self.last_onset_trigger) * 1000.0, 1) if self.last_onset_trigger > 0 else 0.0,
            'pending_trigger_intervals_ms': [round(v * 1000.0, 1) for v in getattr(self, '_pending_trigger_intervals', [])],
            'beat_count': self.state.beat_count,
            'last_energy': self.state.last_energy,
            'last_flux': self.state.last_flux,
            'last_band_energy': self.state.last_band_energy,
            'last_onset_score': self.state.last_onset_score,
            'last_error': self.state.last_error,
            'last_render_source': self.state.last_render_source,
            'entertainment_group_id': self.state.entertainment_group_id,
        }

    def get_state(self):
        profile = self._active_profile()
        defaults, groups = self._build_profile_groups()
        return {
            'mode': self.state.mode,
            'backend_mode': self.state.backend_mode,
            'detector_backend': self.cfg.get('detector_backend', 'native'),
            'detector_statuses': self.detector_statuses,
            'bridge_ip': self.state.bridge_ip,
            'entertainment_group_id': self.state.entertainment_group_id,
            'sample_rate': self.state.sample_rate,
            'block_size': self.state.block_size,
            'sensitivity': self.state.sensitivity,
            'min_interval_ms': self.state.min_interval_ms,
            'energy_decay': self.state.energy_decay,
            'hue_step': self.state.hue_step,
            'beat_prediction_ms': self.state.beat_prediction_ms,
            'beat_subdivision': self.state.beat_subdivision,
            'change_every_beats': self.state.change_every_beats,
            'accent_every_beats': self.state.accent_every_beats,
            'beat_mode': self.state.beat_mode,
            'render_mode': self.state.render_mode,
            'intensity_mode': self.state.intensity_mode,
            'grid_behavior': self.state.grid_behavior,
            'bpm_min': int(self.cfg.get('bpm_min', 100)),
            'bpm_max': int(self.cfg.get('bpm_max', 165)),
            'beat_band_low_hz': int(self.cfg.get('beat_band_low_hz', 45)),
            'beat_band_high_hz': int(self.cfg.get('beat_band_high_hz', 135)),
            'audio_device': self.state.audio_device,
            'strobe_seconds': self.state.strobe_seconds,
            'strobe_on_ms': self.state.strobe_on_ms,
            'strobe_off_ms': self.state.strobe_off_ms,
            'strobe_sync_mode': self.state.strobe_sync_mode,
            'lights': self.state.lights,
            'profiles': self.state.profiles,
            'active_profile': self.state.active_profile,
            'active_profile_data': profile,
            'resolved_profile_groups': groups,
            'profile_defaults': defaults,
            'strobe_presets': self.state.strobe_presets,
            'last_error': self.state.last_error,
            'bpm': self.state.bpm_estimate,
            'bpm_estimate': self.state.bpm_estimate,
            'bpm_confidence': self.state.bpm_confidence,
            'phase_confidence': self.state.phase_confidence,
            'phase_period_ms': round(self.phase_period * 1000.0, 1) if self.phase_period > 0 else 0.0,
            'phase_anchor_age_ms': round((time.time() - self.phase_anchor) * 1000.0, 1) if self.phase_anchor > 0 else 0.0,
            'next_phase_fire_in_ms': round(max(0.0, (getattr(self, '_next_phase_fire_at', 0.0) - time.time()) * 1000.0), 1) if getattr(self, '_next_phase_fire_at', 0.0) > 0 else 0.0,
            'last_onset_trigger_age_ms': round((time.time() - self.last_onset_trigger) * 1000.0, 1) if self.last_onset_trigger > 0 else 0.0,
            'pending_trigger_intervals_ms': [round(v * 1000.0, 1) for v in getattr(self, '_pending_trigger_intervals', [])],
            'beat_count': self.state.beat_count,
            'last_energy': self.state.last_energy,
            'last_flux': self.state.last_flux,
            'last_band_energy': self.state.last_band_energy,
            'last_onset_score': self.state.last_onset_score,
            'last_interval_ms': self.state.last_interval_ms,
            'last_trigger': self.state.last_trigger,
            'last_colors': self.state.last_colors,
            'stream_active': self.state.stream_active,
            'render_fps': self.state.render_fps,
            'last_render_source': self.state.last_render_source,
        }

    def save_admin_settings(self, values):
        cfg = load_config(self.config_path)
        cfg['bridge_ip'] = values.get('bridge_ip', cfg.get('bridge_ip', ''))
        cfg['entertainment_group_id'] = values.get('entertainment_group_id', cfg.get('entertainment_group_id', ''))
        cfg['beat_mode'] = values.get('beat_mode', cfg.get('beat_mode', 'phase_locked')) or 'phase_locked'
        cfg['render_mode'] = values.get('render_mode', cfg.get('render_mode', 'hybrid')) or 'hybrid'
        cfg['detector_backend'] = values.get('detector_backend', cfg.get('detector_backend', 'native')) or 'native'
        cfg['intensity_mode'] = values.get('intensity_mode', cfg.get('intensity_mode', 'adaptive')) or 'adaptive'
        cfg['grid_behavior'] = values.get('grid_behavior', cfg.get('grid_behavior', 'adaptive')) or 'adaptive'
        cfg['strobe_sync_mode'] = values.get('strobe_sync_mode', cfg.get('strobe_sync_mode', 'free')) or 'free'
        cfg['active_profile'] = values.get('active_profile', cfg.get('active_profile', 'lounge')) or cfg.get('active_profile', 'lounge')
        for key in ('sample_rate', 'block_size', 'min_interval_ms', 'strobe_seconds', 'strobe_on_ms', 'strobe_off_ms', 'beat_prediction_ms', 'beat_subdivision', 'change_every_beats', 'accent_every_beats', 'bpm_min', 'bpm_max', 'beat_band_low_hz', 'beat_band_high_hz', 'render_fps'):
            if values.get(key) not in (None, ''):
                cfg[key] = int(float(values[key]))
        for key in ('sensitivity', 'energy_decay', 'hue_step', 'min_pulse_level', 'max_pulse_level', 'audio_intensity_gain', 'audio_gate_threshold'):
            if values.get(key) not in (None, ''):
                cfg[key] = float(values[key])
        audio_value = values.get('audio_device', '')
        cfg['audio_device'] = None if audio_value == '' else audio_value
        profiles_text = values.get('profiles_json', '').strip()
        if profiles_text:
            cfg['profiles'] = json.loads(profiles_text)
        presets_text = values.get('strobe_presets_json', '').strip()
        if presets_text:
            cfg['strobe_presets'] = json.loads(presets_text)
        save_config(self.config_path, cfg)
        self.reload()
