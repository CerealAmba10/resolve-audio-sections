-- Installed in Resolve's native Scripts menu. Uses only sandbox-supported APIs.
local HOME = __HOME__
local f = fusion or resolve:Fusion()
local function json_string(s)
    return '"' .. tostring(s):gsub('[%z\1-\31\\"]',function(c)
        if c=='\\' then return '\\\\' end
        if c=='"' then return '\\"' end
        return string.format('\\u%04x',string.byte(c))
    end) .. '"'
end
local function base64(data)
    local chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local output={}
    for i=1,#data,3 do
        local a,b,c=data:byte(i,i+2)
        local n=a*65536+(b or 0)*256+(c or 0)
        output[#output+1]=chars:sub(math.floor(n/262144)%64+1,math.floor(n/262144)%64+1)
        output[#output+1]=chars:sub(math.floor(n/4096)%64+1,math.floor(n/4096)%64+1)
        output[#output+1]=b and chars:sub(math.floor(n/64)%64+1,math.floor(n/64)%64+1) or '='
        output[#output+1]=c and chars:sub(n%64+1,n%64+1) or '='
    end
    return table.concat(output)
end
local function announce(id,status,progress,message)
    local text='{"id":'..json_string(id)..',"status":'..json_string(status)..
        ',"progress":'..tostring(progress or 0)..',"message":'..json_string(message or '')..'}'
    f:SetPrefs('Global.AudioSectionsResultV1',base64(text))
    f:SavePrefs()
end
local id=tostring(os.time())..'-'..tostring(math.random(100000,999999))
local function run()
local p=assert(resolve:GetProjectManager():GetCurrentProject(),'Open a project first.')
assert(not p:IsRenderingInProgress(),'Wait for the current render to finish.')
local heartbeat=loadfile(HOME..'/heartbeat.lua')
local alive=heartbeat and heartbeat()
assert(alive and os.time()-alive.time<15,'Run the Audio Sections installer once to start its background helper.')
local active=f:GetPrefs('Global.AudioSectionsActiveV1')
local active_time=tonumber((f:GetPrefs('Global.AudioSectionsHeartbeatV1'))) or 0
assert(not active or active=='' or os.time()-active_time>15,'Audio Sections is already sorting or rendering.')
local pool=p:GetMediaPool()
local media
local selected=pool:GetSelectedClips() or {}
if #selected==1 and (selected[1]:GetClipProperty()['File Path'] or '')~='' then media=selected[1] end
local timeline=p:GetCurrentTimeline()
if not media and timeline then
    local item=timeline:GetCurrentVideoItem()
    media=item and item:GetMediaPoolItem()
end
assert(media,'Select the recording in the Media Pool, or open its timeline.')
local source=media:GetClipProperty()['File Path']
assert(source and source~='','Select an original media file, rather than a timeline.')
local reference=timeline and timeline:GetSettings() or p:GetSettings()
local normalized=source:gsub('\\','/')
local stem=normalized:match('([^/]+)$'):gsub('%.[^.]+$','')
local escaped=stem:gsub('([^%w])','%%%1')
local first=1
for i=1,p:GetTimelineCount() do
    local number=p:GetTimelineByIndex(i):GetName():match('^'..escaped..' (%d+)T?$')
    if number then first=math.max(first,tonumber(number)+1) end
end
local project_id=p:GetUniqueId()
local request='{"id":'..json_string(id)..',"source":'..json_string(source)..
    ',"first_number":'..first..'}'
f:SetPrefs('Global.AudioSectionsActiveV1',id)
f:SetPrefs('Global.AudioSectionsHeartbeatV1',os.time())
f:SetPrefs('Global.AudioSectionsRequestV1',base64(request))
f:SavePrefs()
local jobs
local ok,problem=pcall(function()
    local deadline=os.time()+14400
    local state
    while os.time()<deadline do
        f:SetPrefs('Global.AudioSectionsHeartbeatV1',os.time())
        local beat=loadfile(HOME..'/heartbeat.lua')
        local healthy,value=pcall(beat or function() return nil end)
        assert(healthy and value and os.time()-value.time<15,
            'The background scanner stopped. Run the installer again, then retry.')
        local chunk=loadfile(HOME..'/state.lua')
        if chunk then
            local loaded,value=pcall(chunk)
            if loaded and type(value)=='table' and value.id==id then
                state=value
                if value.status=='error' then error(value.message,0) end
                if value.status=='cancelled' then announce(id,'error',0,'Stopped.'); return end
                if value.status=='ready' then break end
            end
        end
        bmd.wait(0.25)
    end
    assert(state and state.status=='ready','Audio scanner did not finish. Run the Audio Sections installer again.')
    local current=resolve:GetProjectManager():GetCurrentProject()
    assert(current and current:GetUniqueId()==project_id,
        'The open project changed while sorting. Return to the original project and try again.')
    assert(not p:IsRenderingInProgress(),'Another render started while sorting. Try again when it finishes.')
    AudioSectionsRequireExistingSource=true
    AudioSectionsReferenceSettings=reference
    local loaded,result=pcall(dofile,HOME..'/batches/'..id..'/queue.lua')
    AudioSectionsRequireExistingSource=nil
    AudioSectionsReferenceSettings=nil
    if not loaded then error(result,0) end
    jobs=assert(result and result.jobIds,'No render jobs were created.')
    assert(resolve:GetProjectManager():SaveProject(),'Could not save the project.')
    assert(p:StartRendering(jobs),'Resolve could not start rendering.')
    announce(id,'rendering',0,'Rendering '..#jobs..' clips in Resolve')
    while p:IsRenderingInProgress() do
        f:SetPrefs('Global.AudioSectionsHeartbeatV1',os.time())
        local progress=0
        for _,job_id in ipairs(jobs) do
            local status=p:GetRenderJobStatus(job_id)
            progress=progress+(tonumber(status.CompletionPercentage) or 0)
        end
        announce(id,'rendering',progress/#jobs,'Rendering '..#jobs..' clips in Resolve')
        local chunk=loadfile(HOME..'/state.lua')
        if chunk then
            local loaded,value=pcall(chunk)
            if loaded and value.id==id and value.cancel then p:StopRendering(); error('Render stopped.',0) end
        end
        bmd.wait(1)
    end
    for _,job_id in ipairs(jobs) do
        local status=p:GetRenderJobStatus(job_id)
        assert(status.JobStatus=='Complete',status.Error or 'A clip did not finish rendering.')
    end
    announce(id,'complete',100,#jobs..' clips saved. Your project is ready.')
end)
AudioSectionsRequireExistingSource=nil
AudioSectionsReferenceSettings=nil
f:SetPrefs('Global.AudioSectionsActiveV1','')
f:SavePrefs()
if not ok then announce(id,'error',0,tostring(problem)); print('Audio Sections: '..tostring(problem)) end
end
local ok,problem=pcall(run)
if not ok then announce(id,'error',0,tostring(problem)); print('Audio Sections: '..tostring(problem)) end
