# Audio Sections for Resolve: current specification

Offline Windows integration for Resolve Free 21.1.1. A one-time installer adds
Workspace → Scripts → Audio Sections - Sort and Render and a quiet local helper
that starts with Windows. Daily operation needs no companion controls or Console.

Select one original Media Pool recording, or use the video under the current
timeline playhead. Scan the complete source, splitting at silence below -40 dBFS
lasting at least 1 second in every embedded audio stream. Keep only sections
strictly longer than 30 seconds. Stream audio with FFmpeg; do not upload anything.

For each section create isolated teaser/full timelines in the Audio Sections bin
and Deliver jobs in that order. Teaser: first 30 seconds rounded to whole frames,
last second fades both audio and video. Full: gap-to-gap, no added fades. Names:
originalfilename NT and originalfilename N. Automatically select the next unused
number from existing timelines and exports. Preserve original edits and jobs.

Load the bundled Audio Sections High preset (MP4, H.264, Native, Balanced,
Quality/High, Auto profile, automatic keyframes, frame reorder on, no network
optimization or marker chapters; AAC 48kHz 320kbps). Use reference timeline size
and frame rate, or project settings if no current timeline. Save the project and
render only newly created jobs. Exports go into Audio Sections Exports beside the
source. Retain the generated timelines and batch JSON/CSV/Lua for inspection.

Reject missing/offline media, obvious variable frame rates, differing stream start
times and source FPS overrides. Refuse collisions; roll back only this batch's
objects if queue creation fails. Source file and existing outputs are never edited.
User timeline effects, titles, grades, cuts and mixer changes are not transferred.

Architecture: worker_core.py/audio_sections.py scan; worker.py provides local
mailbox/progress; native_bridge.lua controls Resolve through supported Lua APIs;
queue_template.lua creates isolated jobs; setup_plugin.py installs; build_plugin.ps1
packages. Free lacks io/process launch; use loadfile/dofile and Fusion prefs.

Verification: unit and real-media tests plus a native-menu completed render.
Decoded teaser must fade relative to the matching first 30 seconds of the full
clip. Rebuild the package after changes and install that exact build for smoke QA.
