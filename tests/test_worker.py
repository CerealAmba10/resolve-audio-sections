import base64
import json
import tempfile
import unittest
from pathlib import Path

from lupa.lua51 import LuaRuntime
from worker_core import read_pref, next_number, write_state


class WorkerTests(unittest.TestCase):
    def test_mailbox_handles_partial_preferences_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Fusion.prefs'
            value = {'id': '123-456', 'source': "C:/film ' é.mp4"}
            encoded = base64.b64encode(json.dumps(value).encode()).decode()
            path.write_text('AudioSectionsRequestV1 = "' + encoded + '",', encoding='utf-8')
            self.assertEqual(read_pref(path, 'AudioSectionsRequestV1'), value)
            path.write_text('AudioSectionsRequestV1 = "partial', encoding='utf-8')
            self.assertIsNone(read_pref(path, 'AudioSectionsRequestV1'))

    def test_numbering_respects_existing_outputs_and_project_number(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'film 7T.mp4').touch()
            (root / 'other 99.mp4').touch()
            self.assertEqual(next_number('film.mp4', root, 2), 8)
            self.assertEqual(next_number('film.mp4', root, 10), 10)

    def test_atomic_state_is_valid_lua_and_preserves_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            message = 'Stopped "film"\nPlease try again. é'
            write_state(folder, '123-456', 'error', message, cancel=True)
            state = LuaRuntime().execute((Path(folder) / 'state.lua').read_text(encoding='utf-8'))
            self.assertEqual(state.message, message)
            self.assertTrue(state.cancel)

    def test_native_bridge_queues_and_renders_only_new_jobs_without_console(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.execute(Path('tests/resolve_fake.lua').read_text(encoding='utf-8'))
            lua.globals().home = str(root).replace('\\', '/')
            lua.execute('''
                local p=resolve:GetProjectManager():GetCurrentProject()
                local pool=p:GetMediaPool()
                function pool:GetSelectedClips() return {{GetClipProperty=function() return {['File Path']='C:/film.mp4'} end}} end
                function p:GetUniqueId() return 'project-1' end
                function p:StartRendering(jobs) state.started=jobs; return true end
                function p:GetRenderJobStatus() return {JobStatus='Complete'} end
                local prefs={}
                fusion={SetPrefs=function(self,k,v) prefs[k]=v end,GetPrefs=function(self,k) if prefs[k]~=nil then return prefs[k] end end,SavePrefs=function() return true end}
                resolve.GetProjectManager=function() return {GetCurrentProject=function() return p end,SaveProject=function() return true end} end
                loadfile=function(path)
                    if path:match('heartbeat.lua$') then return function() return {time=os.time()} end end
                    return function() return {id=prefs['Global.AudioSectionsActiveV1'],status='ready'} end
                end
                dofile=function() return {jobIds={'new-one','new-two'}} end
                bmd={wait=function() end}
                io=nil; os.execute=nil
            ''')
            code = Path('native_bridge.lua').read_text(encoding='utf-8').replace('__HOME__', 'home')
            lua.execute(code)
            self.assertEqual(list(lua.globals().state.started.values()), ['new-one', 'new-two'])
