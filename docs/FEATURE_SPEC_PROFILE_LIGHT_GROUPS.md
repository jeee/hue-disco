# Feature spec: profile-based light render groups

## Goal

Allow a profile to contain multiple light groups that behave differently during the same song or beat stream.

This makes it possible to do things like:
- ceiling bulbs = warm slow color motion
- bar lights = stronger pulses
- corner lamps = low intensity ambient fill
- one group = kick-like emphasis
- another group = softer background wash

The key design rule is:
- Timing stays shared globally
- Render behavior becomes group-specific inside the active profile

That means all lights still follow one common transport / beat model, so newly activated lights do not drift into their own rhythm.

## Core concept

A profile may contain one or more light render groups.

Each render group:
- targets one or more configured lights
- has its own rendering parameters
- may have its own palette preference
- may have its own brightness and pulse behavior
- still uses the shared detector / beat timing model

This is not the same as Zigbee groups or Hue entertainment groups. It is a Hue Disco rendering abstraction layered above the physical lights.

## Design update from latest discussion

The earlier profile design assumed a global allowed-color envelope. That is no longer the preferred direction.

Updated rule:
- per-light allowed_colors still wins if configured for that light
- otherwise palette constraints should be defined at the profile or profile-group level
- the active profile becomes the main palette boundary for render behavior

In practice this means a copied profile can carry its own color constraints, instead of depending on a single global color rule.

## Terminology

### Global sync settings
Settings that remain global for the whole system:
- detector backend
- beat mode
- BPM range
- beat band range
- phase lock behavior
- calibration timing behavior
- transport / diyHue streaming state

### Profile
A named visual style that contains:
- overall profile defaults
- one or more light render groups
- optional profile-level palette preferences
- optional defaults inherited by groups

### Light render group
A profile-local assignment of lights plus rendering behavior.

Example:
- profile: lounge
  - group ambient
  - group accent
  - group bar
  - group back wall

## Functional requirements

### 1. A profile may contain multiple light render groups

Each profile must support:
- zero or more groups during editing
- at least one valid group before save/activation
- ordered display of groups in Admin UI
- ability to duplicate a group
- ability to remove a group
- ability to rename a group

Suggested default:
- when a new profile is created, create one default group containing all eligible lights

### 2. Each render group targets specific lights

A render group must allow selection of:
- one or more lights by light ID
- helper to assign all unassigned lights during setup
- clone-from-existing-group for fast setup

Rules:
- a light should belong to at most one render group within the same active profile
- overlapping assignments should be prevented or resolved before save

Recommended rule:
- exclusive membership per profile

### 3. Group rendering parameters

Each render group should support its own parameters.

#### Brightness / pulse envelope
- base_brightness
- peak_brightness
- pulse_attack_ms
- pulse_hold_ms
- pulse_decay_ms
- pulse_intensity
- min_pulse_level
- max_pulse_level

#### Intensity behavior
- intensity_mode = fixed | audio | adaptive
- audio_intensity_gain
- audio_gate_threshold

#### Motion / visual activity
- color_motion
- color_step_scale
- change_every_beats
- beat_subdivision
- accent_every_beats
- accent_multiplier
- group_activity

#### Render mode preference
- render_mode = color_only | pulse_only | hybrid
- pulse_mix

#### Group palette preference
- palette_colors list
- palette bias such as warm / cool / mixed
- optional static color anchor
- optional reduced palette mode

### 4. Shared timing model

All render groups in the active profile must use the same shared timing source:
- same beat events
- same phase lock state
- same BPM estimate
- same transport clock

Implementation principle:
- one detector/timing model
- many render interpretations

### 5. Group-specific response, not group-specific beat detection

Render groups should not each run their own beat detector.

They may interpret the same beat differently by varying:
- pulse strength
- accent pattern
- brightness curve
- color movement
- gating sensitivity
- palette behavior

### 6. Group-specific grid behavior

Each render group may optionally override profile default grid behavior:
- continuous
- gated
- adaptive

Recommended behavior:
- profile has a default
- each group may inherit or override

### 7. Palette precedence

Updated precedence:
1. per-light allowed_colors
2. active profile-group palette_colors
3. active profile palette_colors
4. system fallback palette

Rule:
- profile and group palette logic may constrain or bias color choice
- profile logic must not override a light-specific allowed_colors list

### 8. Group defaults and inheritance

Suggested model:
- system defaults
- profile defaults
- group overrides

