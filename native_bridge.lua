-- Workspace > Scripts > Audio Sections. Uses only sandbox-safe APIs (Free has no io/os.execute):
-- the helper app is reached through Fusion prefs (Resolve -> helper) and loadfile (helper -> Resolve).
local HOME = __HOME__
local f = fusion or resolve:Fusion()

local function json(value)
    local kind = type(value)
    if kind == 'table' then
        local parts = {}
        if #value > 0 or next(value) == nil then
            for _, v in ipairs(value) do parts[#parts + 1] = json(v) end
            return '[' .. table.concat(parts, ',') .. ']'
        end
        for k, v in pairs(value) do parts[#parts + 1] = json(tostring(k)) .. ':' .. json(v) end
        return '{' .. table.concat(parts, ',') .. '}'
    elseif kind == 'number' then
        if value ~= value or value == math.huge or value == -math.huge then return 'null' end
        return string.format('%.10g', value)
    elseif kind == 'boolean' then
        return tostring(value)
    elseif value == nil then
        return 'null'
    end
    return '"' .. tostring(value):gsub('[%z\1-\31\\"]', function(c)
        if c == '\\' then return '\\\\' end
        if c == '"' then return '\\"' end
        return string.format('\\u%04x', string.byte(c))
    end) .. '"'
end

local function base64(data)
    local chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local output = {}
    for i = 1, #data, 3 do
        local a, b, c = data:byte(i, i + 2)
        local n = a * 65536 + (b or 0) * 256 + (c or 0)
        output[#output + 1] = chars:sub(math.floor(n / 262144) % 64 + 1, math.floor(n / 262144) % 64 + 1)
        output[#output + 1] = chars:sub(math.floor(n / 4096) % 64 + 1, math.floor(n / 4096) % 64 + 1)
        output[#output + 1] = b and chars:sub(math.floor(n / 64) % 64 + 1, math.floor(n / 64) % 64 + 1) or '='
        output[#output + 1] = c and chars:sub(n % 64 + 1, n % 64 + 1) or '='
    end
    return table.concat(output)
end

local function announce(id, status, progress, message, extra)
    local result = {id = id, status = status, progress = progress or 0, message = message or ''}
    for k, v in pairs(extra or {}) do result[k] = v end
    f:SetPrefs('Global.AudioSectionsResultV1', base64(json(result)))
    f:SavePrefs()
end

local function call(object, method, ...)
    if not object or not object[method] then return nil end
    local ok, value = pcall(object[method], object, ...)
    if ok then return value end
end

local function load_state(id)
    local chunk = loadfile(HOME .. '/state.lua')
    if not chunk then return nil end
    local ok, value = pcall(chunk)
    if ok and type(value) == 'table' and value.id == id then return value end
end

local function helper_alive()
    local beat = loadfile(HOME .. '/heartbeat.lua')
    local ok, value = pcall(beat or function() return nil end)
    return ok and value and os.time() - value.time < 15
end

-- Timeline markers as absolute frames (GetMarkers keys are offsets from the timeline start).
local function marker_frames(timeline)
    local frames = {}
    for offset in pairs(call(timeline, 'GetMarkers') or {}) do frames[#frames + 1] = timeline:GetStartFrame() + offset end
    table.sort(frames)
    return frames
end

-- A timeline's layout: enough for the helper to rebuild each audio track from its source files,
-- plus a fingerprint so edits made while the helper is open are caught before rendering.
local function describe(timeline)
    local settings = timeline:GetSettings() or {}
    local data = {name = timeline:GetName(), uid = call(timeline, 'GetUniqueId'),
        fps = settings.timelineFrameRate or timeline:GetSetting('timelineFrameRate'),
        start = timeline:GetStartFrame(), ['end'] = timeline:GetEndFrame(),
        width = settings.timelineResolutionWidth, height = settings.timelineResolutionHeight,
        audio = {}, video = {}, markers = marker_frames(timeline)}
    for _, kind in ipairs({'video', 'audio'}) do
        for index = 1, timeline:GetTrackCount(kind) do
            local track = {index = index, name = call(timeline, 'GetTrackName', kind, index) or '',
                enabled = call(timeline, 'GetIsTrackEnabled', kind, index) ~= false,
                subtype = kind == 'audio' and call(timeline, 'GetTrackSubType', kind, index) or nil, items = {}}
            for _, item in ipairs(timeline:GetItemListInTrack(kind, index) or {}) do
                local media = call(item, 'GetMediaPoolItem')
                local properties = media and call(media, 'GetClipProperty') or {}
                track.items[#track.items + 1] = {name = item:GetName(), start = item:GetStart(), ['end'] = item:GetEnd(),
                    path = properties['File Path'], media_fps = properties.FPS,
                    source_start_time = call(item, 'GetSourceStartTime'), source_end_time = call(item, 'GetSourceEndTime'),
                    source_start_frame = call(item, 'GetSourceStartFrame'), source_end_frame = call(item, 'GetSourceEndFrame'),
                    enabled = call(item, 'GetClipEnabled') ~= false,
                    mapping = kind == 'audio' and call(item, 'GetSourceAudioChannelMapping') or nil}
            end
            table.insert(data[kind], track)
        end
    end
    return data
end

local function fingerprint(timeline)
    local parts = {timeline:GetStartFrame(), timeline:GetEndFrame()}
    for _, kind in ipairs({'video', 'audio'}) do
        for index = 1, timeline:GetTrackCount(kind) do
            for _, item in ipairs(timeline:GetItemListInTrack(kind, index) or {}) do
                parts[#parts + 1] = kind .. index .. ':' .. item:GetStart() .. '-' .. item:GetEnd()
            end
        end
    end
    return table.concat(parts, '|')
end

-- Rebuild the chosen clips as separate pieces on a new timeline, each track at its original position,
-- so they can be watched, bladed, merged (delete the through edit) or deleted in Resolve before rendering.
-- Pieces are plain media: effects and transforms on the source items are not copied.
-- A still image only has one source frame, so pieces cut from it collapse to one frame. The helper
-- turns each still into a video of its item's length; import it once into an "Audio Sections stills" bin.
local function still_media(pool, path)
    local root, bin = pool:GetRootFolder(), nil
    for _, folder in ipairs(root:GetSubFolderList() or {}) do
        if folder:GetName() == 'Audio Sections stills' then bin = folder end
    end
    bin = bin or assert(pool:AddSubFolder(root, 'Audio Sections stills'), 'Could not create the stills bin.')
    local wanted = path:gsub('\\', '/'):lower()
    for _, clip in ipairs(bin:GetClipList() or {}) do
        if tostring(clip:GetClipProperty()['File Path']):gsub('\\', '/'):lower() == wanted then return clip end
    end
    local previous = pool:GetCurrentFolder()
    pool:SetCurrentFolder(bin)
    local imported = pool:ImportMedia({path})
    if previous then pool:SetCurrentFolder(previous) end
    return assert(imported and imported[1], 'Could not import ' .. path)
end

local function build_cuts(p, source, clips, teaser, stills)
    local pool = p:GetMediaPool()
    local names = {}
    for i = 1, p:GetTimelineCount() do names[p:GetTimelineByIndex(i):GetName()] = true end
    local name, n = source:GetName() .. ' (cuts)', 1
    while names[name] do n = n + 1; name = source:GetName() .. ' (cuts ' .. n .. ')' end
    local cuts = assert(pool:CreateEmptyTimeline(name), 'Could not create timeline ' .. name)
    local ok, problem = pcall(function()
        assert(p:SetCurrentTimeline(cuts), 'Could not open timeline ' .. name)
        local s = source:GetSettings() or {}
        cuts:SetSettings({useCustomSettings = '1', timelineFrameRate = s.timelineFrameRate,
            timelineResolutionWidth = s.timelineResolutionWidth, timelineResolutionHeight = s.timelineResolutionHeight})
        while cuts:GetTrackCount('video') < source:GetTrackCount('video') do
            assert(cuts:AddTrack('video'), 'Could not add a video track.')
        end
        -- Match audio track formats (mono, stereo, 5.1...) so each source track lands on a like track.
        local audio = source:GetTrackCount('audio')
        local first_index = 2
        if audio > 0 and call(cuts, 'GetTrackSubType', 'audio', 1) ~= call(source, 'GetTrackSubType', 'audio', 1) then
            first_index = 1
        end
        for index = first_index, audio do
            assert(cuts:AddTrack('audio', source:GetTrackSubType('audio', index)), 'Could not add audio track ' .. index)
        end
        if first_index == 1 then assert(cuts:DeleteTrack('audio', 1), 'Could not replace the first audio track.') end
        local offset = cuts:GetStartFrame() - source:GetStartFrame()
        local colors, placed, ratios = {'Orange', 'Teal'}, 0, {}
        -- Source frames per timeline frame, measured per media item. AppendToTimeline counts in the media's
        -- own frames, which differ from the timeline's when it has another frame rate or is audio-only, so a
        -- piece that comes out the wrong length is measured, removed and placed again.
        local function place(media, key, source_start, offset, length, kind, index, record)
            for attempt = 1, 2 do
                local ratio = ratios[key] or 1
                local from = source_start + math.floor(offset * ratio + 0.5)
                local span = math.max(1, math.floor(length * ratio + 0.5))
                -- endFrame is exclusive in Resolve 21.1.1.
                local added = pool:AppendToTimeline({{mediaPoolItem = media, startFrame = from, endFrame = from + span,
                    mediaType = kind == 'video' and 1 or 2, trackIndex = index, recordFrame = record}})
                local piece = assert(added and added[1], 'Could not place a piece on ' .. kind .. ' track ' .. index)
                local actual = piece:GetEnd() - piece:GetStart()
                if math.abs(actual - length) <= 1 then
                    ratios[key] = ratio
                    return piece
                end
                call(cuts, 'DeleteClips', {piece}, false)
                assert(attempt == 1 and not ratios[key] and actual > 1, string.format(
                    'A piece on %s track %d came out %d frames long instead of %d, so the cuts timeline was not made.',
                    kind, index, actual, length))
                ratios[key] = span / actual
            end
        end
        for number, clip in ipairs(clips) do
            local first, stop = source:GetStartFrame() + clip.first, source:GetStartFrame() + clip.last + 1
            local group = {}
            for _, kind in ipairs({'video', 'audio'}) do
                for index = 1, source:GetTrackCount(kind) do
                    for _, item in ipairs(source:GetItemListInTrack(kind, index) or {}) do
                        local a, b = math.max(first, item:GetStart()), math.min(stop, item:GetEnd())
                        local media = call(item, 'GetMediaPoolItem')
                        local still = kind == 'video' and stills and stills[kind .. index .. ':' .. string.format('%d', item:GetStart())]
                        if b > a and media and call(item, 'GetClipEnabled') ~= false then
                            local source_start, key = item:GetSourceStartFrame(), kind .. index .. ':' .. tostring(item:GetStart())
                            if still then media, source_start, key = still_media(pool, still), 0, still end
                            local piece = place(media, key, source_start, a - item:GetStart(), b - a, kind, index, a + offset)
                            call(piece, 'SetClipColor', colors[number % 2 + 1])
                            local mapping = kind == 'audio' and call(item, 'GetSourceAudioChannelMapping')
                            if mapping then call(piece, 'SetSourceAudioChannelMapping', mapping) end
                            group[#group + 1] = piece
                            placed = placed + 1
                        end
                    end
                end
            end
            -- One clip's video and audio pieces move, blade and delete together.
            if #group > 1 then call(cuts, 'SetClipsLinked', group, true) end
            -- Where the teaser ends by default; drag it to change that clip's teaser. Pieces keep their
            -- source positions, so the clip starts clip.first frames into the cuts timeline.
            if teaser and clip.last - clip.first + 1 > teaser then
                call(cuts, 'AddMarker', clip.first + teaser, 'Blue', 'Teaser end', 'Drag to change where this teaser ends.', 1)
            end
        end
        assert(placed > 0, 'No clips were placed on the cuts timeline.')
    end)
    if not ok then
        pool:DeleteTimelines({cuts})
        p:SetCurrentTimeline(source)
        error(tostring(problem) .. ' The unfinished cuts timeline was removed.', 0)
    end
    return name
end

local id = tostring(os.time()) .. '-' .. tostring(math.random(100000, 999999))

local function run()
    local p = assert(resolve:GetProjectManager():GetCurrentProject(), 'Open a project first.')
    assert(not p:IsRenderingInProgress(), 'Wait for the current render to finish.')
    assert(helper_alive(), 'Run the Audio Sections installer once to start its background helper.')
    local active = f:GetPrefs('Global.AudioSectionsActiveV1')
    local active_time = tonumber((f:GetPrefs('Global.AudioSectionsHeartbeatV1'))) or 0
    assert(not active or active == '' or os.time() - active_time > 15, 'Audio Sections is already open.')

    local timeline = p:GetCurrentTimeline()
    if not timeline then
        -- No timeline open: build one from a single selected recording so raw files stay one click.
        local selected = p:GetMediaPool():GetSelectedClips() or {}
        assert(#selected == 1 and (selected[1]:GetClipProperty()['File Path'] or '') ~= '',
            'Open the timeline to cut, or select one recording in the Media Pool.')
        local name = selected[1]:GetName():gsub('%.[^.]+$', '')
        timeline = assert(p:GetMediaPool():CreateTimelineFromClips(name, {selected[1]}),
            'Could not create a timeline from the selected recording.')
    end
    local print_ok, before = pcall(fingerprint, timeline)
    assert(print_ok, 'Could not read the timeline.')
    local queued = {}
    for _, job in ipairs(p:GetRenderJobList() or {}) do
        if job.OutputFilename then queued[#queued + 1] = tostring(job.OutputFilename):gsub('%.%w+$', '') end
    end
    f:SetPrefs('Global.AudioSectionsActiveV1', id)
    f:SetPrefs('Global.AudioSectionsHeartbeatV1', os.time())
    f:SetPrefs('Global.AudioSectionsRequestV2', base64(json({id = id, project = p:GetName(),
        timeline = describe(timeline), queued = queued})))
    f:SavePrefs()

    -- Command loop while the Audio Sections window is open. Each helper command carries a higher seq:
    -- 'cuts' builds a review timeline, 'pieces' reads back its edited pieces, 'render' queues and
    -- renders (on the scanned timeline, or on a cuts timeline whose pieces were read), then ends.
    local state, seq, prints = nil, 0, {}
    local function find(name)
        for i = 1, p:GetTimelineCount() do
            local t = p:GetTimelineByIndex(i)
            if t:GetName() == name then return t end
        end
    end
    local deadline = os.time() + 4 * 3600
    while true do
        assert(os.time() < deadline, 'Audio Sections timed out waiting for a choice.')
        f:SetPrefs('Global.AudioSectionsHeartbeatV1', os.time())
        assert(helper_alive(), 'The Audio Sections helper stopped. Run the installer again, then retry.')
        state = load_state(id)
        if state and state.status == 'error' then error(state.message, 0) end
        if state and state.status == 'cancelled' then return end
        if state and state.status == 'ready' and (state.seq or 1) > seq then
            seq = state.seq or 1
            local current = resolve:GetProjectManager():GetCurrentProject()
            assert(current and current:GetName() == p:GetName(), 'The open project changed. Return to it and try again.')
            if state.action == 'render' or not state.action then break end
            local done, result = pcall(function()
                if state.action == 'cuts' then
                    assert(fingerprint(timeline) == before, 'The timeline was edited after scanning. Run Audio Sections again.')
                    local name = build_cuts(p, timeline, state.clips, state.teaser, state.stills)
                    return {status = 'cuts_ready', timeline = name, start = find(name):GetStartFrame(),
                        message = 'Made "' .. name .. '".'}
                end
                local cuts = assert(find(state.timeline), 'Timeline "' .. tostring(state.timeline) .. '" was not found.')
                if state.action == 'undo' then
                    -- Only ever asked for a cuts timeline Audio Sections made. Leave it before deleting it.
                    local scanned = state.timeline == timeline:GetName()
                    if not scanned then p:SetCurrentTimeline(timeline) end
                    assert(p:GetMediaPool():DeleteTimelines({cuts}), 'Could not delete "' .. state.timeline .. '".')
                    prints[state.timeline] = nil
                    return {status = 'undone', timeline = state.timeline, ended = scanned,
                        message = 'Deleted "' .. state.timeline .. '".'}
                end
                local track
                for _, kind in ipairs({'video', 'audio'}) do
                    for index = 1, cuts:GetTrackCount(kind) do
                        local items = cuts:GetItemListInTrack(kind, index) or {}
                        if not track and #items > 0 then track = items end
                    end
                end
                local pieces = {}
                for _, item in ipairs(track or {}) do
                    if call(item, 'GetClipEnabled') ~= false then
                        pieces[#pieces + 1] = {start = item:GetStart(), ['end'] = item:GetEnd()}
                    end
                end
                prints[state.timeline] = fingerprint(cuts)
                return {status = 'pieces', timeline = state.timeline, start = cuts:GetStartFrame(), pieces = pieces,
                    markers = marker_frames(cuts),
                    message = #pieces .. ' pieces on "' .. state.timeline .. '".'}
            end)
            if done then
                result.seq = seq
                announce(id, result.status, 100, result.message, result)
                if result.ended then return end      -- the scanned timeline itself was deleted
            else
                announce(id, 'command_error', 0, tostring(result), {seq = seq})
            end
        end
        bmd.wait(0.25)
    end
    local target = state.timeline and assert(find(state.timeline), 'Timeline "' .. state.timeline .. '" was not found.') or timeline
    local name = target:GetName()
    if prints[name] then
        assert(fingerprint(target) == prints[name], '"' .. name .. '" changed after its edits were loaded. Click Load edits again.')
    else
        assert(target == timeline, 'Click Load edits before rendering "' .. name .. '".')
        assert(fingerprint(timeline) == before, 'The timeline was edited after scanning. Run Audio Sections again.')
    end
    timeline = target
    assert(p:SetCurrentTimeline(timeline), 'Could not reopen timeline ' .. timeline:GetName())
    assert(not p:IsRenderingInProgress(), 'Another render started. Try again when it finishes.')

    if not p:LoadRenderPreset(state.preset) then
        assert(resolve:ImportRenderPreset(HOME .. '/' .. state.preset .. '.xml')
            and p:LoadRenderPreset(state.preset), 'Could not load the "' .. state.preset .. '" render preset.')
    end
    local format = p:GetCurrentRenderFormatAndCodec()
    assert(format and format.format == 'mp4' and tostring(format.codec):gsub('[^%w]', ''):upper() == 'H264',
        'The render preset must use MP4 / H.264.')
    local settings = timeline:GetSettings() or {}
    local jobs = {}
    local ok, problem = pcall(function()
        for _, job in ipairs(state.jobs) do
            assert(p:LoadRenderPreset(state.preset), 'Cannot load render preset.')
            assert(p:SetCurrentRenderMode(1), 'Cannot select single-clip rendering.')
            -- Native H.264 rejects named VideoQuality values; High comes from the saved preset.
            assert(p:SetRenderSettings({SelectAllFrames = false, MarkIn = job.mark_in, MarkOut = job.mark_out,
                TargetDir = state.output, CustomName = job.name, UseUniqueFilenames = false,
                ExportVideo = true, ExportAudio = true, NetworkOptimization = false,
                FormatWidth = tonumber(settings.timelineResolutionWidth),
                FormatHeight = tonumber(settings.timelineResolutionHeight)}), 'Cannot apply render settings for ' .. job.name)
            jobs[#jobs + 1] = assert(p:AddRenderJob(), 'Cannot queue ' .. job.name)
            announce(id, 'rendering', 0, 'Queued ' .. #jobs .. ' of ' .. #state.jobs .. ' clips in Resolve')
        end
    end)
    if not ok then
        for _, job_id in ipairs(jobs) do p:DeleteRenderJob(job_id) end
        error(tostring(problem) .. '\nNo jobs from this batch were kept.', 0)
    end
    pcall(function() resolve:GetProjectManager():SaveProject() end)
    assert(p:StartRendering(jobs), 'Resolve could not start rendering.')
    announce(id, 'rendering', 0, 'Rendering ' .. #jobs .. ' clips in Resolve')
    while p:IsRenderingInProgress() do
        f:SetPrefs('Global.AudioSectionsHeartbeatV1', os.time())
        local progress, finished = 0, {}
        for n, job_id in ipairs(jobs) do
            local status = p:GetRenderJobStatus(job_id)
            progress = progress + (tonumber(status.CompletionPercentage) or 0)
            if status.JobStatus == 'Complete' then finished[#finished + 1] = state.jobs[n].name end
        end
        -- The helper cuts each teaser as soon as its clip appears in 'finished'.
        announce(id, 'rendering', progress / #jobs, 'Rendering ' .. #jobs .. ' clips in Resolve', {finished = finished})
        local latest = load_state(id)
        if latest and latest.cancel then p:StopRendering(); error('Render stopped.', 0) end
        bmd.wait(1)
    end
    for _, job_id in ipairs(jobs) do
        local status = p:GetRenderJobStatus(job_id)
        assert(status.JobStatus == 'Complete', status.Error or 'A clip did not finish rendering.')
    end
    announce(id, 'complete', 100, #jobs .. ' clips rendered.')
end

local ok, problem = pcall(run)
f:SetPrefs('Global.AudioSectionsActiveV1', '')
f:SavePrefs()
if not ok then announce(id, 'error', 0, tostring(problem)); print('Audio Sections: ' .. tostring(problem)) end
