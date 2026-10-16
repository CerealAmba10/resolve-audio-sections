# Changelog

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