This keeps profile editing compact and avoids repetitive parameter entry.

### 9. Optional group roles

Suggested optional field:
- role = ambient | main | accent | background | percussive | melodic | custom

This does not need hard behavior initially, but helps future templates and UI organization.

### 10. Activation behavior

When a profile is activated:
- all render groups in that profile become active together
- shared timing model remains unchanged
- group assignments are applied together

Recommended rule:
- require every included light to belong to a group
- provide a default catch-all group during editing

### 11. Admin UI behavior

Admin UI should allow:
- create profile
- select active profile
- create group within profile
- name group
- assign lights to group
- edit group behavior
- duplicate group
- reorder groups
- delete group
- copy built-in profile to custom before destructive editing

### 12. Control UI behavior

Control page does not need full editing.

Recommended behavior:
- choose active profile
- show profile summary
- optionally show per-group badges
- no advanced per-group editing on Control page

## Data model proposal

```json
{
  "name": "lounge",
  "builtin": true,
  "description": "Soft warm hybrid pulses",
  "profile_defaults": {
    "render_mode": "hybrid",
    "intensity_mode": "adaptive",
    "grid_behavior": "adaptive",
    "base_brightness": 110,
    "peak_brightness": 170,
    "pulse_attack_ms": 80,
    "pulse_hold_ms": 120,
    "pulse_decay_ms": 260,
    "pulse_mix": 0.45,
    "pulse_intensity": 0.7,
    "color_motion": 0.35,
    "color_step_scale": 0.5,
    "accent_every_beats": 4,
    "accent_multiplier": 1.08,
    "palette_colors": ["#FFB26B", "#FFD166", "#F26CA7"]
  },
  "light_groups": [
    {
      "id": "ambient",
      "name": "Ambient",
      "role": "ambient",
      "light_ids": ["1", "2"],
      "overrides": {
        "pulse_mix": 0.2,
        "pulse_intensity": 0.45,
        "color_motion": 0.25,
        "palette_colors": ["#FFB26B", "#FFD166"]
      }
    },
    {
      "id": "accent",
      "name": "Accent",
      "role": "accent",
      "light_ids": ["3"],
      "overrides": {
        "peak_brightness": 200,
        "pulse_intensity": 0.9,
        "color_motion": 0.5
      }
    }
  ]
}
```

## Effective render resolution

For a light in a group, effective settings resolve as:
1. system defaults
2. active profile defaults
3. render group overrides
4. per-light allowed_colors
5. real-time audio/beat state
6. final renderer output

## Validation rules

- profile name must be unique
- group name must be unique within a profile
- a group must contain at least one light unless it is a temporary draft
- no light may belong to more than one group in the same profile
- brightness values must stay within safe range
- pulse times must stay within sensible range
- if palette preference conflicts with per-light allowed_colors, fall back cleanly

## Suggested implementation phases

### Phase 1
- profile contains multiple groups
- each group has:
  - name
  - light assignment
  - base/peak brightness
  - pulse attack/hold/decay
  - pulse intensity
  - color motion
  - palette_colors
  - render mode
  - intensity mode
- shared timing only
- exclusive light membership
- Admin UI editing
- Control UI profile selection only

### Phase 2
- group-level grid behavior override
- duplicate group
- reorder groups
- better palette helpers
- copy built-in profile into custom
- validation warnings in UI

### Phase 3
- semantic group roles
- auto-generated starter groups
- profile preview mode
- band-aware behavior
- scene import/export

## Complementary ideas

### 1. Group activity macro
Add a macro control:
- group_activity = 0.0 to 2.0

This scales pulse intensity, accent strength, and color motion together.

### 2. Group templates
User-facing group templates:
- ambient wash
- accent pulse
- background glow
- kick accent
- slow color drift

### 3. Background fill group
A special optional group that never fully drops out:
- minimum ambient brightness
- gentle color drift
- weak or no pulse

### 4. Accent phase offset
Future option:
- shift emphasis by beat position without creating a separate tempo engine

### 5. Band-reactive groups later
Future option:
- let one group respond more to low frequencies and another more to mids/highs
- timing must still remain shared

## Acceptance criteria

This feature is successful when:
- a user can create a profile with at least two groups
- different groups can use different brightness/pulse/color behavior
- all groups remain synchronized to the same beat model
- lights activated mid-stream join the shared timing correctly
- diyHue streaming and current strobe behavior do not regress
