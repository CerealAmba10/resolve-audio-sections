# Validation: native menu integration

Verified on 15 October 2026 in installed DaVinci Resolve Free 21.1.1.10.

## Completed end-to-end test

The command was clicked under Workspace → Scripts with duck test.mp4 already in
the open project. It scanned the source, selected number 2 because pair 1 existed,
created duck test 2T and duck test 2 timelines/jobs, saved the project and rendered
only those two jobs. Existing pair 1 jobs remained untouched, including its
unrendered full job. Resolve showed both new jobs completed and High quality.
Fusion's completion response reported 2 clips saved, 100%.

The final packaged worker was installed and its SHA256 matched the distribution.
A second native-menu run created and completed pair 3T/3, with the same decoded
fade measurements below. Rerunning the installer then gracefully stopped and
updated the idle helper. The Startup shortcut targets the Documents installation.
All 27 automated tests pass. The final ZIP passed its CRC integrity check and
contains the current Lua bridge, worker, installer, preset and quick-start guide.

FFprobe checked exported MP4 files in Downloads/Audio Sections Exports:

| Output | Video duration | Video frames | Codecs | Size / rate |
| --- | --- | --- | --- | --- |
| duck test 2T.mp4 | 30.000000 s | 720 | H.264 + AAC | 1280×720, 24 fps |
| duck test 2.mp4 | 130.083333 s | 3122 | H.264 + AAC | 1280×720, 24 fps |

AAC stream durations differ slightly (30.016 s / 130.090667 s) due to AAC frame
padding. Video duration and frame counts match the requested ranges.

integration/verify_render.py decoded the teaser and the corresponding part of
its full clip. Measurements are teaser/full ratios:

| Position | Picture brightness | Audio RMS |
| --- | --- | --- |
| 28.8 s | 1.000 | 1.000 |
| 29.0 s | 1.000 | 0.966 |
| 29.5 s | 0.449 | 0.477 |
| 29.9 s | 0.008 | 0.066 |

This confirms picture and audio fade during the final second while earlier
content remains unchanged. Checks use decoded output, not only fade API values.

## Compatibility findings and regression coverage

- Lua io/process launch functions are absent; use sandbox-supported file loading
  and Fusion preferences. Native Scripts menu execution DOES work. Earlier
  documentation claiming it was ignored was incorrect and has been replaced.
- Empty Fusion GetPrefs can return zero Lua values; force one value before tonumber.
- Native H.264 rejects named VideoQuality values; retain High via the saved preset.
- AppendToTimeline endFrame is exclusive in this build; manifest endpoints are inclusive.
- Locally launched helper files in AppData were not visible to Resolve in this
  environment. Documents → Audio Sections Plugin is the tested shared location.
- Existing source media is reused; generated timelines are grouped in their own bin.
- Tests cover boundaries, multi-hour frame offsets, strict >30 seconds, real scans,
  stereo antiphase, multiple streams, naming/collisions, rollback, native fades,
  no-io Lua, mailbox escaping, empty prefs and rendering only new IDs.

## Limits

Live test uses a 180-second 24 fps source, not a multi-hour render. Multi-hour frame
math is automated; scanning streams audio. Variable-rate detection uses metadata
and is not exhaustive. Different machines, other Resolve versions, multiple-stream
mixing, international Windows paths and network drives need their own checks.
