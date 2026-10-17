# Audio Sections for DaVinci Resolve

Cut a long multi-track recording into clips and 30-second teasers, splitting at the
silences in the tracks you choose. Offline, for **Windows, Resolve Free 21.1.1**.

## Install once

1. Unzip **AudioSectionsLite-Windows.zip** completely.
2. Open **Audio Sections Setup.exe** and click **Install in DaVinci Resolve**.
3. If the command does not appear immediately, restart Resolve.

No Python installation or account is needed. Setup reuses an existing FFmpeg and
FFprobe pair from PATH/common install folders. If none is available, it downloads
a verified FFmpeg build once; that setup step needs internet. Sorting and rendering
work offline afterward. No commands need to be typed.

The larger **AudioSectionsPlugin-Windows-Offline.zip** remains available for fully
offline installation; its installer is named **Install Audio Sections.exe**.

A quiet local helper starts when you sign in to Windows. Files install in **Documents → Audio
Sections Plugin**, with a Resolve user Scripts command and a Windows Startup
shortcut named **Audio Sections**.

## Cut a recording

1. Open the timeline you want to cut. It can have any number of video and audio
   tracks, such as a facecam over gameplay, a separate mic file, music or Discord.
   With no timeline open, select one recording in the Media Pool and a timeline
   is made from it.
2. Choose **Workspace → Scripts → Audio Sections**. The Audio Sections window opens
   and scans every audio track. A 3-hour track takes about 45 seconds, and tracks scan in parallel.
3. Mark each audio track as **Primary** (speech to cut around) or **Background**
   (music, game sound or room noise to ignore). Choices are remembered by track name.
4. Check the preview. Each track shows its level, with an orange line at its silence
   level. Green blocks are the clips that will render; a dotted line marks where each
   teaser ends. Grey blocks are too short.
5. Click **Render N clips**. Leave Resolve open until the window says it is done.

| Control | What it does |
| --- | --- |
| Silence below (per track) | Level counted as silence. **Auto** sets it from that track's own noise floor and speech level. You can also drag the orange line. |
| Split at silence of at least | Shorter pauses stay inside a clip. Raise it for long recordings so normal pauses do not split a clip. |
| Padding around speech | Extra room before the first word and after the last, never past the middle of a gap. |
| Ignore sounds shorter than | Clicks and pops in a silence do not start a clip. |
| Keep clips longer than | Shorter sections are shown but not rendered. |
| Longest clip / cut at quietest point in last | A section longer than this (default 3:00) is cut at its quietest moment in the final window (default 2:30–3:00). The rest continues as the next clip, cut the same way. 0 turns it off. |
| Teaser length / fade | Teaser duration and the fade-out at its end. |

Mouse wheel zooms the preview; drag to scroll. Drag a green clip edge to trim it,
snapped to whole frames. Double-click a row to skip or include that clip. Changing
a setting re-cuts instantly from the scanned levels and resets manual trims.

Resolve renders each full clip straight from your timeline using in/out marks, so every
video track, audio track, effect, transform, grade and mixer setting is included as you
see and hear it. Each teaser is then cut from its full clip, with the last second fading
to black and silence.

| Output | Name |
| --- | --- |
| Complete section | `name 1.mp4` |
| First 30 seconds, faded | `name 1T.mp4` |

Numbering continues after the highest number already in the export folder or render
queue. Existing files are never overwritten. The default name and folder come from the
main recording ("Audio Sections Exports" beside it); both can be changed in the window.

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

## How detection works

Each audio track is rebuilt over timeline time from its clips: the source file, in-point,
timeline position and Resolve's channel routing for that clip. Every 20 ms FFmpeg measures
the RMS level of the routed channels together, so stereo antiphase cannot cancel out.
Disabled clips and muted channel mappings count as silence. A moment is speech when
**any** primary track is above its own silence level. Background tracks are scanned and
shown but never affect the cuts.

Nothing is uploaded. Source files, existing exports and the timeline are never changed.
If the timeline is edited while the window is open, rendering is refused; run it again.
Each run's timeline description and chosen clips are saved in
Documents → Audio Sections Plugin → batches.

Retimed (speed-changed) audio clips are detected approximately; the window warns about them.

## Help

- **No menu command:** restart Resolve after installation.
- **Helper stopped:** run the installer again, then retry the command.
- **No clips, or clips split too often:** raise "Split at silence of at least", or
  lower a primary track's silence level. Check that the right tracks are Primary.
- **Stop:** use the Audio Sections progress window or Resolve's render Stop
  button. Completed exports and generated timelines remain available.
- **Disable automatic startup:** disable Audio Sections in Task Manager → Startup
  apps. To remove the command, delete only `Audio Sections.lua`
  from your Resolve user Scripts/Utility folder. Helper files are in
  Documents → Audio Sections Plugin.

Earlier companion/Console packages remain separate as advanced fallbacks. Use
this plugin package for the simple menu workflow.

## Verification and development

The multi-track rewrite is tested against a fake Resolve, with real FFmpeg on
multi-stream media, and with its window driven on the duck test recording. FFmpeg
teasers are exactly 720 frames at 24 fps, and their decoded fade matches the
previously verified Resolve fade. It has **not yet run inside Resolve**. VALIDATION.md
records the live checks for the previous single-file version.

Runtime code uses Python's standard library. The compact package combines setup
and helper in one executable, sharing Python and Tk. FFmpeg/FFprobe stay external;
their absolute paths are saved locally. It never downloads during a media scan.
The pinned setup download uses HTTPS and a verified SHA256 before named files are
copied from the archive. Development tests require lupa; packaging uses PyInstaller.

```
python -m unittest discover -s tests -v
powershell -File build_plugin.ps1
powershell -File build_lite.ps1
```

The installed Blackmagic SDK is the API reference:
`C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/`.
The menu uses file loading and Fusion preferences to reach its local helper,
following the transport documented by the
[AutoSubs integration](https://github.com/tmoroney/auto-subs/blob/main/Resolve-Integration/README.md).
It does not modify AutoSubs or Resolve security settings.

The fallback FFmpeg 9.0.2 Windows essentials download comes from
[Gyan](https://www.gyan.dev/ffmpeg/builds/), a provider linked by
[FFmpeg](https://ffmpeg.org/download.html). See FFmpeg-LICENSE.txt and
FFmpeg-README.txt in the full package, or LICENSE/README.txt in the downloaded
tools folder, for build/source instructions. Corresponding
[FFmpeg source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz) and the
[astats reference](https://ffmpeg.org/ffmpeg-filters.html#astats-1)
are available upstream. Runtime protocols are limited to local files and pipes.
