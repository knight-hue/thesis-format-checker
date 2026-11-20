[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$Build,
    [switch]$NoFrontend,
    [switch]$NoBackend,
    [switch]$RequireAI,
    [switch]$AllowCloudDocs,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$NodeHome = $null
$Npm = $null
$Frontend = Join-Path $Root "frontend"

function Assert-Path($Path, $Message) {
    if (-not (Test-Path $Path)) {
        throw $Message
    }
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

function ConvertTo-Version {
    param([string]$Text)
    if ($Text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        return [version]::new([int]$Matches[1], [int]$Matches[2], $patch)
    }
    return $null
}

function Resolve-NodeTools {
    $portableHome = Join-Path $Root ".venv\nodejs"
    $portableNode = Join-Path $portableHome "node.exe"
    $portableNpm = Join-Path $portableHome "npm.cmd"
    if ((Test-Path -LiteralPath $portableNode -PathType Leaf) -and
        (Test-Path -LiteralPath $portableNpm -PathType Leaf)) {
        return [pscustomobject]@{
            Node   = $portableNode
            Npm    = $portableNpm
            Home   = $portableHome
            Source = "项目便携版"
        }
    }

    $systemNode = Get-CommandPath "node"
    if ($systemNode) {
        # Windows 的 Get-Command npm 常返回 npm.ps1，不能直接交给 Start-Process；
        # 优先使用 node.exe 同目录下可执行的 npm.cmd。
        $systemHome = Split-Path -Parent $systemNode
        $systemNpmCmd = Join-Path $systemHome "npm.cmd"
        $systemNpm = if (Test-Path -LiteralPath $systemNpmCmd -PathType Leaf) {
            $systemNpmCmd
        } else {
            Get-CommandPath "npm"
        }
        if ($systemNpm) {
            return [pscustomobject]@{
                Node   = $systemNode
                Npm    = $systemNpm
                Home   = $systemHome
                Source = "系统 PATH"
            }
        }
    }
    throw "未找到配套的 node 和 npm。请先运行 .\scripts\setup.ps1，或安装 Node.js。"
}

function Assert-NodeVersion($NodeTools) {
    $versionText = (& $NodeTools.Node --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Node.js 无法执行：$($NodeTools.Node)"
    }
    $version = ConvertTo-Version $versionText
    $supported = $version -and (
        (($version.Major -eq 20) -and $version -ge [version]"20.19.0") -or
        (($version.Major -eq 22) -and $version -ge [version]"22.12.0") -or
        ($version.Major -gt 22)
    )
    if (-not $supported) {
        throw "Vite 8 不支持当前 Node.js 版本：$versionText。请安装 Node 20.19+ 或 22.12+。"
    }
    Write-Host "Node.js：$versionText（$($NodeTools.Source)）" -ForegroundColor Green
}

function Get-ApiKeySource {
    if ($env:LLM_API_KEY -and $env:LLM_API_KEY.Trim()) {
        return "环境变量 LLM_API_KEY"
    }
    if ($env:THESIS_LLM_API_KEY -and $env:THESIS_LLM_API_KEY.Trim()) {
        return "环境变量 THESIS_LLM_API_KEY"
    }
    $settingsPath = Join-Path $Root "local_settings.py"
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $match = Select-String -LiteralPath $settingsPath `
            -Pattern '^\s*LLM_API_KEY\s*=\s*["'']([^"'']+)["'']' `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($match -and $match.Matches[0].Groups[1].Value.Trim()) {
            return "local_settings.py"
        }
    }
    return $null
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process |
        Where-Object { $_.ParentProcessId -eq $ProcessId } |
        Select-Object -ExpandProperty ProcessId
    foreach ($child in $children) {
        Stop-ProcessTree $child
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

# 命令行里带项目根目录的进程才算本项目拉起的（uvicorn / vite 的路径都落在项目内）。
# 这里用 IndexOf 而不是 Contains(string, StringComparison)：后者在 PowerShell 5.1 的 .NET Framework 上不存在。
function Test-ProjectProcess($Process) {
    return [bool]($Process.CommandLine -and $Process.CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Get-PortOwner([int]$Port) {
    $procIds = @()
    try {
        $procIds = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $procIds = @(netstat -ano | Select-String ":$Port\s.*LISTENING\s+(\d+)\s*$" |
            ForEach-Object { [int]$_.Matches[0].Groups[1].Value } | Sort-Object -Unique)
    }
    $owners = @()
    foreach ($procId in $procIds) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if ($proc) {
            $owners += [pscustomobject]@{
                ProcessId   = $proc.ProcessId
                Name        = $proc.Name
                CommandLine = $proc.CommandLine
            }
        }
    }
    return $owners
}

# 启动前预检端口：被本项目遗留进程占用就自动清理，被别的程序占用就给出处置建议并中止。
# 否则新进程会绑定失败，uvicorn 以 STARTUP_FAILURE（退出码 3）退出，只留下一句看不出原因的报错。
function Resolve-PortConflict([int]$Port, [string]$Service, [string]$SwitchName) {
    $owners = @(Get-PortOwner $Port)
    if ($owners.Count -eq 0) {
        return
    }

    $foreign = @($owners | Where-Object { -not (Test-ProjectProcess $_) })
    if ($foreign.Count -gt 0) {
        Write-Host "端口 $Port 已被占用，无法启动${Service}：" -ForegroundColor Yellow
        foreach ($owner in $foreign) {
            Write-Host ("  PID {0}  {1}" -f $owner.ProcessId, $owner.Name)
            if ($owner.CommandLine) {
                Write-Host ("      命令行：{0}" -f $owner.CommandLine)
            }
        }
        Write-Host ("  处置建议：结束上述进程后重试（Stop-Process -Id {0} -Force），或用 -{1} 指定其它端口。" -f $foreign[0].ProcessId, $SwitchName)
        throw "端口 $Port 被其它进程占用，${Service}启动失败。"
    }

    foreach ($owner in $owners) {
        Write-Host "端口 $Port 被本项目遗留的${Service}占用（PID $($owner.ProcessId)），正在清理……" -ForegroundColor Yellow
        # venv 的 python.exe 启动器、npm 包装进程都只是等子进程结束的中间层，杀监听进程即可，它们会自行退出
        Stop-ProcessTree $owner.ProcessId
    }

    for ($i = 0; $i -lt 20; $i++) {
        if (@(Get-PortOwner $Port).Count -eq 0) {
            Write-Host "端口 $Port 已释放。" -ForegroundColor Yellow
            return
        }
        Start-Sleep -Milliseconds 250
    }
    $left = @(Get-PortOwner $Port) | ForEach-Object { "$($_.ProcessId)（$($_.Name)）" }
    throw "端口 $Port 清理后仍被占用：$($left -join '、')"
}

# 后端不在启动时加载向量模型与生成模型：/api/health 很快就能返回 200，AI 能力在后台预热。
# 这里只等到「服务可用」，不等 AI 就绪，所以页面能立刻打开。
function Wait-BackendReady([int]$Port, [int]$TimeoutSeconds = 60) {
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        try {
            $response = Invoke-WebRequest "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host ("后端已就绪（{0:N1}s）：http://127.0.0.1:{1}" -f $watch.Elapsed.TotalSeconds, $Port) -ForegroundColor Green
                return $true
            }
        } catch {}
        Start-Sleep -Milliseconds 300
    }
    Write-Warning "后端在 $TimeoutSeconds 秒内未就绪，请查看上方日志。"
    return $false
}

# 读取健康检查：AI 能力分两阶段预热，分别回报状态；失败时返回 $null 供调用方跳过本轮。
function Get-AiState([int]$Port) {
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2
        $health = $response.Content | ConvertFrom-Json
        return @{
            LlmReady  = [bool]$health.llm_initialized
            LlmFailed = ($health.llm_status -eq "failed")
            LlmError  = $health.llm_error
            RagReady  = [bool]$health.rag_initialized
            RagFailed = ($health.rag_status -eq "failed")
            RagError  = $health.rag_error
        }
    } catch {
        return $null
    }
}

if ($NoBackend -and $NoFrontend) {
    throw "-NoBackend 与 -NoFrontend 不能同时使用：没有需要启动的服务。"
}
if ($RequireAI -and $NoBackend) {
    throw "-RequireAI 需要同时启动后端，不能与 -NoBackend 一起使用。"
}
if ($AllowCloudDocs -and $NoBackend) {
    throw "-AllowCloudDocs 只影响后端，不能与 -NoBackend 一起使用。"
}
if ($Build -and $NoFrontend) {
    throw "-Build 需要启动前端，不能与 -NoFrontend 一起使用。"
}

if (-not $NoBackend) {
    Assert-Path $Python "未找到项目 Python 环境：$Python。请先运行 .\scripts\setup.ps1。"
}

# 端口预检放在最前，避免起了半天才发现端口被占用
if (-not $NoBackend) {
    Resolve-PortConflict -Port $BackendPort -Service "后端" -SwitchName "BackendPort"
}
if (-not $NoFrontend) {
    Resolve-PortConflict -Port $FrontendPort -Service "前端" -SwitchName "FrontendPort"
}
if (-not $NoBackend) {
    $keySource = Get-ApiKeySource
    if ($keySource) {
        Write-Host "在线 AI：已配置（来源：$keySource；不显示密钥内容）" -ForegroundColor Green
    } elseif ($RequireAI) {
        throw "未配置 API Key，无法满足 -RequireAI。请设置 LLM_API_KEY、THESIS_LLM_API_KEY 或 local_settings.py。"
    } else {
        Write-Host "在线 AI：未配置，按基础模式启动。规则检测与确定性写回仍可使用。" -ForegroundColor Yellow
    }
    if ($AllowCloudDocs) {
        # 演示用：允许语义判定把论文正文发往外部模型端点。
        # 子进程会继承本会话环境变量；不写入任何配置文件。
        $env:THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS = "1"
        Write-Host "演示模式：论文内容已放行发往外部模型端点（语义判定可用）。演示结束请去掉 -AllowCloudDocs。" -ForegroundColor Yellow
    }
}

if (-not $NoFrontend) {
    Assert-Path (Join-Path $Frontend "package.json") "未找到前端项目：$Frontend"
    $nodeTools = Resolve-NodeTools
    Assert-NodeVersion $nodeTools
    $NodeHome = $nodeTools.Home
    $Npm = $nodeTools.Npm
    if (-not $Build) {
        Assert-Path (Join-Path $Frontend "node_modules\.bin\vite.cmd") `
            "尚未安装前端依赖。请先运行 .\scripts\setup.ps1，或本次使用 -Build。"
    }
    # npm.cmd 派生的 vite 子进程靠 PATH 找 node，便携版和系统版都显式加入。
    $env:PATH = "$NodeHome;$env:PATH"
}

# 注意：PowerShell 变量名不区分大小写，这里的进程句柄绝不能叫 $backend/$frontend，
# 否则会把上面保存的 $BackendPort/$Frontend 路径覆盖成 $null。
$backendProc = $null
$frontendProc = $null
try {
    if (-not $NoBackend) {
        $backendProc = Start-Process `
            -FilePath $Python `
            -ArgumentList "-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", "$BackendPort" `
            -WorkingDirectory $Root `
            -PassThru
        Write-Host "后端进程已启动：PID $($backendProc.Id)（等待就绪…）"
        if (-not (Wait-BackendReady -Port $BackendPort)) {
            throw "后端未在规定时间内就绪，已停止本次启动。"
        }
        if ($keySource) {
            Write-Host "  生成模型门面与知识库正在依次预热：门面秒级就绪，知识库慢一些。" -ForegroundColor DarkGray
            Write-Host "  就绪前上传检测会跳过需要 AI 的语义条款，AI 问答也会提示初始化中；就绪后本窗口会提示。" -ForegroundColor DarkGray
        } else {
            Write-Host "  当前为基础模式：AI 后台初始化预计失败，但不会影响规则检测与确定性写回。" -ForegroundColor DarkGray
        }
    }

    if (-not $NoFrontend) {
        $env:VITE_PROXY_TARGET = "http://127.0.0.1:$BackendPort"
        if ($Build) {
            Push-Location $Frontend
            try {
                & $Npm ci
                if ($LASTEXITCODE -ne 0) {
                    throw "前端依赖安装失败，退出码：$LASTEXITCODE"
                }
                & $Npm run build
                if ($LASTEXITCODE -ne 0) {
                    throw "前端构建失败，退出码：$LASTEXITCODE"
                }
            }
            finally {
                Pop-Location
            }
        }

        $frontendProc = Start-Process `
            -FilePath $Npm `
            -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort" `
            -WorkingDirectory $Frontend `
            -PassThru
        Write-Host "前端已启动：PID $($frontendProc.Id)，http://127.0.0.1:$FrontendPort"
    }

    if ($OpenBrowser -and -not $NoFrontend) {
        Start-Process "http://127.0.0.1:$FrontendPort"
    }

    Write-Host ""
    Write-Host "项目已启动。关闭当前窗口或按 Ctrl+C 将停止由本脚本启动的进程。"

    # AI 能力在后台线程里两阶段预热，就绪后在这里补状态，避免用户猜什么时候能用。
    $aiWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $llmState = "initializing"
    $ragState = "initializing"
    while ($true) {
        Start-Sleep -Seconds 1
        if ($backendProc -and $backendProc.HasExited) {
            $code = $backendProc.ExitCode
            $hint = ""
            if ($code -eq 3) {
                $hint = "（uvicorn 启动失败：常见原因是端口被占用或应用导入出错，可用 -BackendPort 换个端口重试）"
            }
            throw "后端进程已退出，退出码：$code$hint"
        }
        if ($frontendProc -and $frontendProc.HasExited) {
            throw "前端进程已退出，退出码：$($frontendProc.ExitCode)"
        }
        if ($backendProc -and ($llmState -eq "initializing" -or $ragState -eq "initializing")) {
            $state = Get-AiState -Port $BackendPort
            if ($state) {
                if ($llmState -eq "initializing" -and $state.LlmReady) {
                    $llmState = "ready"
                    Write-Host ("生成模型服务已就绪（启动后 {0:N1}s）：语义检查、段落改写与 AI 问答可用。" -f $aiWatch.Elapsed.TotalSeconds) -ForegroundColor Green
                } elseif ($llmState -eq "initializing" -and $state.LlmFailed) {
                    # 门面失败时后端不再预热知识库，这里一并结束等待。
                    $llmState = "failed"
                    $ragState = "skipped"
                    Write-Host "生成模型服务初始化失败，AI 问答与语义检查不可用：$($state.LlmError)" -ForegroundColor Yellow
                    Write-Host "  规则检测与格式写回仍可正常使用；详见后端日志。" -ForegroundColor DarkGray
                }
                if ($ragState -eq "initializing" -and $state.RagReady) {
                    $ragState = "ready"
                    Write-Host ("知识库已就绪（启动后 {0:N1}s）：AI 问答可用。" -f $aiWatch.Elapsed.TotalSeconds) -ForegroundColor Green
                } elseif ($ragState -eq "initializing" -and $state.RagFailed) {
                    $ragState = "failed"
                    Write-Host "知识库初始化失败，AI 问答不可用：$($state.RagError)" -ForegroundColor Yellow
                    Write-Host "  规则检测、语义检查与格式写回仍可正常使用；详见后端日志。" -ForegroundColor DarkGray
                }
            }
        }
    }
}
finally {
    foreach ($process in @($frontendProc, $backendProc)) {
        if ($process -and -not $process.HasExited) {
            Stop-ProcessTree $process.Id
        }
    }
}
