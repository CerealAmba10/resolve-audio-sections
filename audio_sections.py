"""Offline audio section detection and Resolve queue preparation."""
import csv
import json
import math
import re
import subprocess
import sys
import threading
from collections import deque
from fractions import Fraction
from pathlib import Path
from datetime import datetime


def merge_intervals(intervals, duration):
    merged = []
    for start, end in sorted(intervals):
        start, end = max(0, start), min(duration, end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def sections_from_silence(duration, silences):
    sections, cursor = [], 0.0
    for start, end in merge_intervals(silences, duration):
        if start - cursor > 30 + 1e-6:
            sections.append((cursor, start))
        cursor = end
    if duration - cursor > 30 + 1e-6:
        sections.append((cursor, duration))
    return sections


def intersect_silences(tracks):
    if not tracks:
        return []
    result = tracks[0]
    for track in tracks[1:]:
        intersection, i, j = [], 0, 0
        while i < len(result) and j < len(track):
            start, end = max(result[i][0], track[j][0]), min(result[i][1], track[j][1])
            if end > start:
                intersection.append((start, end))
            if result[i][1] < track[j][1]:
                i += 1
            else:
                j += 1
        result = intersection
    return result


def frame_jobs(filename, sections, fps, first_number=1, fade_seconds=1):
    if first_number < 1 or not 0 <= fade_seconds <= 30:
        raise ValueError('Numbering must start at 1 or above; fade must be 0–30 seconds.')
    jobs, base = [], Path(filename).stem
    for number, (start, end) in enumerate(sections, first_number):
        first = math.floor(Fraction(str(start)) * fps + Fraction(1, 2))
        stop = math.floor(Fraction(str(end)) * fps + Fraction(1, 2))
        teaser_count = math.floor(30 * fps + Fraction(1, 2))
        if stop - first < teaser_count:
            raise ValueError('Section is shorter than the teaser after frame rounding.')
        for teaser in (True, False):
            jobs.append(dict(name=f'{base} {number}' + ('T' if teaser else ''),
                             start_frame=first, end_frame=first + teaser_count - 1 if teaser else stop - 1,
                             fade_seconds=fade_seconds if teaser else 0))
    return jobs


def lua_string(value):
    return '"' + ''.join('\\' + ch if ch in ('"', '\\') else
                         f'\\{ord(ch):03d}' if ord(ch) < 32 else ch for ch in str(value)) + '"'


class Cancelled(Exception):
    pass


def resource_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / name


def run_args(args, **kwargs):
    return subprocess.run(args, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)


def probe_media(source):
    result = run_args([str(resource_path('tools/ffprobe.exe')), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams',
                       '-show_format', '-of', 'json', str(source)], capture_output=True,
                      encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise ValueError('Cannot read media: ' + result.stderr[-2000:])
    data = json.loads(result.stdout)
    videos = [s for s in data['streams'] if s['codec_type'] == 'video' and
              not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in data['streams'] if s['codec_type'] == 'audio']
    if not videos or not audios:
        raise ValueError('Choose a video file with at least one audio stream.')
    video = videos[0]
    try:
        fps = Fraction(video.get('avg_frame_rate', '0/1'))
        nominal = Fraction(video.get('r_frame_rate', '0/1'))
    except (ValueError, ZeroDivisionError):
        raise ValueError('Unknown frame rate. Convert to constant frame rate before scanning.') from None
    if fps <= 0 or nominal <= 0 or abs(float(fps / nominal) - 1) > 0.001:
        raise ValueError('Variable or unknown frame rate. Convert to constant frame rate before scanning.')
    duration = float(video.get('duration', data.get('format', {}).get('duration', 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Media has no valid duration.')
    start = float(video.get('start_time', 0))
    if any(abs(float(a.get('start_time', start)) - start) > 0.05 for a in audios):
        raise ValueError('Audio/video streams have different start times. Normalize the file before scanning.')
    return dict(fps=nominal, duration=duration, audios=audios, width=video['width'], height=video['height'])


def detect_track(source, index, duration, threshold, gap, cancel, progress):
    args = [str(resource_path('tools/ffmpeg.exe')), '-hide_banner', '-nostdin', '-nostats',
            '-protocol_whitelist', 'file,pipe',
            '-xerror', '-i', str(source), '-map', f'0:a:{index}', '-vn', '-sn', '-dn',
            '-af', f'asetpts=PTS-STARTPTS,silencedetect=noise={threshold}dB:d={gap}',
            '-progress', 'pipe:2', '-f', 'null', '-']
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # A watcher can interrupt decoding even while FFmpeg has no output to read.
    done = threading.Event()
    def watch():
        while not done.wait(0.1):
            if cancel.is_set():
                try:
                    proc.terminate()
                except OSError:
                    pass
                return
    threading.Thread(target=watch, daemon=True).start()
    silences, pending, tail, last_time = [], None, deque(maxlen=20), 0.0
    try:
        for line in proc.stderr:
            tail.append(line)
            match = re.search(r'silence_(start|end):\s*([\d.eE+-]+)', line)
            if match:
                kind, value = match.group(1), float(match.group(2))
                if kind == 'start':
                    pending = value
                elif pending is not None:
                    silences.append((pending, value))
                    pending = None
            if line.startswith('out_time_us='):
                try:
                    last_time = max(last_time, int(line.split('=')[1]) / 1e6)
                    progress(min(1, last_time / duration))
                except ValueError:
                    pass
        proc.wait()
        if cancel.is_set():
            raise Cancelled()
        if proc.returncode:
            raise ValueError('Audio scan failed:\n' + ''.join(tail))
        if pending is not None:
            silences.append((pending, duration))
        if last_time < duration - 0.1:
            silences.append((last_time, duration))
        return merge_intervals(silences, duration)
    finally:
        done.set()
        proc.stderr.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def scan_media(source, threshold, gap, audio, cancel, progress):
    if not math.isfinite(threshold) or not -100 <= threshold < 0:
        raise ValueError('Silence threshold must be between -100 and below 0 dBFS.')
    if not math.isfinite(gap) or gap <= 0:
        raise ValueError('Minimum silence must be greater than zero.')
    if cancel.is_set():
        raise Cancelled()
    info = probe_media(source)
    indexes = list(range(len(info['audios']))) if audio == 'all' else [int(audio)]
    if any(i < 0 or i >= len(info['audios']) for i in indexes):
        raise ValueError('Audio stream index is not present in this file (first stream is 0).')
    tracks = []
    for position, index in enumerate(indexes):
        tracks.append(detect_track(source, index, info['duration'], threshold, gap, cancel,
                                   lambda value: progress((position + value) / len(indexes))))
    silences = [(a, b) for a, b in intersect_silences(tracks) if b - a >= gap - 1e-6]
    return info, sections_from_silence(info['duration'], silences)


def prepare_queue(source, output, info, sections, first_number, fade_seconds, preset):
    if not sections:
        raise ValueError('No audio sections longer than 30 seconds were found.')
    if not preset.strip():
        raise ValueError('Enter the saved Resolve render preset name.')
    if not math.isfinite(fade_seconds):
        raise ValueError('Fade duration must be finite.')
    source, output = Path(source).resolve(), Path(output).resolve()
    jobs = frame_jobs(source.name, sections, info['fps'], first_number, fade_seconds)
    for job in jobs:
        if (output / (job['name'] + '.mp4')).exists():
            raise ValueError(f"Output already exists: {job['name']}.mp4. Choose another starting number or folder.")
    output.mkdir(parents=True, exist_ok=True)
    folder = output / ('AudioSections-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir()
    manifest = dict(source=str(source), output=str(output), source_size=source.stat().st_size,
                    fps=str(info['fps']), preset=preset.strip(), sections=sections, jobs=jobs)
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    with (folder / 'clips.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['name', 'source_start_seconds', 'source_end_seconds', 'fade_seconds'])
        for job in jobs:
            writer.writerow([job['name'], job['start_frame'] / float(info['fps']),
                             (job['end_frame'] + 1) / float(info['fps']), job['fade_seconds']])
    header = '\n'.join(['local SOURCE = ' + lua_string(source.as_posix()),
                        'local OUTPUT = ' + lua_string(output.as_posix()),
                        'local PRESET = ' + lua_string(preset.strip()),
                        'local RECEIPT = ' + lua_string((folder / 'queued.txt').as_posix()),
                        f"local SOURCE_FPS = {float(info['fps']):.12g}", 'local JOBS = {'] +
                       [f'  {{name={lua_string(j["name"])}, first={j["start_frame"]}, last={j["end_frame"]}, fade={j["fade_seconds"]}}},'
                        for j in jobs] + ['}', ''])
    (folder / 'queue.lua').write_text(header + resource_path('queue_template.lua').read_text(encoding='utf-8'), encoding='utf-8')
    return folder
