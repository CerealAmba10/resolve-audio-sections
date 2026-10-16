# Validation of the initial local build

- Installed Resolve executable reports 21.1.1.10.
- API calls checked against its locally installed Blackmagic scripting SDK,
  last updated 31 August 2026, including native audio/video `SetFades` and the
  named `High` video-quality value.
- 23 tests pass: section/frame math, real FFmpeg scans, antiphase stereo,
  multiple embedded audio streams, Lua 5.1 queue simulation, rollback,
  collisions, and desktop scan/prepare/clipboard flow.
- Portable Windows build completed with PyInstaller 6.22.3 / Python 3.11.9.
- Packaged executable started successfully in a smoke test.
- FFmpeg 9.0.2 and FFprobe are bundled. Runtime network protocols are disabled.

Live Resolve Free 21.1.1 testing identified and fixed absent Lua io, rejected
VideoQuality strings and exclusive source append endpoints. Queue creation,
native fade settings, High preset retention, existing-source reuse, project bin
organization and OTIO handoff were verified in the user's current project.

Not verified: a completed render or the visual/audio result of native fades.
Review a small render before queuing a full recording. Constant-frame-rate media is required;
variable-rate detection based on metadata is not exhaustive.
