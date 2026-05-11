# Release Notes v18

Date: 2026-04-20

## Included changes
- Packaged snapshot updated to the current live-install code path used during the later beat-lock debugging session.
- Real Hue Bridge entertainment stream activation via CLIP v2 entertainment_configuration lookup and start action.
- DTLS stale-client cleanup before reconnect to avoid orphaned `openssl s_client` processes.
- Waitress uses poll mode; systemd template keeps `LimitNOFILE=65536`.
- Calibration pulse supports intensity-scaled white flash level / hold time.
- Two-trigger BPM lock: a single transient no longer seeds BPM/phase lock.
- Trigger acceptance requires onset + RMS + flux + bass-band support.
- Phase-period retuning prefers pending interval pairs that are close to the current lock.
- Existing lock decay is softer than the earlier hard reset path.
- Live-state diagnostics include phase period, anchor age, next scheduled fire, onset-trigger age, and pending trigger intervals.

## Not included yet
- No BTrack backend integration. Installation on the target Pi failed while building `btrack-beat-tracker` on Python 3.13 / aarch64 because the source build expected a missing `../../src` directory.
- No BeatNet backend integration yet.

## Known issues / open work
- Microphone / ambient-noise input can still produce musically implausible beat candidates.
- Calibration sync can still feel visibly wrong even when lock telemetry looks stable; the remaining issue appears closer to phase offset / wrong trigger source than pure drift.
- Real line-in validation is still the highest-value next test.
- BeatNet is the recommended next serious detector upgrade path.
