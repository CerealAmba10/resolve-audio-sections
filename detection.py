"""Pure section detection over timeline time. No I/O; everything is in seconds or frames."""
import math
import re
from fractions import Fraction
from pathlib import Path

HOP = 0.02          # envelope resolution: one RMS value per 20 ms
FLOOR_DB = -120.0   # value used for digital silence and timeline gaps

DEFAULTS = dict(min_silence=1.0, padding=0.25, min_burst=0.1, min_length=30.0, max_length=180.0, cut_window=30.0, teaser=30.0, fade=1.0)

_NTSC = {'23.976': Fraction(24000, 1001), '29.97': Fraction(30000, 1001), '47.952': Fraction(48000, 1001),
         '59.94': Fraction(60000, 1001), '119.88': Fraction(120000, 1001)}


def parse_fps(value):
    text = str(value).strip().split(' ')[0].rstrip('DF')
    if text in _NTSC:
        return _NTSC[text]
    fps = Fraction(text).limit_denominator(1001)
    if fps <= 0:
        raise ValueError(f'Unknown timeline frame rate: {value}')
    return fps


def source_seconds(item, fps):
    """Where in its file a timeline item starts. Returns (seconds, trusted).

    Source frames count from the start of the file. Source times include the file's embedded
    start timecode (a 51:12 timecode gives 3072 s too much), so they are only a fallback.
    trusted is False when the item looks retimed.
    """
    duration = float((item['end'] - item['start']) / fps)
    first, last = item.get('source_start_frame'), item.get('source_end_frame')
    if isinstance(first, (int, float)):
        try:
            media_fps = parse_fps(item.get('media_fps') or fps)
        except (ValueError, ZeroDivisionError):
            media_fps = fps
        span = float((last - first + 1) / media_fps) if isinstance(last, (int, float)) else duration
        return float(first / media_fps), abs(span - duration) <= max(0.1, duration * 0.01)
    return float(item.get('source_start_time') or 0), False


def auto_threshold(envelope):
    """24 dB under the speech peaks, but at least 6 dB over the quietest 2%.

    Peak-relative, because recordings with almost no true silence have no reliable noise floor;
    the floor term keeps the line above steady hiss on noisy mics.
    """
    values = sorted(v for v in envelope if v > -90)
    if len(values) < 50:
        return -40.0
    floor, loud = values[len(values) // 50], values[len(values) * 95 // 100]
    return round(min(-20.0, max(-70.0, loud - 24, floor + 6)), 1)


def active_runs(envelopes, thresholds, hop=HOP):
    """Union of above-threshold runs across primary tracks, as (start, end) seconds."""
    runs = []
    for envelope, threshold in zip(envelopes, thresholds):
        start = None
        for i, value in enumerate(envelope):
            if value > threshold:
                if start is None:
                    start = i
            elif start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(envelope)))
    merged = []
    for start, end in sorted(runs):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start * hop, end * hop) for start, end in merged]


def find_sections(runs, duration, min_silence, padding, min_burst, min_length):
    """Group speech runs into sections split by silences of at least min_silence.

    Returns every candidate as dict(start, end, keep); keep is False when too short.
    Padding extends each edge into the silence but never past the middle of the gap,
    so neighbouring clips cannot overlap.
    """
    groups = []
    for start, end in runs:
        if end - start < min_burst:
            continue
        if groups and start - groups[-1][1] < min_silence:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    sections = []
    for i, (start, end) in enumerate(groups):
        left = (groups[i - 1][1] + start) / 2 if i else 0.0
        right = (end + groups[i + 1][0]) / 2 if i + 1 < len(groups) else duration
        start, end = max(left, start - padding), min(right, end + padding)
        sections.append(dict(start=start, end=end, keep=end - start > min_length))
    return sections


def quietest(envelopes, thresholds, start, end, hop=HOP, smooth=0.5):
    """Time in [start, end) where the primary tracks are quietest, relative to their thresholds,
    averaged over `smooth` seconds so a brief stop inside a word does not win."""
    first, last, width = int(start / hop), int(end / hop), max(1, round(smooth / hop))
    level = [max((e[i] - t if i < len(e) else FLOOR_DB) for e, t in zip(envelopes, thresholds))
             for i in range(first, last)] if envelopes else []
    if len(level) <= width:
        return end
    total = sum(level[:width])
    best, best_at = total, 0
    for i in range(width, len(level)):
        total += level[i] - level[i - width]
        if total < best:
            best, best_at = total, i - width + 1
    return (first + best_at + width / 2) * hop


