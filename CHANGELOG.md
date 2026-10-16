# Changelog

## Project-aware update

- Fixed the Free-edition crash caused by the absent Lua io library.
- Fixed Native H.264 quality-setting rejection by retaining the verified High preset.
- Fixed the one-frame-short teaser discovered in live Resolve queue verification.
- Reuse existing project media and group generated timelines in an Audio Sections bin.
- Connect a project once, then analyze and queue with short Console commands.
- Bundle the verified export preset and add project handoff regression tests.

Verified live in Resolve Free 21.1.1: queue creation, source reuse, both teaser
fades, and High quality retention. Free-edition Scripts-menu launch is ignored,
so Console entry points are required. No completed render has been checked.

## Initial local build

- Offline audio-gap scanning with adjustable silence threshold and minimum gap.
- Paired 30-second teaser and full-clip timelines and Resolve render jobs.
- Optional native audio and video teaser fade-out.
- Original-filename numbering, High render quality, collision checks and rollback.
- Portable Windows package and Free-edition internal Lua Console workflow.

Verified with synthetic media, Lua 5.1 simulation and desktop flow tests.
Live Resolve queue/render verification remains outstanding.
