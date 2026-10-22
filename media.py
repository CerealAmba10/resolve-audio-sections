"""FFmpeg/FFprobe calls: per-track loudness envelopes, teaser cutting and output checks."""
import array
import json
import subprocess
import sys
import threading
from pathlib import Path

from detection import FLOOR_DB, HOP
from ffmpeg_tools import tool_path

SAMPLE_RATE = 16000


class Cancelled(Exception):
    pass


def resource_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / name


def tool(name):
    return str(tool_path(name, resource_path('')))


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)


def audio_channels(path):
    """Channel count of each embedded audio stream, in file order."""
    result = run([tool('ffprobe'), '-v', 'error', '-select_streams', 'a', '-show_entries',
                  'stream=channels', '-of', 'json', str(path)], timeout=60)
    if result.returncode:
        raise ValueError(f'Cannot read audio from {Path(path).name}: {result.stderr[-500:]}')
    return [int(s.get('channels', 0)) for s in json.loads(result.stdout).get('streams', [])]


def mapped_channels(mapping, streams):
    """0-based flattened channel indexes Resolve routes into a timeline item, or None for all.

    Resolve's channel_idx values are 1-based over the file's embedded channels, stream by
    stream. Links to separate audio files (indexes past the embedded count) fall back to all.
    """
    try:
        track = next(iter(json.loads(mapping)['track_mapping'].values()))
    except (TypeError, ValueError, KeyError, StopIteration, AttributeError):
        return None
    if track.get('mute'):
        return []
    picked = sorted({int(i) - 1 for i in track.get('channel_idx', []) if int(i) > 0})
    if not picked or picked[-1] >= sum(streams):
        return None
    return picked


def item_filter(streams, channels):
    """Decode only the streams holding the routed channels, stack them, then pick those channels."""
    offsets = [sum(streams[:i]) for i in range(len(streams))]
    used = [i for i, n in enumerate(streams) if n and (channels is None or
                                                      any(offsets[i] <= c < offsets[i] + n for c in channels))]
    # Generic "Nc" layouts let amerge stack streams whose named layouts overlap (stereo + stereo, mono + 5.1).
    chain = ''.join(f'[0:a:{i}]aresample={SAMPLE_RATE},channelmap=map={"|".join(map(str, range(streams[i])))}:'
                    f'channel_layout={streams[i]}c[s{i}];' for i in used)
    chain += ''.join(f'[s{i}]' for i in used)
    chain += f'amerge=inputs={len(used)},' if len(used) > 1 else 'anull,'
    if channels is not None:
        base = {i: sum(streams[j] for j in used if j < i) - offsets[i] for i in used}
        stream_of = lambda c: next(i for i in used if offsets[i] <= c < offsets[i] + streams[i])
        picked = [c + base[stream_of(c)] for c in channels]
        chain += f'pan={len(picked)}c|' + '|'.join(f'c{n}=c{c}' for n, c in enumerate(picked)) + ','
    # RMS over all selected channels together: stereo antiphase cannot cancel out.
    return chain + (f'highpass=f=60,asetnsamples=n={round(SAMPLE_RATE * HOP)}:p=0,'
                    'astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,'
                    'ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-')


def item_envelope(path, source_start, duration, mapping, cancel=None):
    """RMS dBFS every HOP seconds for [source_start, source_start + duration) of one file."""
    streams = audio_channels(path)
    count = round(duration / HOP)
    if not streams:
        return array.array('f', [FLOOR_DB] * count)
    channels = mapped_channels(mapping, streams)
    if channels == []:
        return array.array('f', [FLOOR_DB] * count)
    args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-nostats', '-v', 'error',
            '-protocol_whitelist', 'file,pipe', '-ss', f'{source_start:.6f}', '-t', f'{duration:.6f}',
            '-i', str(path), '-vn', '-sn', '-dn', '-filter_complex', item_filter(streams, channels),
            '-f', 'null', '-']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
                            errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    errors = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()
    values = array.array('f')
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if line.startswith('lavfi.astats.Overall.RMS_level='):
                text = line.split('=', 1)[1].strip()
                value = float(text) if text not in ('-inf', 'inf', 'nan') else FLOOR_DB
                values.append(max(FLOOR_DB, value))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(5)
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode:
        raise ValueError(f'Audio scan failed for {Path(path).name}:\n' + ''.join(errors)[-800:])
    values.extend([FLOOR_DB] * max(0, count - len(values)))
    return values[:count]


