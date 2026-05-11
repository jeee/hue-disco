# diyHue upstream patches included in this package

This package includes separate diyHue patch files under:

- `patches/diyhue/0001-fix-entertainment-stream-action-endpoint.patch`
- `patches/diyhue/0002-avoid-duplicate-entertainment-thread-start.patch`
- `patches/diyhue/0003-harden-entertainment-parser-sync-and-indexing.patch`

## Patch summary

### 0001: stream activation on v1 group action endpoint

Fixes `PUT /api/<user>/groups/<id>/action` so:

```json
{"stream":{"active":true}}
```

properly updates `group.stream`, sets the owner, and starts the entertainment listener.

### 0002: avoid duplicate entertainment thread starts

Prevents repeated `{"stream":{"active":true}}` requests from spawning duplicate
`entertainmentService` threads and multiple `openssl s_server` listeners on UDP 2100.

### 0003: harden parser sync and v2 indexing

Keeps `entertainmentService` alive when sync detection computes an invalid frame size
or when a malformed / unsupported HueStream v2 frame leads to an out-of-range channel index.

## Local Hue Disco changes in this package

These are **not** diyHue upstream patches. They are shipped directly in the Hue Disco role:

- activate diyHue entertainment stream via REST before DTLS connect
- use `app_key` for the REST call instead of `client_key`
- parse Hue-style JSON errors from diyHue
- avoid repeated `active:true` REST calls while the DTLS subprocess is already alive
- use a re-entrant lock around connect/send
- send diyHue-compatible v1 HueStream frames:
  - header version `1.0`
  - payload layout `[type][u16 light_id][r][g][b]`

## Suggested apply order in a diyHue checkout

```bash
git apply 0001-fix-entertainment-stream-action-endpoint.patch
git apply 0002-avoid-duplicate-entertainment-thread-start.patch
git apply 0003-harden-entertainment-parser-sync-and-indexing.patch
```


## Automatic install behavior

The Ansible package now applies equivalent diyHue runtime edits automatically via `apply_diyhue_runtime_patches.py` on every diyHue container start. The standalone patch files are still included separately for upstreaming and review.
