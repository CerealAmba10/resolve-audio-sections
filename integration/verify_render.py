"""Compare exported teaser fades with the first 30 seconds of its full clip."""
import array
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / 'tools/ffmpeg.exe'


def decode(path, start, seconds, audio):
    args = [str(FFMPEG), '-v', 'error', '-ss', str(start), '-i', str(path), '-t', str(seconds)]
    args += ['-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'] if audio else [
        '-an', '-frames:v', '1', '-vf', 'scale=128:72,format=gray', '-f', 'rawvideo', '-']
    return subprocess.run(args, capture_output=True, check=True).stdout


def energy(data):
    values = array.array('f', data)
    return math.sqrt(sum(v * v for v in values) / len(values))


def verify(teaser, full):
    measurements = []
    for position in (28.8, 29.0, 29.5, 29.9):
        video = [sum(decode(p, position, 0.1, False)) / (128 * 72) for p in (teaser, full)]
        audio = [energy(decode(p, position, 0.08, True)) for p in (teaser, full)]
        measurements.append(dict(seconds=position, brightness_ratio=video[0] / video[1],
                                 audio_rms_ratio=audio[0] / audio[1]))
    assert measurements[0]['brightness_ratio'] > 0.95
    assert measurements[0]['audio_rms_ratio'] > 0.95
    assert measurements[-1]['brightness_ratio'] < 0.2
    assert measurements[-1]['audio_rms_ratio'] < 0.2
    return measurements


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
