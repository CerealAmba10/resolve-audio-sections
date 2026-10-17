"""Runs native_bridge.lua against a fake Resolve (needs lupa). No real project is touched."""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

import lupa

from worker import write_state

ROOT = Path(__file__).resolve().parent.parent

FAKE = r'''
local function item(s, e, path) return {
    GetName=function() return 'clip' end, GetStart=function() return s end, GetEnd=function() return e end,
    GetMediaPoolItem=function() return {GetClipProperty=function() return {['File Path']=path, FPS='24'} end} end,
    GetSourceStartTime=function() return 0 end, GetSourceEndTime=function() return (e-s)/24 end,
    GetSourceStartFrame=function() return 0 end, GetSourceEndFrame=function() return e-s end,
    GetClipEnabled=function() return true end,
    GetSourceAudioChannelMapping=function() return '{"track_mapping":{"1":{"channel_idx":[1,2],"mute":false}}}' end}
end
tracks = {video={{item(86400, 90721, 'C:/v.mp4')}, {item(86400, 87000, 'C:/cam.mp4')}},
          audio={{item(86400, 90721, 'C:/v.mp4')}, {item(86400, 90721, 'C:/music.wav')}}}
timeline = {GetName=function() return 'Edit' end, GetUniqueId=function() return 'tl1' end,
    GetSettings=function() return {timelineFrameRate='24', timelineResolutionWidth='1920', timelineResolutionHeight='1080'} end,
    GetSetting=function() return '24' end, GetStartFrame=function() return 86400 end, GetEndFrame=function() return 90721 end,
    GetTrackCount=function(self, kind) return #tracks[kind] end,
    GetItemListInTrack=function(self, kind, i) return tracks[kind][i] end,
    GetTrackName=function(self, kind, i) return kind .. ' ' .. i end, GetIsTrackEnabled=function() return true end,
    GetTrackSubType=function() return 'stereo' end}
rendering, jobs, settings_log = false, {}, {}
project = {GetName=function() return 'P' end, IsRenderingInProgress=function() local r = rendering; rendering = false; return r end,
    GetCurrentTimeline=function() return timeline end, SetCurrentTimeline=function() return true end,
    GetRenderJobList=function() return {{OutputFilename='Other 7.mp4'}} end,
    LoadRenderPreset=function() return true end, SetCurrentRenderMode=function() return true end,
    GetCurrentRenderFormatAndCodec=function() return {format='mp4', codec='H264'} end,
    SetRenderSettings=function(self, s) table.insert(settings_log, s); return true end,
    AddRenderJob=function() table.insert(jobs, 'job' .. #jobs + 1); return jobs[#jobs] end,
    DeleteRenderJob=function() return true end,
    StartRendering=function(self, ids) started = ids; rendering = true; return true end,
    GetRenderJobStatus=function() return {JobStatus='Complete', CompletionPercentage=100} end}
resolve = {GetProjectManager=function() return {GetCurrentProject=function() return project end,
    SaveProject=function() return true end} end}
appended, colors, video_tracks = {}, {}, 1
cuts = {GetStartFrame=function() return 86400 end, SetSettings=function() return true end,
    GetTrackCount=function(self, kind) return kind == 'video' and video_tracks or 1 end,
    AddTrack=function(self, kind) if kind == 'video' then video_tracks = video_tracks + 1 end; return true end,
    GetTrackSubType=function() return 'stereo' end}
pool = {CreateEmptyTimeline=function(self, name) created = name; return cuts end,
    AppendToTimeline=function(self, infos) table.insert(appended, infos[1])
        return {{SetClipColor=function(_, c) table.insert(colors, c) end, SetSourceAudioChannelMapping=function() return true end}} end,
    DeleteTimelines=function() deleted = true; return true end}
project.GetMediaPool = function() return pool end
project.GetTimelineCount = function() return 1 end
project.GetTimelineByIndex = function() return {GetName=function() return 'Edit (cuts)' end} end
prefs = {}
fusion = {SetPrefs=function(self, k, v) prefs[k] = v end, GetPrefs=function(self, k) return prefs[k] end,
    SavePrefs=function() end}
'''


