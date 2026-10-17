# Changelog

## 1.1

- Export quiet stretches longer than 45 s as their own clips (with teasers), so only short
  pauses are cut and the track is exported nearly intact.
- Log each run (render request, Resolve finished, teasers made, errors) to log.txt.

## 1.0: GitHub releases and updates

- Publish builds as GitHub releases (release.ps1). The helper checks for newer releases and
  offers a one-click Update, verified against GitHub's published SHA-256 before installing.

## Multi-track rewrite

- Cut the open timeline instead of one source file, with any number of video and audio tracks.
- New Audio Sections window: Primary/Background track roles, per-track silence levels with
  auto-detection, live preview, zoom, frame-snapped trims and per-clip skip.
- Detection uses 20 ms RMS envelopes rebuilt from each track's clips and channel routing, with
  padding, click rejection and adjustable split length, instead of silencedetect on all streams.
- Render full clips directly from the timeline with in/out marks, so every track, effect and
  mix is kept. Cut teasers from the rendered clips with FFmpeg and check frame counts.
- Cap clip length (default 3:00): cut at the quietest point in the last 30 seconds before it,
  continuing the rest as the next clip.
- Remove the generated per-clip timelines, the old companion app and the Console workflow.

## Compact distribution

- Combine installer and helper into one executable, sharing the Python/Tk runtime.
- Reuse installed FFmpeg/FFprobe or download a verified release during setup.
- Keep media processing offline after installation, with no typed commands.
- Retain the full offline package as an alternative.
- Add dependency discovery, checksum, archive handling and offline-runtime tests.

## Native Sort and Render integration

- Add a one-time installer and a native Workspace/Scripts command.
- Scan the selected project recording and automatically render new teaser/full pairs.
- Add quiet Windows startup helper, progress, cancellation and automatic numbering.
- Use a tested shared Documents folder and handle empty Fusion preference returns.
- Verify completed native-menu renders, exact teaser duration and decoded picture/audio fades.
- Correct the earlier claim that Free-edition Scripts-menu execution was ignored.

## Project-aware update

- Fixed the Free-edition crash caused by the absent Lua io library.
- Fixed Native H.264 quality-setting rejection by retaining the verified High preset.
- Fixed the one-frame-short teaser discovered in live Resolve queue verification.
- Reuse existing project media and group generated timelines in an Audio Sections bin.
- Connect a project once, then analyze and queue with short Console commands.
- Bundle the verified export preset and add project handoff regression tests.

Historical validation for this earlier companion build covered queue creation,
source reuse, fade settings and High quality. The native update above replaces
its Console workflow and adds completed-render verification.

## Initial local build

- Offline audio-gap scanning with adjustable silence threshold and minimum gap.
- Paired 30-second teaser and full-clip timelines and Resolve render jobs.
- Optional native audio and video teaser fade-out.
- Original-filename numbering, High render quality, collision checks and rollback.
- Portable Windows package and Free-edition internal Lua Console workflow.

Verified with synthetic media, Lua 5.1 simulation and desktop flow tests.
Live Resolve queue/render verification remains outstanding.
