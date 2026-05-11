# Release Notes v17

Date: 2026-04-20

## Included changes
- Real Hue Bridge entertainment stream activation via CLIP v2 entertainment_configuration lookup and start action.
- DTLS stale client cleanup before reconnecting to avoid orphaned openssl s_client processes.
- Waitress switched to poll mode to avoid `filedescriptor out of range in select()` after raising `LimitNOFILE`.
- systemd service template now sets `LimitNOFILE` to 65536 by default.
- Calibration pulse now supports intensity-scaled white flash level and hold duration.
- Two-trigger BPM lock: a single transient no longer seeds BPM/phase lock.
- Added bass-supported live trigger gating (`rms`, `flux`, `band_energy`) before trigger acceptance.
- Phase-locked calibration scheduler now uses explicit next-fire scheduling with grace gating.
- Calibration flashes now require recent real trigger evidence and minimum lock quality.
- Added live-state diagnostics for phase period, phase anchor age, next scheduled fire, onset trigger age, and pending trigger intervals.

## Known issues / open work
- Microphone / ambient-noise input can still produce musically implausible beat candidates. Real line-in validation remains the priority.
- Phase lock is more stable than before, but visible sync can still feel wrong due to phase offset and acceptance of far-period updates.
- BeatNet/BTrack integration is recommended as the next serious detector upgrade path instead of continuing to overfit heuristic thresholds.
