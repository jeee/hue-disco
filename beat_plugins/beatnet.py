from __future__ import annotations

PLUGIN_NAME = 'beatnet'
DISPLAY_NAME = 'BeatNet neural beat tracker + PLL'

import collections
import collections.abc

import numpy as np

from beat_tracker_core import BeatTrackerConfig, FreshBeatTracker, Peak


def _compat_patch():
    # BeatNet/madmom are not fully Python 3.13 / NumPy 2 clean yet.
    for name in ['MutableSequence', 'MutableMapping', 'MutableSet', 'Sequence', 'Mapping', 'Set', 'Iterable']:
        if not hasattr(collections, name) and hasattr(collections.abc, name):
            setattr(collections, name, getattr(collections.abc, name))
    for name, value in [('float', float), ('int', int), ('complex', complex)]:
        if not hasattr(np, name):
            setattr(np, name, value)


def check_available():
    _compat_patch()
    from BeatNet.BeatNet import BeatNet  # noqa: F401
    return True


class BeatNetPLL(FreshBeatTracker):
    def __init__(self, cfg, model_no=2, device='cpu', eval_interval_s=0.50, buffer_seconds=8.0):
        _compat_patch()
        from BeatNet.BeatNet import BeatNet

        super().__init__(cfg)
        self.beatnet = BeatNet(int(model_no), mode='online', inference_model='PF', plot=[], thread=False, device=device)
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_seconds = float(buffer_seconds)
        self.eval_interval_s = float(eval_interval_s)
        self.last_eval = 0.0
        self.last_beatnet_peak = -999.0

    def process_block(self, mono, now=None):
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
        self.t_audio += len(mono) / self.cfg.sample_rate if mono.size else 0.0
        t_now = float(now) if now is not None else self.t_audio
        rms = float(np.sqrt(np.mean(mono * mono) + 1e-12)) if mono.size else 0.0

        if mono.size:
            self.buffer = np.concatenate((self.buffer, mono))
            max_samples = int(self.cfg.sample_rate * self.buffer_seconds)
            if len(self.buffer) > max_samples:
                self.buffer = self.buffer[-max_samples:]

        peak = None
        accepted = False
        have_enough = len(self.buffer) >= int(self.cfg.sample_rate * 2.0)
        due_eval = (t_now - self.last_eval) >= self.eval_interval_s
        if have_enough and due_eval and rms >= self.cfg.silence_rms:
            self.last_eval = t_now
            try:
                beats = np.asarray(self.beatnet.process(self.buffer.astype(np.float32, copy=False)))
            except Exception:
                beats = np.zeros((0, 2), dtype=np.float32)
            if beats.size:
                beats = beats.reshape((-1, beats.shape[-1]))
                beat_rel = float(beats[-1, 0])
                buffer_duration = len(self.buffer) / float(self.cfg.sample_rate)
                beat_abs = t_now - buffer_duration + beat_rel
                tail_age = t_now - beat_abs
                if 0.0 <= tail_age <= 0.35 and beat_abs - self.last_beatnet_peak >= self.cfg.min_peak_gap_s:
                    self.last_beatnet_peak = beat_abs
                    peak = Peak(t=beat_abs, strength=1.0, rms=rms)
                    accepted = self._observe(peak, t_now)

        self._decay(t_now, rms)
        due_t = self._due_fire(t_now)
        return self._update(
            peak is not None,
            peak.t if peak else 0.0,
            peak.strength if peak else 0.0,
            accepted,
            due_t is not None,
            due_t or 0.0,
            rms,
            1.0 if peak else 0.0,
        )


def create_tracker(config):
    cfg = BeatTrackerConfig(
        sample_rate=int(config.get('sample_rate', 22050)),
        hop_s=float(config.get('hop_s', config.get('hop_seconds', 0.02))),
        bpm_min=float(config.get('bpm_min', 100.0)),
        bpm_max=float(config.get('bpm_max', 165.0)),
        silence_rms=float(config.get('silence_rms', 0.0060)),
        flash_lead_s=float(config.get('flash_lead_s', 0.0)),
        accept_phase_s=float(config.get('accept_phase_s', 0.050)),
    )
    return BeatNetPLL(
        cfg,
        model_no=int(config.get('beatnet_model', 2)),
        device=str(config.get('device', 'cpu')),
        eval_interval_s=float(config.get('eval_interval_s', 0.50)),
        buffer_seconds=float(config.get('buffer_seconds', 8.0)),
    )
