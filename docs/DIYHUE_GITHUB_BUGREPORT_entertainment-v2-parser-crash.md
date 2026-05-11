# Bug report: entertainmentService crashes on malformed / unsupported HueStream v2 frames

## Summary

When diyHue entertainment mode receives a HueStream frame that selects the `apiVersion == 2`
parser path but carries an out-of-range channel index, `entertainmentService` crashes with:

```text
IndexError: index out of range
  File "/opt/hue-emulator/services/entertainment.py", line 182, in entertainmentService
    light = lights_v2[data[i]]["light"]
```

In the same code path, sync detection can also compute an invalid `frameBites` value and then crash with:

```text
ValueError: read length must be non-negative or -1
  File "/opt/hue-emulator/services/entertainment.py", line 125, in entertainmentService
    p.stdout.read(frameBites - 9)
```

## Observed behavior

- `PUT /api/<user>/groups/<id>/action {"stream":{"active":true}}` returns success.
- diyHue starts `entertainmentService`.
- If the incoming frame is malformed, truncated, or not compatible with the selected parser branch,
  the thread crashes and the DTLS listener disappears.
- Repeated clients can then restart the service again, leading to a noisy loop of start/crash/start.

## Expected behavior

- diyHue should not crash the entertainment thread on malformed or unsupported frames.
- It should log a warning and either drop the bad frame or resynchronize cleanly.

## Reproduction notes

This was reproduced during integration testing against a custom Hue Disco sender on Raspberry Pi.
The failing path was triggered when diyHue entered the v2 parser branch and then indexed:

```python
light = lights_v2[data[i]]["light"]
```

with `data[i]` outside the valid range.

A separate sync-path failure was also observed when `frameBites - 9` became negative.

## Suggested fix

Two small hardening changes are sufficient:

1. Guard sync resynchronization when `frameBites <= 9`
2. Guard the v2 parser against out-of-range channel indexes before reading `lights_v2[data[i]]`

See included patch:

- `patches/diyhue/0003-harden-entertainment-parser-sync-and-indexing.patch`

## Additional context

A separate upstream fix was also needed to avoid starting duplicate entertainment threads on repeated:

```json
{"stream":{"active":true}}
```

See:

- `patches/diyhue/0002-avoid-duplicate-entertainment-thread-start.patch`
