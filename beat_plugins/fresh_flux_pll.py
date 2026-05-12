PLUGIN_NAME = 'fresh_flux_pll'
DISPLAY_NAME = 'Fresh spectral-flux PLL'

from beat_tracker_core import BeatTrackerConfig, FreshBeatTracker


def create_tracker(config):
    cfg = BeatTrackerConfig(
        sample_rate=int(config.get('sample_rate', 22050)),
        hop_s=float(config.get('hop_s', config.get('hop_seconds', 0.02))),
        bpm_min=float(config.get('bpm_min', 110.0)),
        bpm_max=float(config.get('bpm_max', 118.0)),
        silence_rms=float(config.get('silence_rms', 0.0082)),
        flash_lead_s=float(config.get('flash_lead_s', 0.0)),
        accept_phase_s=float(config.get('accept_phase_s', 0.035)),
    )
    return FreshBeatTracker(cfg)