def split_long(sections, max_length, window, envelopes, thresholds, min_length, runs=(), padding=0.0):
    """Cut sections longer than max_length at their quietest point in the last `window`
    seconds before max_length; the rest continues as the next clip, split the same way.

    A cut that falls in a pause trims the pause off both clips, leaving `padding` of quiet
    after the last sound and before the next, like any other clip edge.
    """
    if max_length <= 0:
        return sections
    result = []
    for section in sections:
        start, end = section['start'], section['end']
        while end - start > max_length:
            cut = quietest(envelopes, thresholds, start + max(1.0, max_length - window), start + max_length)
            before = max((e for s, e in runs if start < e <= cut), default=cut)
            after = min((s for s, e in runs if cut <= s < end), default=cut)
            if any(s < cut < e for s, e in runs):
                before = after = cut
            result.append(dict(start=start, end=min(cut, before + padding), keep=cut - start > min_length))
            start = max(cut, after - padding)
        result.append(dict(start=start, end=end, keep=end - start > min_length))
    return result


def to_frames(start, end, fps):
    """Inclusive frame range covering [start, end) seconds."""
    first = math.floor(Fraction(start).limit_denominator(10**6) * fps)
    last = math.ceil(Fraction(end).limit_denominator(10**6) * fps) - 1
    return first, max(first, last)


def next_number(stem, taken_names):
    pattern = re.compile(re.escape(stem) + r' (\d+)T?$', re.IGNORECASE)
    numbers = [int(m.group(1)) for m in map(pattern.fullmatch, taken_names) if m]
    return max(numbers, default=0) + 1


def existing_names(folder):
    folder = Path(folder)
    return [p.stem for p in folder.glob('*.mp4')] if folder.is_dir() else []


if __name__ == '__main__':
    assert parse_fps('23.976') == Fraction(24000, 1001) and parse_fps('24') == 24 and parse_fps('29.97 DF') == Fraction(30000, 1001)
    # Values Resolve 21.1.1 reported for duck test.mp4 (start timecode 51:12) trimmed to 49.96 s.
    resolve_item = dict(start=86400, end=89522, source_start_frame=1199, source_end_frame=4320, media_fps=24,
                        source_start_time=3121.958333, source_end_time=3252.041667)
    assert source_seconds(resolve_item, Fraction(24)) == (1199 / 24, True)
    assert to_frames(1.0, 2.0, Fraction(24)) == (24, 47)
    # Two tracks: track B covers the gap in track A, so only one silence (2.0-3.5 s) remains.
    a = [-20] * 50 + [-80] * 125 + [-20] * 100          # active 0-1, 3.5-5.5
    b = [-80] * 50 + [-20] * 50 + [-80] * 175           # active 1-2
    runs = active_runs([a, b], [-40, -40])
    assert [(round(s, 2), round(e, 2)) for s, e in runs] == [(0, 2.0), (3.5, 5.5)], runs
    s = find_sections(runs, 6.0, min_silence=1.0, padding=0.25, min_burst=0.1, min_length=1.0)
    assert [(round(x['start'], 2), round(x['end'], 2), x['keep']) for x in s] == [(0, 2.25, True), (3.25, 5.75, True)], s
    # Padding is capped at the gap midpoint; short groups are kept but flagged.
    s = find_sections([(0, 1), (1.4, 1.45), (3, 3.2)], 3.5, 0.3, 5, 0.1, 1.5)
    assert [(x['start'], x['end'], x['keep']) for x in s] == [(0, 2.0, True), (2.0, 3.5, False)], s
    assert auto_threshold([-80] * 100 + [-20] * 900) == -44.0      # peaks -20 dB: 24 dB under
    assert auto_threshold([-42] * 100 + [-30] * 900) == -36.0      # loud hiss at -42: stay 6 dB over it
    # 400 s of speech with a quiet dip at 160-161 s: cut there, then again inside 310-340 s.
    env = [-20.0] * 20000
    env[8000:8050] = [-60.0] * 50
    s = split_long([dict(start=0, end=400, keep=True)], 180, 30, [env], [-40], 30)
    assert len(s) == 3 and s[0]['start'] == 0 and 160 <= s[1]['start'] <= 161, s
    assert 310 <= s[2]['start'] <= 340.5 and s[-1]['end'] == 400 and all(x['end'] - x['start'] <= 180 for x in s), s
    # Same dip as a real pause: both clips stop 0.25 s into it instead of sharing the silence.
    s = split_long([dict(start=0, end=400, keep=True)], 180, 30, [env], [-40], 30,
                   runs=[(0, 160.0), (161.0, 400)], padding=0.25)
    assert (s[0]['end'], s[1]['start']) == (160.25, 160.75), s
    assert next_number('duck test', ['duck test 1T', 'duck test 3', 'other 9']) == 4
    print('detection ok')
