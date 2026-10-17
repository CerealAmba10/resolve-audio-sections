# Audio Sections for Resolve: current specification

Offline Windows integration for Resolve Free 21.1.1. A one-time installer adds
Workspace → Scripts → Audio Sections and a quiet local helper that starts with Windows.

Input is the current timeline, with any number of audio and video tracks. If none is open,
make one from a single selected Media Pool recording. The helper rebuilds each audio track
over timeline time from its items (file, source in-point from GetSourceStartFrame / clip FPS,
since GetSourceStartTime includes the file's start timecode, record position,
GetSourceAudioChannelMapping routing; disabled items and muted mappings are silent) and
measures RMS dBFS every 20 ms with FFmpeg across the routed channels together.

Default mode: sections are everything between dead spans (every enabled track below -80 dBFS
for at least min_dead), split by max_length at the quietest point, kept if longer than
min_clip (1 s); a clip shorter than the teaser gets a whole-clip teaser. The optional
pause mode adds the speech rules below.

A window lets the user mark tracks Primary or Background and set each primary track's
silence level (auto: max(95th-percentile level - 24 dB, 2nd-percentile level + 6 dB)).
Speech = any primary track above its level. Drop bursts shorter than min_burst; merge runs
separated by less than min_silence; pad edges by padding, capped at the gap midpoint; split
sections longer than max_length (default 180 s) at the quietest 0.5 s, relative to each
primary track's level, within the last cut_window (default 30 s) before max_length, repeating
on the remainder; keep sections strictly longer than max(min_length, teaser). Before splitting, any span
between kept sections longer than min_quiet (default 45 s, blips included) becomes a quiet
section, split by length but never trimmed. Then dead spans (every scanned track below
-80 dBFS for at least min_dead, default 0.5 s) are cut out of all sections, leaving the pieces
either side as separate sections that must still exceed max(min_length, teaser). The preview re-cuts instantly and
supports zoom, frame-snapped manual trims and per-clip skip.

Make cuts: Lua creates "<name> (cuts)" (unique), matches frame rate, resolution and track
formats, and appends each clip's overlap with every enabled source item (AppendToTimeline
with source frames, exclusive endFrame, trackIndex, recordFrame, channel mapping), coloring
clips alternately; on failure the timeline is deleted. The helper records the timeline name
with output, stem, teaser and fade. Running on a recorded cuts timeline skips scanning: each
enabled item on the first non-empty track (video first) is one clip.

For each chosen section, queue one Resolve job on the source timeline with MarkIn/MarkOut
(absolute frames, inclusive) and the bundled Audio Sections High preset at the timeline
resolution. Refuse if the project changed or the timeline was edited since scanning; on a
queue failure delete this batch's jobs. Save the project and render only these jobs. After
rendering, check each full clip's frame count and cut its teaser (first teaser-length
frames, video+audio fade-out over fade seconds, libx264 CRF 16 / AAC 320k).
Names: "name N" and "name NT", N after the highest existing number in the folder or queue.

Architecture: detection.py (pure logic), media.py (FFmpeg), editor.py (Tk window),
worker.py (Fusion-prefs mailbox, progress, teasers), native_bridge.lua (Resolve side; Free
lacks io/process launch, so only loadfile and Fusion prefs are used), setup_plugin.py,
plugin_entry.py, build_lite.ps1 / build_plugin.ps1.

Compact distribution: one executable combines installer and worker using --worker.
FFmpeg/FFprobe are reused or downloaded at setup with a pinned SHA256, never during scans.

Verification: unit tests (detection, Lua bridge against a fake Resolve, real-FFmpeg
multi-stream routing and offset tests), the editor driven on real media, and a live Resolve render.
