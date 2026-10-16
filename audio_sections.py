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
