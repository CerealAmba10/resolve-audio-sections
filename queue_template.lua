-- Runs INSIDE Resolve, using its internal Lua Console. No external connection.
local function require_ok(value, message)
    if not value then error(message, 0) end
    return value
end
local function exists(path)
    if bmd and bmd.fileexists then return bmd.fileexists(path) end
    if not (io and io.open) then return false end
    local f = io.open(path, 'rb')
    if f then f:close(); return true end
    return false
end
local function frame_rate(value)
    return tonumber(tostring(value):match('[%d.]+'))
end
local r = resolve or (bmd and bmd.scriptapp('Resolve'))
require_ok(r, 'Run this script in the Lua tab of Workspace > Console in Resolve.')
local project = require_ok(r:GetProjectManager():GetCurrentProject(), 'Open a project first.')
AudioSectionsQueued = AudioSectionsQueued or {}
local batch_key = project:GetName() .. ':' .. RECEIPT
require_ok(not project:IsRenderingInProgress(), 'Wait for the current render to finish.')
require_ok(not AudioSectionsQueued[batch_key] and not exists(RECEIPT),
    'This batch was already queued. Generate a new batch to queue again.')
require_ok(#JOBS > 0, 'This batch contains no clips.')
local old_timeline = project:GetCurrentTimeline()
local old_page = r:GetCurrentPage()
local pool = project:GetMediaPool()
local old_folder = pool:GetCurrentFolder()
local base = old_timeline and old_timeline:GetSettings() or project:GetSettings()
local fps = require_ok(frame_rate(base.timelineFrameRate), 'Cannot determine timeline frame rate.')
local width = require_ok(tonumber(base.timelineResolutionWidth), 'Cannot determine timeline width.')
local height = require_ok(tonumber(base.timelineResolutionHeight), 'Cannot determine timeline height.')
local names = {}
for i = 1, project:GetTimelineCount() do
    names[project:GetTimelineByIndex(i):GetName()] = true
end
for _, job in ipairs(project:GetRenderJobList() or {}) do
    if job.OutputFilename then
        names[job.OutputFilename:gsub('%.mp4$', '')] = true
    end
end
for _, job in ipairs(JOBS) do
    require_ok(not names[job.name], 'A timeline or queued file already uses: ' .. job.name)
    require_ok(not exists(OUTPUT .. '/' .. job.name .. '.mp4'), 'Output already exists: ' .. job.name)
    names[job.name] = true
end
if not project:LoadRenderPreset(PRESET) then
    require_ok(PRESET_FILE and exists(PRESET_FILE) and r:ImportRenderPreset(PRESET_FILE)
        and project:LoadRenderPreset(PRESET),
        'Save a Deliver preset named "' .. PRESET .. '" first. See README.')
end
local format = project:GetCurrentRenderFormatAndCodec()
require_ok(format and format.format == 'mp4' and
    tostring(format.codec):gsub('[^%w]', ''):upper() == 'H264',
    'The saved preset must use MP4, H.264 and Native encoder. Current codec: ' .. tostring(format and format.codec))

local created, queued = {}, {}
local function source_in_project(folder)
    local wanted = SOURCE:gsub('\\', '/'):lower()
    for _, clip in ipairs(folder:GetClipList() or {}) do
        local properties = clip:GetClipProperty()
        if tostring(properties['File Path']):gsub('\\', '/'):lower() == wanted then return clip end
    end
    for _, subfolder in ipairs(folder:GetSubFolderList() or {}) do
        local found = source_in_project(subfolder)
        if found then return found end
    end
end
local function restore()
    if old_timeline then project:SetCurrentTimeline(old_timeline) end
    if old_folder then pool:SetCurrentFolder(old_folder) end
    if old_page then r:OpenPage(old_page) end
end
local success, problem = pcall(function()
    -- Check receipt writability before making timelines or jobs.
    if io and io.open then
        local check = require_ok(io.open(RECEIPT, 'w'), 'Cannot write batch receipt. Use a writable batch folder.')
        check:close()
        require_ok(os.remove(RECEIPT), 'Cannot clear the temporary receipt.')
    end
    local media = source_in_project(pool:GetRootFolder())
    if not media then
        require_ok(not AudioSectionsRequireExistingSource,
            'The analyzed source is not in this project. Select its project and analyze again.')
        local imported = require_ok(pool:ImportMedia({{FilePath=SOURCE}}), 'Source import failed: ' .. SOURCE)
        media = require_ok(imported[1], 'Resolve could not import this media file.')
    end
    local properties = media:GetClipProperty()
    local imported_fps = require_ok(frame_rate(properties.FPS), 'Cannot read source frame rate from Resolve.')
    require_ok(math.abs(imported_fps - SOURCE_FPS) < 0.01,
        'Resolve source frame rate differs from scan. Reset Clip Attributes or normalize the source.')
    local bin
    for _, folder in ipairs(pool:GetRootFolder():GetSubFolderList() or {}) do
        if folder:GetName() == 'Audio Sections' then bin = folder end
    end
    bin = bin or require_ok(pool:AddSubFolder(pool:GetRootFolder(), 'Audio Sections'),
        'Cannot create the Audio Sections project bin.')
    require_ok(pool:SetCurrentFolder(bin), 'Cannot select the Audio Sections project bin.')
    for _, job in ipairs(JOBS) do
        local timeline = require_ok(pool:CreateEmptyTimeline(job.name), 'Cannot create timeline: ' .. job.name)
        table.insert(created, timeline)
        require_ok(project:SetCurrentTimeline(timeline), 'Cannot activate timeline: ' .. job.name)
        require_ok(timeline:SetSettings({useCustomSettings='1', timelineFrameRate=base.timelineFrameRate,
            timelineResolutionWidth=tostring(width), timelineResolutionHeight=tostring(height)}),
            'Cannot match the reference timeline settings.')
        local appended = require_ok(pool:AppendToTimeline({{mediaPoolItem=media,
            startFrame=job.first, endFrame=job.last + 1}}), 'Cannot trim source: ' .. job.name)
        require_ok(#appended > 0, 'No media was appended: ' .. job.name)
        local count = timeline:GetEndFrame() - timeline:GetStartFrame()
        local expected = math.floor((job.last - job.first + 1) / SOURCE_FPS * fps + 0.5)
        local tolerance = math.abs(fps - SOURCE_FPS) < 0.01 and 0 or 1
        require_ok(math.abs(count - expected) <= tolerance,
            'Unexpected clip duration for ' .. job.name .. '. No jobs from this batch will be kept.')
        local fade_frames = math.floor(job.fade * fps + 0.5)
        for _, track_type in ipairs({'video', 'audio'}) do
            local items_found = 0
            for track = 1, timeline:GetTrackCount(track_type) do
                for _, item in ipairs(timeline:GetItemListInTrack(track_type, track) or {}) do
                    items_found = items_found + 1
                    require_ok(item:SetFades({FadeIn=0, FadeOut=fade_frames}),
                        'Cannot set ' .. track_type .. ' fade for ' .. job.name .. '. Resolve 21.1.1 is required.')
                    local fades = item:GetFades()
                    require_ok(fades and fades.FadeOut == fade_frames and fades.FadeIn == 0,
                        'Resolve did not retain the requested fade for ' .. job.name)
                end
            end
            require_ok(items_found > 0, 'Missing ' .. track_type .. ' in ' .. job.name)
        end
        require_ok(project:LoadRenderPreset(PRESET), 'Cannot load render preset.')
        require_ok(project:SetCurrentRenderMode(1), 'Cannot select single-clip rendering.')
        require_ok(project:SetRenderSettings({SelectAllFrames=true, TargetDir=OUTPUT, CustomName=job.name,
            -- Native H.264 rejects the documented VideoQuality string in 21.1.1.
            -- High is preserved by loading the saved preset immediately above.
            UseUniqueFilenames=false, ExportVideo=true, ExportAudio=true,
            NetworkOptimization=false, FormatWidth=width, FormatHeight=height, FrameRate=fps}),
            'Cannot apply render settings for ' .. job.name)
        local id = require_ok(project:AddRenderJob(), 'Cannot queue ' .. job.name)
        table.insert(queued, id)
        print('Queued: ' .. job.name)
    end
    if io and io.open then
        local receipt = require_ok(io.open(RECEIPT, 'w'), 'Cannot save batch receipt.')
        receipt:write('Queued in project: ', project:GetName(), '\n')
        for _, id in ipairs(queued) do receipt:write(id, '\n') end
        receipt:close()
    end
    AudioSectionsQueued[batch_key] = true
end)
if not success then
    local cleanup_ok = true
    for _, id in ipairs(queued) do
        if not project:DeleteRenderJob(id) then cleanup_ok = false end
    end
    -- Keep timelines if job removal failed, so retained jobs still have media.
    if cleanup_ok and #created > 0 then cleanup_ok = pool:DeleteTimelines(created) end
    if io and io.open and os.remove then os.remove(RECEIPT) end
    restore()
    if not cleanup_ok then
        error(tostring(problem) .. '\nRollback was incomplete. Review this batch in the queue and Media Pool.', 0)
    end
    error(tostring(problem) .. '\nNew jobs and timelines were rolled back; imported source media may remain.', 0)
end
restore()
r:OpenPage('deliver')
print('Audio Sections: ' .. #queued .. ' jobs queued. Review them, save the project, then click Render All.')
