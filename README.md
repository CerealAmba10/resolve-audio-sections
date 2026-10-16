# Audio Sections for DaVinci Resolve 21.1.1

Offline Windows audio scanner plus an internal Resolve Lua script. For every
audio section longer than 30 seconds, it queues a 30-second teaser and then
the full section. Names are `originalfilename 1T.mp4`, `originalfilename 1.mp4`,
`originalfilename 2T.mp4`, `originalfilename 2.mp4`, and so on.

## Start here

Open `AudioSections.exe` in the portable package. Keep the `_internal` folder
beside it. No Python installation, internet connection or account is required.
Unzip the entire package before starting. Resolve does not have to be open
while scanning. Resolve must be open with a project when queuing.

This is a companion app with a Resolve script, rather than a Studio Workflow
Integration. The free edition does not offer Studio's external scripting
connection. The generated command runs through Resolve's internal **Lua Console**.
This build was checked against your installed 21.1.1 SDK and tested with real
FFmpeg media and a simulated Resolve project. Live queuing/rendering in your
Free installation still needs verification. If Console scripting is unavailable,
this program cannot add jobs to Resolve; it will still generate a clip manifest.

## One-time Deliver preset setup

In Resolve, open a project and timeline, then open **Deliver > Custom Export**.
Set these options, matching the supplied screenshot with Quality changed to High:

| Setting | Value |
| --- | --- |
| Render | Single clip |
| Export Video | Enabled |
| Format | MP4 |
| Codec | H.264 |
| Encoder | Native |
| Network Optimization | Off |
| Resolution | Timeline Resolution |
| Frame rate | Timeline Frame Rate |
| Chapters from Markers | Off |
| Encoding Profile | Auto |
| Key Frames | Automatic |
| Frame reordering | On |
| Preset | Balanced |
| Rate Control | Quality |
| Quality | High |

On the **Audio** tab, enable Export Audio and choose AAC. The screenshot did not
show audio settings; choose the sample rate/bitrate you normally use.

Use the render settings panel's preset menu to save a new preset named
**Audio Sections High**. This preserves encoder controls that Resolve does not
expose through its scripting API. The script explicitly applies High quality,
video/audio export, timeline size/rate and no network optimization to every job.
If you use a different preset name, enter that name in the scanner.

## Each recording

1. Open the app and choose the original video and an export folder.
2. Leave the defaults initially: **-40 dBFS**, **1-second gap**, **1-second fade**,
   start number **1**, audio **all**. Click **Scan file**.
3. Review the section start/end times. Adjust the silence settings and scan again
   if the splits are wrong. Only sections strictly longer than 30 seconds appear.
4. Click **Prepare Resolve queue**. The Resolve command is copied automatically;
   **Copy Resolve command** copies it again if needed.
5. In Resolve, open the destination project and select a timeline with the output
   resolution/frame rate you want. Open **Workspace > Console**, select **Lua**,
   paste the command and press Enter.
6. Review the jobs on Deliver, save the project, then click **Render All** when ready.

The command looks like `dofile("C:/.../AudioSections-.../queue.lua")`. It creates
separate timelines and jobs, in teaser/full order. It does not start rendering.
The teasers fade to black and silence over their last second. Set fade to **0**
to disable both fades. Full clips receive no added fades.

## How sections work

An audio gap is signal below the silence threshold for at least the minimum gap
duration. Clips start where the preceding gap ends and end where the next gap
starts. Shorter pauses remain inside the clip. File start/end are used when there
is no surrounding gap. Long files are scanned as a stream without loading their
audio into memory or decoding the video.

With **all**, every embedded audio stream must be silent at the same time to
split a section. Channels are analyzed without mixing them, so opposite-phase
stereo does not cancel. To scan only one audio stream, enter its zero-based
number (0 is the first). This selection affects detection; imported clip audio
uses Resolve's embedded audio mapping. Check the timeline mixer before rendering
files that contain several separate recordings of the same signal.

- For low-level background noise counted as sound, raise the threshold (e.g. -30).
- For quiet audio being missed, lower it (e.g. -50).
- For pauses that should stay inside clips, increase the minimum gap.
- Teasers are rounded to the nearest whole video frame. At fractional frame rates,
  their duration can differ from exactly 30 seconds by less than one frame.
- Use constant-frame-rate video. Obvious variable-rate metadata is rejected;
  metadata cannot reliably identify every variable-rate recording. Convert screen
  recordings with variable timestamps to constant frame rate first.
- Files with differing audio/video start times are rejected rather than guessing
  their alignment. Normalize those files to a common start time first.

## Files and repeat runs

The app creates a dated batch folder inside the export folder containing:
`manifest.json` (source sections and frame ranges), `clips.csv` (reviewable list),
`queue.lua` (the Resolve script), and, after queuing, `queued.txt` (job IDs).

Existing output files and same-name timelines are refused. To make another batch
from the same source, use a new start number and/or export folder; existing
same-name timelines in the project still need distinct numbers. If queuing fails,
the script attempts to remove only the jobs and timelines it just created, and
reports incomplete cleanup. The imported source may remain in the Media Pool.
Existing edits and queue jobs are preserved. Source files are never modified.

Keep the original media available until rendering is complete. Keep generated
timelines until their render jobs are finished. Source Clip Attributes frame-rate
overrides are refused if they differ from the scan. This tool processes the source
file, not an existing timeline's edits, titles or grades.

For a console file-read error on a path containing non-ASCII characters, move the
batch folder to a simple local path and update the command; adjust the RECEIPT
path in queue.lua to match. Resolve's own media API is used to import the source.

## Development and verification

Python 3.11+, Tk, FFmpeg and FFprobe are needed to run from source. Put the latter
executables in `tools/`. Runtime code uses only the Python standard library.
Tests additionally use `lupa` for a Lua 5.1 adapter simulation; packaging uses
PyInstaller. Run:

```
python -m unittest discover -s tests -v
python app.py
powershell -ExecutionPolicy Bypass -File build.ps1
```

The tests cover boundary detection, paired naming, frame math, antiphase audio,
native fade settings, queue isolation, collisions and rollback. They do not
substitute for a real Resolve render. FFmpeg is restricted to local file and pipe
protocols; the application has no network calls or automatic update checks.

## Sources and bundled tools

Resolve API source: the Blackmagic SDK shipped with Resolve 21.1.1 at
`C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/`.
In particular, `DaVinciResolveScript.pyi` documents `TimelineItem.SetFades`,
`RenderSettings.VideoQuality='High'` and the timeline/render queue methods.

FFmpeg 9.0.2 Windows essentials build is bundled from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/), a Windows build provider linked
by [FFmpeg's download page](https://ffmpeg.org/download.html).
See `FFmpeg-LICENSE.txt` and `FFmpeg-README.txt` in the package. Build information
and corresponding source retrieval instructions are in that README; upstream
[FFmpeg 9.0.2 source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz) is available
from the FFmpeg project. Filter reference:
[silencedetect](https://ffmpeg.org/ffmpeg-filters.html#silencedetect).

