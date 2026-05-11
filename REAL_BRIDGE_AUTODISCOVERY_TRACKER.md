# Real bridge auto-discovery tracker

## What was wrong

- `official_bridge` mode required `bridge_ip` to already be present.
- Bootstrap aborted with `Missing bridge_ip` instead of attempting discovery.
- Runtime readiness checks used the same manual-IP assumption.
- The admin page and example config implied manual editing rather than real-bridge discovery.

## Changes added in this adapted package

- Added automatic bridge discovery in `bootstrap_hue_credentials.py`.
- Discovery sources currently tried in order:
  1. Hue discovery service (`https://discovery.meethue.com/`)
  2. Local SSDP/UPnP probe fallback
- Bootstrap now persists the discovered IP into `config.yaml` when it succeeds.
- `app_ctl.py` readiness checks now use the discovery-aware bridge resolver.
- `disco_core.py` runtime bridge resolution now uses the same discovery-aware resolver.
- Admin help text, README, and example vars updated to describe the new behavior.

## Still required from the user on a real bridge

- The Hue bridge must be reachable on the same network.
- The physical link button still needs to be pressed when credentials are first registered.
- The chosen entertainment area still needs to exist, or be created from discovered lights when supported by the bridge/API path.

## Good follow-up tests

1. Fresh config with `backend_mode: official_bridge` and blank `bridge_ip`.
2. Run bootstrap and verify it saves the discovered IP.
3. Confirm credential registration succeeds after pressing the real bridge button.
4. Confirm entertainment area discovery works.
5. Confirm restart still works without manually re-entering `bridge_ip`.
6. Test an offline/no-internet LAN where SSDP fallback matters.


## Follow-up fix: manual IP but no button prompt

Symptom: a user sets `bridge_ip` manually for `official_bridge`, but never gets a registration prompt.

Root cause found:
- background bootstrap only ran for `diyhue`
- engine bootstrap retries only ran for `diyhue`
- bootstrap failures were mostly silent in the web UI
- Hue link-button-required error type `101` was not translated into a useful message

Package changes made:
- enabled background bootstrap for `official_bridge`
- enabled engine bootstrap retries for `official_bridge`
- surfaced bootstrap errors in admin/control pages through `state.last_error`
- translated Hue registration error `101` into an explicit instruction to press the physical bridge button

Still worth validating on hardware:
- save admin settings with real bridge IP and confirm status message appears within about one retry cycle
- press bridge button and confirm `app_key` and `client_key` are written to config
- verify entertainment area discovery still completes after credentials are created


## 2026-04-23 follow-up: Ansible terminal visibility
Symptom: user runs deploy with `official_bridge` and sees nothing in the Ansible terminal.

Root cause:
- Ansible bootstrap and readiness-report tasks still only ran when `hue.backend_mode == 'diyhue'`.
- `official_bridge` retries were only happening later inside the running service.
- That made deployment look silent even when a real-bridge registration attempt was needed.

Fix applied:
- enabled bootstrap task during Ansible deploy for `official_bridge`
- added explicit debug output for bootstrap stdout/stderr/rc
- enabled readiness validation reporting during Ansible deploy for `official_bridge`

Expected result:
- during `ansible-playbook`, terminal output should now show whether bootstrap ran
- link-button-required errors should be visible directly in the deploy output
- runtime retries still remain in place after the service starts


## Update v5
- Fixed real-bridge registration POST to avoid HTTP->HTTPS redirect downgrading POST into GET.
- Prefer HTTPS `/api` and `/api/` before HTTP.
- Disable automatic redirects for registration and replay POST manually when redirected.


## Added in v6
- Added a pre-bootstrap Ansible terminal message for `official_bridge` telling the operator to press the physical bridge button.
- Added bounded pairing retries in `bootstrap_hue_credentials.py` for `official_bridge` after a Hue `type 101` response.
- Pairing window defaults to 45 seconds with a 3 second retry interval and emits progress messages into Ansible-captured stderr.
- On success after retries, bootstrap now emits a positive pairing-complete message.

## Still open
- Light-name matching for `left_front` and `right_front` still needs adaptation to the exact names/resources exposed by the real bridge.


## v7 hotfix
- Fixed startup crash in `disco_core.py` by importing `resolved_bridge_ip` as `bootstrap_resolved_bridge_ip`.
- Symptom fixed: `NameError: name 'bootstrap_resolved_bridge_ip' is not defined` during service startup.


## v8
- Skip bootstrap retries when app_key and client_key already exist.
- Clear stale bootstrap error state on reload when credentials are present.
- Avoid overwriting UI status with bootstrap errors after credentials have already been saved.

- v9: treat configured light names like logical aliases when ids are empty; auto-bind discovered bridge light ids by exact match first, then by enabled-light order when counts match. Clarified example config so default names do not imply manual renaming is required.