class BridgeTest(unittest.TestCase):
    def run_bridge(self, respond):
        home = Path(tempfile.mkdtemp())
        (home / 'heartbeat.lua').write_text(f'return {{time={int(time.time())}}}', encoding='utf-8')
        lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        lua.execute(FAKE)
        g = lua.globals()
        def wait(seconds):
            request = json.loads(base64.b64decode(g.prefs['Global.AudioSectionsRequestV2']))
            respond(home, request, g)
        g.bmd = lua.table(wait=wait)
        script = (ROOT / 'native_bridge.lua').read_text(encoding='utf-8')
        lua.execute(script.replace('__HOME__', '"' + home.as_posix() + '"'))
        result = g.prefs['Global.AudioSectionsResultV1']
        return g, result and json.loads(base64.b64decode(result))

    def test_describes_every_track_and_renders_marked_ranges(self):
        seen = {}
        def respond(home, request, g):
            seen.update(request)
            write_state(home, request['id'], 'ready', preset='Audio Sections High', output='C:/out',
                        jobs=[dict(name='v 1', mark_in=87000, mark_out=88199), dict(name='v 2', mark_in=89000, mark_out=90720)])
        g, result = self.run_bridge(respond)
        timeline = seen['timeline']
        self.assertEqual([len(timeline['audio']), len(timeline['video'])], [2, 2])
        self.assertEqual(timeline['audio'][1]['items'][0]['path'], 'C:/music.wav')
        self.assertIn('channel_idx', timeline['audio'][0]['items'][0]['mapping'])
        self.assertEqual(seen['queued'], ['Other 7'])
        self.assertEqual(result['status'], 'complete', result)
        log = [dict(s) for s in g.settings_log.values()]
        self.assertEqual([(s['MarkIn'], s['MarkOut'], s['CustomName'], s['SelectAllFrames']) for s in log],
                         [(87000, 88199, 'v 1', False), (89000, 90720, 'v 2', False)])
        self.assertEqual(list(g.started.values()), ['job1', 'job2'])

    def test_cancel_in_editor_queues_nothing(self):
        g, _ = self.run_bridge(lambda home, request, g: write_state(home, request['id'], 'cancelled'))
        self.assertEqual(len(g.jobs), 0)
        self.assertEqual(g.prefs['Global.AudioSectionsActiveV1'], '')

    def test_timeline_edited_while_choosing_is_rejected(self):
        def respond(home, request, g):
            write_state(home, request['id'], 'ready', preset='p', output='C:/out', jobs=[dict(name='a', mark_in=1, mark_out=2)])
            g.timeline.GetEndFrame = lambda self: 99999
        g, result = self.run_bridge(respond)
        self.assertEqual(result['status'], 'error')
        self.assertIn('edited', result['message'])
        self.assertEqual(len(g.jobs), 0)

    def test_cuts_timeline_places_each_clip_on_every_track(self):
        def respond(home, request, g):
            write_state(home, request['id'], 'ready', action='cuts',
                        clips=[dict(first=600, last=1199), dict(first=2000, last=2999)])
        g, result = self.run_bridge(respond)
        self.assertEqual((result['status'], result['timeline']), ('cuts_ready', 'Edit (cuts 2)'), result)
        placed = [dict(a) for a in g.appended.values()]
        # Clip 1 = source frames 600-1199: V1, A1, A2 (V2's camera item ends before it). endFrame is exclusive.
        self.assertEqual([(a['mediaType'], a['trackIndex'], a['startFrame'], a['endFrame'], a['recordFrame']) for a in placed],
                         [(1, 1, 600, 1200, 87000), (2, 1, 600, 1200, 87000), (2, 2, 600, 1200, 87000),
                          (1, 1, 2000, 3000, 88400), (2, 1, 2000, 3000, 88400), (2, 2, 2000, 3000, 88400)])
        self.assertEqual(list(g.colors.values()), ['Teal'] * 3 + ['Orange'] * 3)
        self.assertEqual(g.video_tracks, 2)
        self.assertEqual(len(g.jobs), 0)


if __name__ == '__main__':
    unittest.main()