def track_envelope(track, timeline, cancel=None, progress=lambda fraction: None):
    """Rebuild one Resolve audio track's loudness over timeline time from its items."""
    fps, start = timeline['fps'], timeline['start']
    total = round(float((timeline['end'] - start) / fps) / HOP)
    envelope = array.array('f', [FLOOR_DB] * total)
    items = [i for i in track['items'] if i.get('enabled', True) and i.get('path')]
    for n, item in enumerate(items):
        offset = round(float((item['start'] - start) / fps) / HOP)
        duration = float((item['end'] - item['start']) / fps)
        values = item_envelope(item['path'], item['source_seconds'], duration, item.get('mapping'), cancel)
        for i, value in enumerate(values, offset):
            if 0 <= i < total and value > envelope[i]:
                envelope[i] = value
        progress((n + 1) / len(items))
    return envelope


def frame_count(path):
    result = run([tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                  'stream=nb_frames', '-of', 'default=nw=1:nk=1', str(path)], timeout=60)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def make_teaser(full, teaser, frames, fps, fade):
    """First `frames` frames of a rendered full clip, with picture and sound fading out at the end."""
    seconds = float(frames / fps)
    fade_start = max(0.0, seconds - fade)
    filters = ['-vf', f'fade=t=out:st={fade_start:.6f}:d={fade:.6f}',
               '-af', f'afade=t=out:st={fade_start:.6f}:d={fade:.6f}'] if fade > 0 else []
    args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-i', str(full), '-frames:v', str(frames), '-t', f'{seconds:.6f}', *filters,
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-movflags', '+faststart', '-n', str(teaser)]
    result = run(args, timeout=3600)
    if result.returncode:
        raise ValueError(f'Could not make {Path(teaser).name}:\n{result.stderr[-800:]}')


def fade_out(path, frames, fps, fade):
    """Fade a rendered clip's last `fade` seconds of picture and sound to black and silence, in place."""
    path = Path(path)
    seconds = float(frames / fps)
    start = max(0.0, seconds - fade)
    temporary = path.with_name(path.stem + '.fading.mp4')
    args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-y',
            '-i', str(path), '-vf', f'fade=t=out:st={start:.6f}:d={fade:.6f}', '-af', f'afade=t=out:st={start:.6f}:d={fade:.6f}',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-movflags', '+faststart', str(temporary)]
    result = run(args, timeout=3600)
    if result.returncode:
        temporary.unlink(missing_ok=True)
        raise ValueError(f'Could not fade out {path.name}:\n{result.stderr[-800:]}')
    temporary.replace(path)    # only replace the render once the faded copy is complete


STILL_TYPES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.gif', '.psd', '.exr', '.dpx', '.tga', '.heic'}


def still_video(image, frames, fps, width, height, target):
    """A video of `frames` frames showing one still image, so a cuts timeline can hold it for its full length.
    The image is scaled once, one minute is encoded, and copies are joined without re-encoding: a 3-hour
    still takes seconds instead of re-scaling the image for every frame."""
    target = Path(target)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    rate = f'{fps.numerator}/{fps.denominator}'
    work = target.with_suffix('.parts')
    work.mkdir(exist_ok=True)
    try:
        scaled = work / 'scaled.png'
        fit = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
               f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2')
        steps = [[tool('ffmpeg'), '-v', 'error', '-y', '-i', str(image), '-vf', fit, '-frames:v', '1', str(scaled)]]
        minute = max(1, round(60 * fps))
        whole, rest = divmod(int(frames), minute)
        parts = []
        for name, count in (('minute.mp4', minute if whole else 0), ('rest.mp4', rest)):
            if count:
                steps.append([tool('ffmpeg'), '-v', 'error', '-y', '-loop', '1', '-framerate', rate, '-i', str(scaled),
                              '-frames:v', str(count), '-vf', 'format=yuv420p', '-c:v', 'libx264', '-preset', 'medium',
                              '-tune', 'stillimage', '-crf', '18', '-g', str(minute), '-r', rate, str(work / name)])
        parts = ['minute.mp4'] * whole + (['rest.mp4'] if rest else [])
        (work / 'list.txt').write_text(''.join(f"file '{p}'\n" for p in parts), encoding='utf-8')
        partial = target.with_suffix('.partial.mp4')
        steps.append([tool('ffmpeg'), '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(work / 'list.txt'),
                      '-c', 'copy', '-movflags', '+faststart', str(partial)])
        for args in steps:
            result = run(args, timeout=3600)
            if result.returncode:
                raise ValueError(f'Could not prepare the still {Path(image).name}:\n{result.stderr[-800:]}')
        partial.replace(target)
        return target
    finally:
        for leftover in work.glob('*'):
            leftover.unlink(missing_ok=True)
        work.rmdir()
