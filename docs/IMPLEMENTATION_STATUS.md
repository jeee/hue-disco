# Implementation status

This file is the package-level checklist for the current build. It separates what is implemented, what is partial, and what is still intentionally unresolved.

## Original handoff requirements

### Known-good baseline preservation
- ✅ diyHue entertainment streaming preserved in package architecture
- ✅ automatic diyHue runtime patching preserved
- ✅ calibration mode preserved as dedicated timing/debug mode
- ✅ free strobe path preserved

### Intensity model
- ✅ `intensity_mode = fixed | audio | adaptive`
- ✅ `min_pulse_level`, `max_pulse_level`, `audio_intensity_gain`, `audio_gate_threshold`
- ⚠️ needs real-device tuning for musical feel across genres

### Built-in profiles + custom profiles
- ✅ built-in profiles included
- ✅ custom profile editing in Admin UI
- ✅ copy-current-profile workflow
- ⚠️ destructive editing guardrails are basic, not versioned

### Render mode expansion
- ✅ `calibration`, `color_only`, `pulse_only`, `hybrid`
- ✅ hybrid renderer with separate color and pulse controls
- ⚠️ still needs field validation on real bulbs for final smoothness judgment

### Beat-grid behavior
- ✅ `continuous`, `gated`, `adaptive`
- ⚠️ thresholds are implemented but still need real-room tuning

### Detector backends
- ✅ `native` backend preserved
- ✅ `aubio` backend preserved
- ✅ `btrack` backend integrated
- ✅ Admin UI detector selector updated to expose `btrack`
- ✅ BTrack CPU reduced with a downsampled ~22.05 kHz path, 2.0 s rolling buffer, and 0.50 s evaluation interval
- ⚠️ BTrack still feeds the existing phase/grid pipeline; visual sync still needs real-room/video validation

### BPM-locked strobe variant
- ✅ `free` and `beat_locked` sync modes
- ✅ preset-level sync mode
- ⚠️ Zigbee/Hue timing limits still apply

### Multiple strobe presets/buttons
- ✅ multiple named presets in Admin UI
- ✅ one button per preset in Control UI

### Documentation and GUI help
- ✅ README updated
- ✅ practical UI help text added
- ✅ profile light-group feature spec included
- ✅ this implementation checklist included

## Profile light-group feature spec

### Core structure
- ✅ profiles can contain multiple light groups
- ✅ each group targets specific lights
- ✅ exclusive light assignment per profile enforced in editor save path
- ✅ shared timing model remains global

### Group rendering parameters
- ✅ pulse envelope fields
- ✅ intensity fields
- ✅ color motion / activity fields
- ✅ group role field
- ✅ group palette / static color fields

### Group activity
- ✅ `group_activity` implemented

### Inheritance model
- ✅ system defaults -> profile defaults -> group overrides -> per-light hard limits
- ⚠️ inheritance UI is implicit rather than visualized as a diff

### Validation and usability
- ✅ profile/group editor in Admin UI
- ✅ overlap/incomplete assignment warnings in UI
- ⚠️ no dedicated light-identify/preview tool yet

## Still intentionally unresolved
- ❌ beat detection is not guaranteed perfect across all music
- ❌ aubio half-time / double-time ambiguity is not fully solved
- ❌ BTrack visual sync still needs render/phase tuning on real footage even though the backend state is materially healthier
- ❌ entertainment-area membership refresh is still not truly automatic after initial setup; current workaround is bootstrap/redeploy refresh
- ❌ Zigbee/Hue/diyHue transport jitter cannot be solved purely in package code

## Release quality note

This build should be treated as a serious installable integration package, not as a mathematically complete final product. The remaining truth has to come from a fresh Pi install and real bulbs.
