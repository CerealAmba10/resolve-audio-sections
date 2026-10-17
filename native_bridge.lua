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

local function announce(id, status, progress, message, timeline)
    f:SetPrefs('Global.AudioSectionsResultV1', base64(json({id = id, status = status,
        progress = progress or 0, message = message or '', timeline = timeline})))
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

-- A timeline's layout: enough for the helper to rebuild each audio track from its source files,
-- plus a fingerprint so edits made while the helper is open are caught before rendering.
local function describe(timeline)
    local settings = timeline:GetSettings() or {}
    local data = {name = timeline:GetName(), uid = call(timeline, 'GetUniqueId'),
        fps = settings.timelineFrameRate or timeline:GetSetting('timelineFrameRate'),
        start = timeline:GetStartFrame(), ['end'] = timeline:GetEndFrame(),
        width = settings.timelineResolutionWidth, height = settings.timelineResolutionHeight,
        audio = {}, video = {}}
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
local function build_cuts(p, source, clips)
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
        local colors, placed = {'Orange', 'Teal'}, 0
        for number, clip in ipairs(clips) do
            local first, stop = source:GetStartFrame() + clip.first, source:GetStartFrame() + clip.last + 1
            for _, kind in ipairs({'video', 'audio'}) do
                for index = 1, source:GetTrackCount(kind) do
                    for _, item in ipairs(source:GetItemListInTrack(kind, index) or {}) do
                        local a, b = math.max(first, item:GetStart()), math.min(stop, item:GetEnd())
                        local media = call(item, 'GetMediaPoolItem')
                        if b > a and media and call(item, 'GetClipEnabled') ~= false then
                            -- endFrame is exclusive in Resolve 21.1.1.
                            local from = item:GetSourceStartFrame() + (a - item:GetStart())
                            local added = pool:AppendToTimeline({{mediaPoolItem = media, startFrame = from,
                                endFrame = from + (b - a), mediaType = kind == 'video' and 1 or 2,
                                trackIndex = index, recordFrame = a + offset}})
                            local piece = assert(added and added[1], 'Could not place a piece on ' .. kind .. ' track ' .. index)
                            call(piece, 'SetClipColor', colors[number % 2 + 1])
                            local mapping = kind == 'audio' and call(item, 'GetSourceAudioChannelMapping')
                            if mapping then call(piece, 'SetSourceAudioChannelMapping', mapping) end
                            placed = placed + 1
                        end
                    end
                end
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

    -- Wait while the helper scans and the user picks tracks and clips.
    local state
    local deadline = os.time() + 4 * 3600
    while os.time() < deadline do
        f:SetPrefs('Global.AudioSectionsHeartbeatV1', os.time())
        assert(helper_alive(), 'The Audio Sections helper stopped. Run the installer again, then retry.')
        state = load_state(id)
        if state and state.status == 'error' then error(state.message, 0) end
        if state and state.status == 'cancelled' then return end
        if state and state.status == 'ready' then break end
        bmd.wait(0.25)
    end
    assert(state and state.status == 'ready', 'Audio Sections timed out waiting for a choice.')
    local current = resolve:GetProjectManager():GetCurrentProject()
    assert(current and current:GetName() == p:GetName(), 'The open project changed. Return to it and try again.')
    assert(p:SetCurrentTimeline(timeline), 'Could not reopen timeline ' .. timeline:GetName())
    assert(fingerprint(timeline) == before, 'The timeline was edited after scanning. Run Audio Sections again.')
    assert(not p:IsRenderingInProgress(), 'Another render started. Try again when it finishes.')
    if state.action == 'cuts' then
        local name = build_cuts(p, timeline, state.clips)
        announce(id, 'cuts_ready', 100, 'Review "' .. name .. '", then run Audio Sections again on it.', name)
        return
    end

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
        local progress = 0
        for _, job_id in ipairs(jobs) do
            progress = progress + (tonumber(p:GetRenderJobStatus(job_id).CompletionPercentage) or 0)
        end
        announce(id, 'rendering', progress / #jobs, 'Rendering ' .. #jobs .. ' clips in Resolve')
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
