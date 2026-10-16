"""Project-aware handoff through Resolve's supported metadata export API."""
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from audio_sections import lua_string


def integration_directory():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    return base / 'integration'


def write_project_tools(folder):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    script = '''-- Load once per Resolve session from the internal Lua Console.
function AudioSectionsAnalyze()
    local p = assert(resolve:GetProjectManager():GetCurrentProject(), 'Open a project first.')
    local timeline = assert(p:GetCurrentTimeline(), 'Open a timeline containing your source recording.')
    assert(timeline:Export(REQUEST, resolve.EXPORT_OTIO), 'Cannot send the timeline to the local scanner.')
    print('Audio Sections: sent ' .. timeline:GetName() .. ' to the local scanner. Keep AudioSections open.')
    print('When its scan is ready, run AudioSectionsQueue().')
end
function AudioSectionsQueue()
    AudioSectionsRequireExistingSource = true
    local ok, problem = pcall(dofile, LATEST)
    AudioSectionsRequireExistingSource = nil
    if not ok then error(problem, 0) end
end
print('Audio Sections project tools loaded. Run AudioSectionsAnalyze() or AudioSectionsQueue().')
'''
    header = 'local REQUEST = ' + lua_string((folder / 'request.otio').as_posix()) + '\n'
    header += 'local LATEST = ' + lua_string((folder / 'latest.lua').as_posix()) + '\n'
    (folder / 'project_tools.lua').write_text(header + script, encoding='utf-8')
    if not (folder / 'latest.lua').exists():
        invalidate_latest(folder)
    return folder / 'project_tools.lua'


def invalidate_latest(folder):
    (Path(folder) / 'latest.lua').write_text(
        'error("Audio Sections: a scan is still running or no batch is ready. Check the local scanner.",0)\n', encoding='utf-8')


def publish_batch(folder, batch):
    path = Path(folder) / 'latest.lua'
    path.write_text('dofile(' + lua_string((Path(batch) / 'queue.lua').resolve().as_posix()) + ')\n', encoding='utf-8')


def read_project_request(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    paths = set()
    def visit(value):
        if isinstance(value, dict):
            url = value.get('target_url')
            if url:
                parsed = urlparse(url)
                if parsed.scheme == 'file':
                    decoded = unquote(parsed.path)
                    if parsed.netloc:
                        decoded = '//' + parsed.netloc + decoded
                    elif len(decoded) > 2 and decoded[0] == '/' and decoded[2] == ':':
                        decoded = decoded[1:]
                    paths.add(str(Path(decoded)))
                elif len(parsed.scheme) <= 1:
                    paths.add(str(Path(unquote(url))))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
    visit(data)
    if len(paths) != 1:
        raise ValueError('Analyze a timeline containing one source recording. Use Browse for a timeline with several source files.')
    source = Path(paths.pop())
    if not source.is_file():
        raise ValueError('The project source file is offline. Relink it in Resolve before analyzing.')
    return source
