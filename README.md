# Audio Sections for DaVinci Resolve

One-click offline sorting and rendering for **Windows, Resolve Free 21.1.1**.

## Install once

1. Unzip **AudioSectionsPlugin-Windows-Offline.zip** completely.
2. Open **Install Audio Sections.exe** and click **Install in DaVinci Resolve**.
3. If the command does not appear immediately, restart Resolve.

No Python installation, account or internet connection is needed. A quiet local
helper starts when you sign in to Windows. Files install in **Documents → Audio
Sections Plugin**, with a Resolve user Scripts command and a Windows Startup
shortcut named **Audio Sections**.

## Sort and render a recording

1. Open your project and select the **original recording in the Media Pool**.
   Alternatively, open its timeline with the playhead over the recording.
2. Choose **Workspace → Scripts → Audio Sections - Sort and Render**.
3. Leave Resolve open while it scans and renders. Finished MP4s appear in
   **Audio Sections Exports**, beside the original recording.

The command scans the entire original recording, creates a teaser/full pair for
each audio section longer than 30 seconds, queues the pair, saves your project,
and starts rendering **only the new jobs**. Generated timelines appear in the
**Audio Sections** Media Pool bin. Existing edits and queue jobs remain.

| Output | Name | Added fade |
| --- | --- | --- |
| First 30 seconds | `originalfilename 1T.mp4` | Last second fades to black and silence |
| Complete audio section | `originalfilename 1.mp4` | None |

Further pairs use 2T/2, 3T/3, etc. Repeating the command scans again and chooses
the next unused number from project timelines and existing exports. It creates
another batch; it does not overwrite an earlier one.

## Export settings

The bundled **Audio Sections High** preset imports automatically when missing.
It matches the supplied video settings with Quality changed to **High**:

| Setting | Value |
| --- | --- |
| Render | Single clip |
| Format / codec / encoder | MP4 / H.264 / Native |
| Resolution and frame rate | Current timeline settings; project settings when no timeline is open |
| Network Optimization / Chapters from Markers | Off |
| Encoding Profile / Key Frames | Auto / Automatic |
| Frame reordering | On |
| Preset / Rate Control / Quality | Balanced / Quality / High |
| Audio | AAC, 48 kHz, 320 kbps |

The screenshot did not include audio settings; AAC settings above were selected
for the bundled preset. Native H.264 rejects the documented named VideoQuality
argument, so High is retained through the verified saved preset. Keep that preset
unchanged for consistent exports.

## Detection and project behavior

The simple menu workflow uses **-40 dBFS**, a **1-second minimum silence**, all
embedded audio streams, and a **1-second teaser fade**. A gap must be silent in
every audio stream. Stereo channels are not mixed during detection. Clips begin
where the preceding silence ends and end where the next silence starts; shorter
pauses stay in the clip. File start/end are boundaries where needed.

FFmpeg streams the recording without loading hours of audio into memory or
decoding video during detection. Nothing is uploaded. Teasers use whole frames;
fractional frame rates can differ from 30 seconds by less than one frame.

This processes **source media**, including portions trimmed out of the current
timeline. Timeline cuts, titles, grades, effects and mixer adjustments are not
copied. Keep source media online until rendering ends. Audio uses Resolve's media
mapping; multiple recordings of the same signal may need Clip Attributes/audio
mapping adjusted before running.

Use constant-frame-rate video with matching audio/video start times. Obvious
variable-rate metadata and differing start times are rejected. Metadata cannot
identify every variable-rate recording; normalize such recordings first.

## Help

- **No menu command:** restart Resolve after installation.
- **Helper stopped:** run the installer again, then retry the command.
- **No qualifying sections:** no audio section exceeds 30 seconds under the
  fixed silence settings.
- **Stop:** use the Audio Sections progress window or Resolve's render Stop
  button. Completed exports and generated timelines remain available.
- **Disable automatic startup:** disable Audio Sections in Task Manager → Startup
  apps. To remove the command, delete only `Audio Sections - Sort and Render.lua`
  from your Resolve user Scripts/Utility folder. Helper files are in
  Documents → Audio Sections Plugin.

Earlier companion/Console packages remain separate as advanced fallbacks. Use
this plugin package for the simple menu workflow.

## Verification and development

Verified from Resolve's native menu in **Free 21.1.1.10**: source reuse, automatic
numbering, isolated jobs, High quality, project save, and completed teaser/full
renders. The teaser has exactly 720 frames at 24 fps. Decoded output confirms
both picture and audio fade in the final second. See VALIDATION.md for details.

Runtime code uses Python's standard library. Python, Tk, FFmpeg and FFprobe are
bundled. Development tests additionally require lupa; packaging uses PyInstaller.

```
python -m unittest discover -s tests -v
powershell -File build_plugin.ps1
```

The installed Blackmagic SDK is the API reference:
`C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/`.
The menu uses file loading and Fusion preferences to reach its local helper,
following the transport documented by the
[AutoSubs integration](https://github.com/tmoroney/auto-subs/blob/main/Resolve-Integration/README.md).
It does not modify AutoSubs or Resolve security settings.

FFmpeg 9.0.2 Windows essentials comes from
[Gyan](https://www.gyan.dev/ffmpeg/builds/), a provider linked by
[FFmpeg](https://ffmpeg.org/download.html). See FFmpeg-LICENSE.txt and
FFmpeg-README.txt in the package for build/source instructions. Corresponding
[FFmpeg source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz) and the
[silencedetect reference](https://ffmpeg.org/ffmpeg-filters.html#silencedetect)
are available upstream. Runtime protocols are limited to local files and pipes.
