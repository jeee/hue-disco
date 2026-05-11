# BTrack install and current tuning notes

This note captures the currently working BTrack path on the Raspberry Pi real-bridge install.

## Install sequence that worked

```bash
sudo apt update
sudo apt install -y cmake ninja-build pybind11-dev build-essential pkg-config libsamplerate0-dev
sudo /opt/hue-disco/.venv/bin/pip install -U pip setuptools wheel
sudo /opt/hue-disco/.venv/bin/pip install scikit-build-core pybind11 numpy
sudo /opt/hue-disco/.venv/bin/pip install --no-build-isolation btrack-beat-tracker
cd /tmp/BTrack/plugins/python-module
sudo sed -i '1i #include <algorithm>' BTrackPythonModule.cpp
sudo /opt/hue-disco/.venv/bin/pip install --no-build-isolation /tmp/BTrack/plugins/python-module
```

## Runtime shape now

- detector backend: `btrack`
- admin selector exposes `BTrack`
- BTrack path uses a downsampled ~22.05 kHz signal derived from the main 44.1 kHz input stream
- BTrack rolling buffer: 2.0 s
- BTrack evaluation interval: 0.50 s
- beat timestamps are scaled back after downsampling so BPM/phase stay in the correct time base

## Why the downsample is BTrack-only

The overall app still runs the normal audio loop cadence from the main input stream.
Only the BTrack analysis branch is downsampled for CPU reduction.
This keeps the main scheduling cadence while reducing the expensive BTrack workload on the Pi.

## Current outcome

- CPU load dropped materially after BTrack throttling/downsampling.
- BTrack now tracks music and clears stale lock state after music stops.
- Silence no longer advances beat count in the validated tests.
- Visual sync is improved versus the earlier aubio path but still needs phase/render tuning from real footage.
