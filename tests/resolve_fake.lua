-- Stateful test double: no real Resolve project is accessed.
state = {jobs={}, timelines={}, fail_at=0, fade_failure=false, settings={}, imports=0}
local base = {timelineFrameRate=24, timelineResolutionWidth='1920', timelineResolutionHeight='1080'}
local function copy(t)
    local result = {}; for k,v in pairs(t) do result[k]=v end; return result
end
local function item()
    return {fades={FadeIn=0,FadeOut=0}, SetFades=function(self, f)
        if state.fade_failure then return false end
        self.fades=copy(f); return true
    end, GetFades=function(self) return self.fades end}
end
local function timeline(name)
    return {name=name, count=0, video=item(), audio=item(),
        GetName=function(self) return self.name end,
        GetSettings=function() return base end,
        SetSettings=function() return true end,
        GetStartFrame=function() return 86400 end,
        GetEndFrame=function(self) return 86400+self.count end,
        GetTrackCount=function() return 1 end,
        GetItemListInTrack=function(self, kind) return {self[kind]} end}
end
state.original = timeline('Original edit')
state.timelines[1] = state.original
state.current = state.original
state.jobs[1] = {id='existing', OutputFilename='unrelated.mp4'}
local pool = {}
function pool:GetRootFolder()
    return {GetClipList=function()
        if state.existing_source then
            return {{GetClipProperty=function() return {FPS='24',['File Path']=state.existing_source} end}}
        end
        return {}
    end, GetSubFolderList=function() return {} end}
end
function pool:AddSubFolder(root,name) state.bin=name; return name end
function pool:GetCurrentFolder() return 'original folder' end
function pool:SetCurrentFolder() return true end
function pool:ImportMedia() state.imports=state.imports+1; return {{GetClipProperty=function() return {FPS='24'} end}} end
function pool:CreateEmptyTimeline(name)
    local t=timeline(name); table.insert(state.timelines,t); return t
end
function pool:AppendToTimeline(clips)
    state.current.count=clips[1].endFrame-clips[1].startFrame
    return {state.current.video,state.current.audio}
end
function pool:DeleteTimelines(timelines)
    for _,t in ipairs(timelines) do
        for i=#state.timelines,1,-1 do
            if state.timelines[i]==t then table.remove(state.timelines,i) end
        end
    end
    return true
end
local project = {}
function project:GetName() return 'Test project' end
function project:IsRenderingInProgress() return false end
function project:GetCurrentTimeline() return state.current end
function project:SetCurrentTimeline(t) state.current=t; return true end
function project:GetMediaPool() return pool end
function project:GetSettings() return base end
function project:GetTimelineCount() return #state.timelines end
function project:GetTimelineByIndex(i) return state.timelines[i] end
function project:GetRenderJobList() return state.jobs end
function project:LoadRenderPreset(name) state.quality='High'; return name=='Audio Sections High' end
function project:GetCurrentRenderFormatAndCodec() return {format='mp4',codec='H264'} end
function project:SetCurrentRenderMode(mode) return mode==1 end
function project:SetRenderSettings(settings)
    if settings.VideoQuality then return false end -- Actual 21.1.1 Native H.264 behavior.
    state.settings=copy(settings); return true
end
function project:AddRenderJob()
    if state.fail_at==#state.jobs then return nil end
    local job={id='new-'..#state.jobs, timeline=state.current, quality=state.quality, settings=copy(state.settings),
        OutputFilename=state.settings.CustomName..'.mp4'}
    table.insert(state.jobs,job); return job.id
end
function project:DeleteRenderJob(id)
    for i,j in ipairs(state.jobs) do if j.id==id then table.remove(state.jobs,i); return true end end
    return false
end
resolve = {GetProjectManager=function() return {GetCurrentProject=function() return project end} end,
    GetCurrentPage=function() return 'edit' end, OpenPage=function() return true end}
print = function() end
