# Changelog

## 1.11

- Clip name and export folder are remembered per Resolve project (by its id, so renaming the project keeps
  them) and the project wins over cuts-timeline records and other projects. A project's first run uses
  the project's name and the folder beside the recording.

## 1.10

- Fix Make cuts refusing to build when an audio piece lands a frame or two off ("came out 3769 frames long
  instead of 3767"). Small rounding differences are accepted; only real mismatches are re-placed or stop it.

## 1.9

- Check for updates button in the Audio Sections window, next to the installed version. Updating from
  there closes the window and restarts the helper; anything already in Resolve stays.

## 1.8

- Still images on video tracks no longer collapse to one frame on the cuts timeline: each is turned
  into a video of its item's length (cached; a 3-hour still takes seconds) and placed like footage.
- Undo cuts: deletes the cuts timeline in one click and switches back to your timeline.
- Cuts timeline pieces are measured after placing: media that counts frames at another rate (other frame
  rate, audio-only files) is re-placed at the right length, and a piece that still comes out wrong stops
  Make cuts with a clear message instead of building a timeline with wrong lengths.
- Resolve reports each queued job, and the helper waits up to 5 minutes, so queueing many clips in a big
  project no longer ends in a false "Resolve stopped responding".
- Make cuts saves its request in batches, for checking later.

## 1.7

- Make cuts in Resolve places a blue "Teaser end" marker at the teaser length (30 s) into every clip
  longer than that, so the default teaser end is visible and can be dragged.

## 1.6

- A timeline marker inside a clip on the cuts timeline ends that clip's teaser (no marker: first 30 s).
  The list shows each marked teaser's length and the preview draws it.
- Teasers are cut as soon as each clip finishes rendering, instead of after the whole batch.
- The window says when it is showing the pieces of a cuts timeline rather than automatic cuts.

## 1.5

- Fix "The Audio Sections helper stopped" while the window is open: the heartbeat Resolve checks is
  written from its own thread, so an open error dialog or a busy window no longer looks like a crash.
- Fix missing teasers: an unexpected error in the helper's loop is logged to log.txt instead of
  silently stopping it, so it still sees "render complete" and makes every teaser.
- Error dialogs no longer hold up the helper; Resolve's message also shows in the window's status line.

## 1.4

- The Audio Sections window stays open after Make cuts in Resolve. Load edits from Resolve reads
  your bladed, merged or deleted pieces into the list, and Render renders them from the cuts
  timeline. The stuck "Render your cuts" popup (its button was cut off) is gone.
- Each clip's video and audio pieces on the cuts timeline are linked.
- New setting: fade out the end of each full clip.
- Fix: a render after Make cuts in the same session is no longer missed by the helper.

## 1.3

- Noise-gated recordings no longer shatter into fragments: only dead silence over 3 s ends a
  clip; shorter dead pauses stay inside, and 3-minute cuts trim the dead pause they land in.
- Every setting change re-cuts the preview and clip list immediately (typing, arrows, wheel).
- Pause-only settings are greyed out unless "Also split at pauses" is ticked.
- Saved cutting settings from older versions are reset once; track roles are kept.

## 1.2

- Default: cut only at digital silence and export everything else, however quiet, in clips of
  at most 3 minutes. Speech-pause cutting is now an optional checkbox.
- Short clips are no longer dropped (minimum 1 s); their teaser is the whole clip.
- Make cuts in Resolve: build a "(cuts)" review timeline to blade, merge or delete pieces, then
  run Audio Sections on it to render each piece and its teaser.
- The minimum-length setting is reset to 1 s, replacing the old saved 30 s value.

## 1.1

- Export quiet stretches longer than 45 s as their own clips (with teasers), so only short
  pauses are cut and the track is exported nearly intact.
- Hard-cut dead silence (every track below -80 dBFS for 0.5 s or more) out of every clip.
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
