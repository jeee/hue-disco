#!/usr/bin/env python3
"""Reusable beat tracker core.

No GPIO, no terminal UI, no ffmpeg. Feed mono float32 blocks and a timestamp;
it returns beat/grid events and status. This is intentionally importable by both
standalone LED tools and Hue Disco plugins.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def wrap_err(err: float, period: float) -> float:
    if period <= 0.0:
        return err
    return ((err + 0.5 * period) % period) - 0.5 * period


@dataclass
class BeatTrackerConfig:
    sample_rate: int = 22050
    hop_s: float = 0.02
    frame_s: float = 0.064
    bpm_min: float = 110.0
    bpm_max: float = 118.0
    silence_rms: float = 0.0082
    arm_rms: float = 0.0083
    arm_hits: int = 3
    arm_window_s: float = 0.75
    min_peak_gap_s: float = 0.18
    peak_abs_floor: float = 0.035
    peak_std_factor: float = 0.28
    peak_rel_factor: float = 1.03
    lock_min_peaks: int = 3
    lock_min_support: int = 2
    lock_min_conf: float = 0.34
    accept_phase_s: float = 0.035
    phase_gain: float = 0.32
    period_gain: float = 0.055
    stale_s: float = 2.0
    flash_lead_s: float = 0.0
    flash_lookahead_s: float = 0.060

    @property
    def min_period(self) -> float:
        return 60.0 / self.bpm_max

    @property
    def max_period(self) -> float:
        return 60.0 / self.bpm_min


@dataclass
class BeatUpdate:
    peak: bool = False
    peak_time: float = 0.0
    peak_strength: float = 0.0
    accepted_peak: bool = False
    beat_due: bool = False
    beat_time: float = 0.0
    locked: bool = False
    bpm: float = 0.0
    confidence: float = 0.0
    support: int = 0
    candidate_bpm: float = 0.0
    rms: float = 0.0
    onset_score: float = 0.0
    mean_abs_ms: Optional[float] = None
    p95_abs_ms: Optional[float] = None
    within40pct: Optional[float] = None


@dataclass
class Peak:
    t: float
    strength: float
    rms: float


@dataclass
class Candidate:
    period: float
    bpm: float
    support: int
    spread: float
    score: float
    conf: float


class FreshBeatTracker:
    """Spectral-flux onset detector + tempo/phase PLL."""

    plugin_name = "fresh_flux_pll"
    display_name = "Fresh spectral-flux PLL"

    def __init__(self, cfg: BeatTrackerConfig | None = None):
        self.cfg = cfg or BeatTrackerConfig()
        self.frame_len = max(256, int(round(self.cfg.frame_s * self.cfg.sample_rate)))
        self.hop_len = max(1, int(round(self.cfg.hop_s * self.cfg.sample_rate)))
        self.nfft = 1
        while self.nfft < self.frame_len:
            self.nfft <<= 1
        self.window = np.hanning(self.frame_len).astype(np.float32)
        self.framebuf: Deque[float] = collections.deque(maxlen=self.frame_len)
        self.t_audio = 0.0
        self.prev_spec: Optional[np.ndarray] = None
        self.band_avg = 0.0
        self.flux_avg = 0.0
        self.rms_avg = 0.0
        self.env = 0.0
        self.prev_env = 0.0
        self.last_peak_t = -1e9
        self.hist: Deque[tuple[float, float, float]] = collections.deque(maxlen=256)
        self.arm_times: Deque[float] = collections.deque(maxlen=32)
        self.peaks: Deque[Peak] = collections.deque(maxlen=24)
        self.locked = False
        self.period = 0.0
        self.last_good_period = 0.0
        self.anchor = 0.0
        self.conf = 0.0
        self.support = 0
        self.last_support_t = 0.0
        self.next_fire_t = 0.0
        self.candidate: Optional[Candidate] = None
        self.errors_ms: list[float] = []
        self.rejects = 0

    def process_block(self, mono: np.ndarray, now: Optional[float] = None) -> BeatUpdate:
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
        if mono.size == 0:
            return self._update(False, 0.0, 0.0, False, False, 0.0, 0.0, 0.0)
        self.framebuf.extend(mono.tolist())
        self.t_audio += len(mono) / self.cfg.sample_rate
        if len(self.framebuf) < self.frame_len:
            return self._update(False, 0.0, 0.0, False, False, 0.0, 0.0, 0.0)
        t_now = float(now) if now is not None else self.t_audio
        frame = np.asarray(self.framebuf, dtype=np.float32)
        center_t = t_now - 0.5 * (len(frame) / self.cfg.sample_rate)
        peak = self._detect_peak(frame, center_t)
        accepted = False
        if peak is not None:
            accepted = self._observe(peak, t_now)
        rms = float(np.sqrt(np.mean(frame * frame) + 1e-12))
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
            self.env,
        )

    def status(self) -> dict:
        bpm = 60.0 / (self.period if self.period > 0 else self.last_good_period) if (self.period > 0 or self.last_good_period > 0) else 0.0
        return {
            "locked": self.locked,
            "bpm": bpm,
            "period": self.period if self.period > 0 else self.last_good_period,
            "confidence": self.conf,
            "support": self.support,
            "candidate_bpm": self.candidate.bpm if self.candidate else 0.0,
            "peaks": len(self.peaks),
            "rejects": self.rejects,
        }

    def _update(self, peak, peak_time, peak_strength, accepted, beat_due, beat_time, rms, onset_score) -> BeatUpdate:
        errs = np.asarray(self.errors_ms, dtype=np.float64)
        st = self.status()
        return BeatUpdate(
            peak=peak,
            peak_time=peak_time,
            peak_strength=peak_strength,
            accepted_peak=accepted,
            beat_due=beat_due,
            beat_time=beat_time,
            locked=bool(st["locked"]),
            bpm=float(st["bpm"]),
            confidence=float(st["confidence"]),
            support=int(st["support"]),
            candidate_bpm=float(st["candidate_bpm"]),
            rms=float(rms),
            onset_score=float(onset_score),
            mean_abs_ms=float(np.mean(np.abs(errs))) if errs.size else None,
            p95_abs_ms=float(np.percentile(np.abs(errs), 95)) if errs.size else None,
            within40pct=float(np.mean(np.abs(errs) <= 40.0) * 100.0) if errs.size else None,
        )

    def _detect_peak(self, frame: np.ndarray, t_center: float) -> Optional[Peak]:
        rms = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        if rms >= self.cfg.arm_rms:
            self.arm_times.append(t_center)
        while self.arm_times and self.arm_times[0] < t_center - self.cfg.arm_window_s:
            self.arm_times.popleft()
        armed = len(self.arm_times) >= self.cfg.arm_hits
        spec = np.abs(np.fft.rfft(frame * self.window, n=self.nfft)).astype(np.float32)
        freqs = np.fft.rfftfreq(self.nfft, 1.0 / self.cfg.sample_rate)
        mask = (freqs >= 45.0) & (freqs <= 2200.0)
        band = spec[mask] if np.any(mask) else spec
        flux = 0.0 if self.prev_spec is None or len(self.prev_spec) != len(band) else float(np.mean(np.maximum(0.0, band - self.prev_spec)))
        self.prev_spec = band.copy()
        decay = 0.84
        band_mean = float(np.mean(band))
        self.band_avg = decay * self.band_avg + (1.0 - decay) * band_mean
        self.flux_avg = decay * self.flux_avg + (1.0 - decay) * flux
        self.rms_avg = decay * self.rms_avg + (1.0 - decay) * rms
        bandx = band_mean / max(self.band_avg, 1e-9)
        fluxx = flux / max(self.flux_avg, 1e-9)
        rmsx = rms / max(self.rms_avg, 1e-9)
        novelty = max(0.30 * bandx + 1.15 * fluxx + 0.08 * rmsx, 0.18 * bandx + 1.22 * fluxx, 0.50 * bandx + 0.88 * fluxx + 0.08 * rmsx)
        onset = max(0.0, novelty - 1.06)
        if rms < self.cfg.silence_rms:
            onset *= 0.10
        self.env = 0.66 * self.env + 0.34 * onset
        self.hist.append((t_center, self.env, rms))
        if len(self.hist) < 5:
            return None
        mid_i = len(self.hist) - 3
        t, env, prms = self.hist[mid_i]
        _, left, _ = self.hist[mid_i - 1]
        _, right, _ = self.hist[mid_i + 1]
        if not (env > left and env >= right):
            return None
        envs = np.asarray([x[1] for x in self.hist], dtype=np.float32)
        thr = max(self.cfg.peak_abs_floor, float(np.mean(envs) + self.cfg.peak_std_factor * np.std(envs)))
        if env < thr or env < self.cfg.peak_rel_factor * self.prev_env:
            self.prev_env = env
            return None
        self.prev_env = env
        if not armed or prms < self.cfg.silence_rms or t - self.last_peak_t < self.cfg.min_peak_gap_s:
            return None
        self.last_peak_t = t
        return Peak(t=t, strength=min(1.0, env / max(thr, 1e-6)), rms=prms)

    def _observe(self, peak: Peak, now: float) -> bool:
        if self.locked and self.period > 0.0:
            pred = self.anchor + round((peak.t - self.anchor) / self.period) * self.period
            err = wrap_err(peak.t - pred, self.period)
            if abs(err) > self.cfg.accept_phase_s:
                self.rejects += 1
                return False
            self.errors_ms.append(err * 1000.0)
        self.peaks.append(peak)
        cand = self._estimate_candidate()
        self.candidate = cand
        if cand is None:
            return True
        self.support = cand.support
        if not self.locked:
            if len(self.peaks) >= self.cfg.lock_min_peaks and cand.support >= self.cfg.lock_min_support and cand.conf >= self.cfg.lock_min_conf:
                self._lock(peak.t, cand, now)
            return True
        pred = self.anchor + round((peak.t - self.anchor) / self.period) * self.period
        phase_err = wrap_err(peak.t - pred, self.period)
        self.anchor += clamp(phase_err, -self.cfg.accept_phase_s, self.cfg.accept_phase_s) * self.cfg.phase_gain
        self.period = clamp(self.period + (cand.period - self.period) * self.cfg.period_gain, self.cfg.min_period, self.cfg.max_period)
        self.last_good_period = self.period
        self.conf = clamp(max(self.conf * 0.992, cand.conf), 0.0, 1.0)
        self.last_support_t = now
        self._schedule_next(now)
        return True

    def _lock(self, beat_t: float, cand: Candidate, now: float):
        recent = np.asarray([p.t for p in self.peaks][-12:], dtype=np.float64)
        last = float(recent[-1])
        phases = np.mod(recent - last, cand.period)
        angles = phases * (2.0 * math.pi / cand.period)
        mean_angle = math.atan2(float(np.mean(np.sin(angles))), float(np.mean(np.cos(angles))))
        if mean_angle < 0.0:
            mean_angle += 2.0 * math.pi
        self.anchor = beat_t + mean_angle * cand.period / (2.0 * math.pi)
        self.period = cand.period
        self.last_good_period = cand.period
        self.conf = cand.conf
        self.locked = True
        self.last_support_t = now
        self._schedule_next(now)

    def _estimate_candidate(self) -> Optional[Candidate]:
        if len(self.peaks) < 3:
            return None
        times = np.asarray([p.t for p in self.peaks], dtype=np.float64)
        intervals = np.diff(times)
        intervals = intervals[(intervals >= 0.10) & (intervals <= 1.20)]
        if len(intervals) < 2:
            return None
        core = intervals[-14:]
        seeds: list[float] = []
        for interval in core:
            for mult in (1.0, 2.0, 3.0, 4.0):
                p = float(interval * mult)
                if self.cfg.min_period <= p <= self.cfg.max_period:
                    seeds.append(p)
        if not seeds:
            return None
        seeds.extend(np.linspace(self.cfg.min_period, self.cfg.max_period, 25).tolist())
        candidates = []
        for period in seeds:
            errs = []
            weights = []
            for t in times[-16:]:
                pred = times[-1] + round((t - times[-1]) / period) * period
                e = abs(wrap_err(t - pred, period))
                errs.append(e)
                weights.append(max(0.0, 1.0 - e / max(0.001, 0.18 * period)))
            errs_a = np.asarray(errs, dtype=np.float64)
            weights_a = np.asarray(weights, dtype=np.float64)
            support = int(np.sum(errs_a <= min(0.075, 0.16 * period)))
            if support < 2:
                continue
            spread = float(np.percentile(errs_a, 75) / period)
            bpm = 60.0 / period
            tempo_bias = max(0.0, 1.0 - abs(bpm - 115.0) / 12.0) * 0.45
            score = float(np.sum(weights_a)) + 0.65 * support + tempo_bias - 2.5 * spread
            conf = clamp(0.18 + 0.08 * support + 0.11 * score, 0.0, 1.0)
            candidates.append((score, support, -spread, period, conf))
        if not candidates:
            return None
        score, support, neg_spread, period, conf = max(candidates)
        return Candidate(period=period, bpm=60.0 / period, support=support, spread=-neg_spread, score=score, conf=conf)

    def _decay(self, now: float, rms: float):
        if not self.locked:
            return
        if rms < self.cfg.silence_rms or now - self.last_support_t > self.cfg.stale_s:
            self.locked = False
            self.period = 0.0
            self.anchor = 0.0
            self.conf = 0.0
            self.next_fire_t = 0.0
            return
        self.conf *= 0.997

    def _schedule_next(self, now: float):
        if not self.locked or self.period <= 0.0:
            self.next_fire_t = 0.0
            return
        k = math.floor((now - self.anchor) / self.period) + 1
        self.next_fire_t = self.anchor + k * self.period - self.cfg.flash_lead_s
        while self.next_fire_t <= now:
            self.next_fire_t += self.period

    def _due_fire(self, now: float) -> Optional[float]:
        if not self.locked or self.period <= 0.0 or self.conf < 0.20:
            return None
        if self.next_fire_t <= 0.0:
            self._schedule_next(now)
        if self.next_fire_t <= now + self.cfg.flash_lookahead_s:
            t = self.next_fire_t
            self.next_fire_t += self.period
            return t
        return None


def create_tracker(config: dict | None = None) -> FreshBeatTracker:
    config = config or {}
    cfg = BeatTrackerConfig(
        sample_rate=int(config.get("sample_rate", 22050)),
        hop_s=float(config.get("hop_s", config.get("hop_seconds", 0.02))),
        bpm_min=float(config.get("bpm_min", 110.0)),
        bpm_max=float(config.get("bpm_max", 118.0)),
        silence_rms=float(config.get("silence_rms", 0.0082)),
        flash_lead_s=float(config.get("flash_lead_s", 0.0)),
        accept_phase_s=float(config.get("accept_phase_s", 0.035)),
    )
    return FreshBeatTracker(cfg)
