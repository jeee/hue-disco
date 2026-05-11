# Latest Status (v18 snapshot)

Generated: 2026-04-20

## Confirmed fixed / improved
- Real bridge entertainment stream activation uses CLIP v2 entertainment_configuration lookup and start action instead of the diyHue-only group stream endpoint.
- DTLS subprocess leak / stale-client accumulation was mitigated by killing stale `openssl s_client` processes before reconnect.
- systemd template includes `LimitNOFILE=65536` and the web server runs Waitress in poll mode to avoid `select()` file-descriptor range crashes.
- Silence no longer creates a stable BPM lock from a single stray transient.
- Live `/status` telemetry now exposes phase period, anchor age, next scheduled fire, onset-trigger age, and pending trigger intervals.
- Phase-period retuning now prefers updates that are close to the existing lock instead of accepting every plausible new pair.
- Existing lock decay is softer than the earlier hard reset, so BPM/phase does not immediately collapse on one bad interval pair.

## Current detector state
- Detector backend is still the custom heuristic/aubio-assisted path.
- Two-trigger consistency is required before BPM/phase lock is promoted.
- Trigger acceptance requires onset + RMS + flux + bass-band support.
- Calibration remains experimental: the lock can be numerically stable while human-visible sync still feels wrong because of phase offset and trigger-source quality.

## Current known problems
- Microphone / ambient-noise input can still generate musically implausible beat candidates; line-in remains the real validation target.
- Calibration can still look visually broken even when the clock is internally stable because the detected beat source may be wrong.
- Some tracks/sections still cause tempo retuning that is too eager or not eager enough; lock smoothing is improved but not solved.
- The ansible package snapshot is intentionally conservative and does not yet include BeatNet or BTrack integration.

## Investigated detector alternatives
- BTrack was evaluated as a third backend candidate, but installation on the Pi's Python 3.13 / aarch64 environment failed while building `btrack-beat-tracker` due to a broken source-package/CMake path (`../../src` missing during wheel build).
- BeatNet remains the most promising next alternative backend to try.

## Recommended next step
1. Validate against the real line-in signal instead of room/microphone input.
2. Add BeatNet as an experimental third detector backend alongside `native` and `aubio`.
3. Keep calibration mode for sync diagnosis only; do not treat it as production visual behavior yet.
