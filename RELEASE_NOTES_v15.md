# v15 package release notes

This package builds on v14 and adds the live fixes made during MG21 entertainment troubleshooting.

## Added in v15

- Native MG21 group stream activation now creates a local diyHue `EntertainmentConfiguration` object when the group exists only in native backend state.
- The native MG21 stream path now updates diyHue `group.stream` state and starts `entertainmentService` on the inactive -> active transition, which opens UDP 2100 for DTLS clients.
- The constructor call for synthesized entertainment groups is corrected to `EntertainmentConfiguration.EntertainmentConfiguration(...)`.
- `dtls_hue_stream.py` now activates the diyHue stream before launching the OpenSSL DTLS client, reducing startup race conditions.
- Temporary debug logging was added to `disco_core.py` and `dtls_hue_stream.py` under `/tmp/hue-disco-*.log` for continued strobe/DTLS troubleshooting.

## Current live status at handoff

- diyHue now returns success for `PUT /api/<user>/groups/1/action` with `{"stream":{"active":true}}`.
- diyHue now opens UDP 2100 after stream activation.
- MG21 native state shows the entertainment group and `stream.active: true`.
- End-to-end lamp updates from Hue Disco are still not fully proven; MG21 `/state` metrics still showed zero entertainment frames in the last captured checks.
- Hue Disco debug files written to `/tmp` may not be visible from the host while `PrivateTmp=true` remains enabled in the systemd unit.

## Recommended next validation

1. Trigger Hue Disco strobe or disco mode.
2. Inspect logs from inside the service namespace or temporarily disable `PrivateTmp` for easier host-side debugging.
3. Confirm `/tmp/hue-disco-strobe.log` and `/tmp/hue-disco-dtls.log` are actually written by the running service.
4. Re-check `http://127.0.0.1:9123/state` for non-zero `entertainment_frames_received` and command counters.
5. Only after frame flow is confirmed, debug lamp output/rendering differences.
