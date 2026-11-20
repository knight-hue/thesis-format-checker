[CmdletBinding()]
param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
# 脚本在 scripts/ 下，项目根是上一层（与 check_environment.ps1 同一写法）。
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Frontend = Join-Path $Root "frontend"
$Requirements = Join-Path $Root "requirements.txt"
$DevRequirements = Join-Path $Root "requirements-dev.txt"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$EnvironmentCheck = Join-Path $Root "scripts\check_environment.ps1"

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

function Invoke-CapturedCommand {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    try {
        $output = & $Command @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        return (($output | Out-String).Trim())
    } catch {
        return $null
    }
}

function Invoke-RequiredCommand {
    param(
        [string]$Command,
        [string[]]$Arguments,
        [string]$Description,
        [string]$WorkingDirectory = $Root
    )
    Write-Host "→ $Description" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Description 失败，退出码：$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function ConvertTo-Version {
    param([string]$Text)
    if ($Text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        return [version]::new([int]$Matches[1], [int]$Matches[2], $patch)
    }
    return $null
}

function Resolve-SystemPython {
    foreach ($candidateName in @("python", "py")) {
        $candidatePath = Get-CommandPath $candidateName
        if (-not $candidatePath) {
            continue
        }
        $versionText = Invoke-CapturedCommand $candidatePath @("--version")
        $version = ConvertTo-Version $versionText
        if ($version -and $version -ge [version]"3.10.0") {
            return [pscustomobject]@{
                Path    = $candidatePath
                Version = $versionText
            }
        }
    }
    throw "未找到 Python 3.10+。请安装后确认 python 或 py 命令可用。"
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
    $systemNpm = Get-CommandPath "npm"
    if ($systemNode -and $systemNpm) {
        return [pscustomobject]@{
            Node   = $systemNode
            Npm    = $systemNpm
            Home   = Split-Path -Parent $systemNode
            Source = "系统 PATH"
        }
    }
    throw "未找到配套的 node 和 npm。请安装 Node.js，或放入 .venv\nodejs。"
}

function Assert-NodeVersion {
    param($NodeTools)
    $versionText = Invoke-CapturedCommand $NodeTools.Node @("--version")
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

if ($SkipBackend -and $SkipFrontend) {
    throw "-SkipBackend 与 -SkipFrontend 不能同时使用：没有需要安装的组件。"
}

Write-Host "论文格式检查系统环境初始化" -ForegroundColor Cyan
Write-Host "项目目录：$Root"
Write-Host ""

if (-not $SkipBackend) {
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw "缺少后端依赖清单：$Requirements"
    }

    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        $systemPython = Resolve-SystemPython
        Write-Host "系统 Python：$($systemPython.Version)（$($systemPython.Path)）" -ForegroundColor Green
        Invoke-RequiredCommand $systemPython.Path @("-m", "venv", (Join-Path $Root ".venv")) "创建项目虚拟环境"
    } else {
        $venvVersion = Invoke-CapturedCommand $VenvPython @("--version")
        if (-not (ConvertTo-Version $venvVersion)) {
            throw "现有项目虚拟环境无法运行：$VenvPython。请手工移走损坏的 .venv 后重试。"
        }
        Write-Host "复用项目虚拟环境：$venvVersion" -ForegroundColor Green
    }

    Invoke-RequiredCommand $VenvPython @("-m", "pip", "install", "-r", $Requirements) "安装后端依赖"
    if (Test-Path -LiteralPath $DevRequirements -PathType Leaf) {
        # 测试与接口自测依赖（pytest / httpx）单独一份，缺失会导致测试无法收集。
        Invoke-RequiredCommand $VenvPython @("-m", "pip", "install", "-r", $DevRequirements) "安装测试依赖"
    }
    Invoke-RequiredCommand $VenvPython @("-m", "pip", "check") "检查后端依赖一致性"
    Invoke-RequiredCommand $VenvPython @("-c", "import backend.api") "验证后端入口可导入"
}

if (-not $SkipFrontend) {
    if (-not (Test-Path -LiteralPath (Join-Path $Frontend "package.json") -PathType Leaf)) {
        throw "缺少前端项目：$Frontend"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Frontend "package-lock.json") -PathType Leaf)) {
        throw "缺少 package-lock.json，无法执行可复现的 npm ci。"
    }

    $nodeTools = Resolve-NodeTools
    Assert-NodeVersion $nodeTools
    # npm.cmd 派生的 vite 进程通过 PATH 查找 node；便携版与系统版都显式加入，避免环境差异。
    $env:PATH = "$($nodeTools.Home);$env:PATH"
    Invoke-RequiredCommand $nodeTools.Npm @("ci") "安装前端依赖" $Frontend
    if (-not $SkipFrontendBuild) {
        Invoke-RequiredCommand $nodeTools.Npm @("run", "build") "验证前端生产构建" $Frontend
    } else {
        Write-Host "已跳过前端生产构建。" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "安装步骤已完成，正在执行环境检查……" -ForegroundColor Cyan
if (Test-Path -LiteralPath $EnvironmentCheck -PathType Leaf) {
    & $EnvironmentCheck -ProjectRoot $Root
    $checkExit = $LASTEXITCODE
    if ($checkExit -ne 0) {
        if ($SkipBackend -or $SkipFrontend) {
            Write-Host "环境检查仍有基础项未就绪；本次跳过了部分组件，请查看上方明细。" -ForegroundColor Yellow
        } else {
            throw "安装命令已执行，但环境检查仍未通过。请按上方处理建议修复后重试。"
        }
    }
} else {
    Write-Host "未找到环境检查脚本，已跳过最终检查：$EnvironmentCheck" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "环境初始化完成。下一步运行：.\start.ps1 -OpenBrowser" -ForegroundColor Green
