# Audio Sections for Resolve

## Objective and success criteria
An offline Windows companion for DaVinci Resolve 21.1.1 Free. Scan a local
video's audio without decoding its video, find sections between qualifying
silences, and create isolated timelines and Deliver jobs in this order:
`originalfilename 1T`, `originalfilename 1`, `originalfilename 2T`, etc.
Only sections strictly longer than 30 seconds qualify. Teasers contain the
first 30 seconds, with an optional native audio/video fade over the last second.
Full clips have no added fades. Nothing starts rendering automatically.

Defaults: silence below -40 dBFS lasting at least 1 second, all audio streams
(a gap must be silent in every selected stream), numbering from 1, one-second
teaser fade. All are adjustable except the requested 30-second teaser length.
Boundaries exclude the qualifying silence; shorter pauses remain inside clips.
The beginning/end of the file are boundaries when there is no surrounding gap.

Use a saved Deliver preset named `Audio Sections High` for screenshot settings:
MP4, H.264, Native, no network optimization, timeline resolution and frame rate,
Auto profile, automatic keyframes, frame reordering on, Balanced preset,
Quality rate control, High quality. Enable audio (AAC recommended). The script
explicitly overrides quality to High and export audio/video to on. Unexposed
encoder options come from this preset rather than guessed API keys.

## Design and implementation order
1. Python standard-library scanner with FFmpeg/FFprobe. Streaming detection,
   bounded log memory, cancellation, rational frame math, no media uploads.
2. Generate a standalone Lua 5.1 queue script and reviewable JSON/CSV manifest.
   Invoke from Resolve's internal Lua Console for Free edition compatibility.
3. Desktop Tk interface, portable Windows executable and bundled FFmpeg tools.

Create a new timeline for each output and retain it so later changes cannot
alter an earlier job's fades. Preserve existing timelines and jobs. Check
filename/timeline collisions before changes. Roll back only objects created
by this script if queuing fails. On repeat, refuse duplicates. Source must be
a local, constant-frame-rate video with audio; reject obvious variable rates
or differing stream start times until normalized by the user.

## Commands and structure
`python -m unittest discover -s tests -v`
`python app.py` (desktop interface)
`python -m PyInstaller --noconfirm --windowed --onedir --name AudioSections app.py`

`audio_sections.py`: detection and export; `queue_template.lua`: Resolve adapter;
`app.py`: desktop interface; `tests/`: unit, real-media and Lua adapter checks;
`README.md`: setup and operation. Snake case functions, explicit validation,
subprocess argument arrays (no shell). Example: `sections_from_silence(120, gaps)`.

## Verification and boundaries
Always test silence at start/end, fully silent/audio files, threshold length,
short pauses, multiple tracks, frame rounding, naming and Lua escaping.
Use real FFmpeg synthetic media and a Lua test double for render queue state.
Do not claim a real Resolve render was verified unless it actually was.
Never change user media, delete existing jobs, start renders or use networking
at runtime. Ask before publishing or changing unrelated installed software.

