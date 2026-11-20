[CmdletBinding()]
param(
    [switch]$Json,
    [switch]$SkipDependencyCheck,
    [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
$defaultRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Root = if ($ProjectRoot) {
    (Resolve-Path -LiteralPath $ProjectRoot).Path
} else {
    $defaultRoot
}
$Results = [System.Collections.Generic.List[object]]::new()

function Add-CheckResult {
    param(
        [ValidateSet("pass", "warning", "error")]
        [string]$Status,
        [string]$Name,
        [string]$Message,
        [string]$Resolution = ""
    )
    $Results.Add([pscustomobject]@{
        status     = $Status
        name       = $Name
        message    = $Message
        resolution = $Resolution
    })
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

function Invoke-VersionCommand {
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

function ConvertTo-Version {
    param([string]$Text)
    if ($Text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        return [version]::new([int]$Matches[1], [int]$Matches[2], $patch)
    }
    return $null
}

function Get-ConfigString {
    param(
        [string]$Name,
        [string]$Default
    )
    $configPath = Join-Path $Root "config.py"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $Default
    }
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*["'']([^"'']+)["'']'
    $match = Select-String -LiteralPath $configPath -Pattern $pattern -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($match) {
        return $match.Matches[0].Groups[1].Value
    }
    return $Default
}

function Test-LocalApiKey {
    $settingsPath = Join-Path $Root "local_settings.py"
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        return $false
    }
    $match = Select-String -LiteralPath $settingsPath `
        -Pattern '^\s*LLM_API_KEY\s*=\s*["'']([^"'']+)["'']' `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]($match -and $match.Matches[0].Groups[1].Value.Trim())
}

# 仓库骨架：这些文件缺失意味着当前目录不是可运行的完整项目。
$requiredFiles = @(
    "backend\api.py",
    "backend\config.py",
    "requirements.txt",
    "frontend\package.json",
    "frontend\package-lock.json",
    "config\colleges.json"
)
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $Root $_) -PathType Leaf)
})
if ($missingFiles.Count -eq 0) {
    Add-CheckResult pass "项目文件" "项目骨架完整"
} else {
    Add-CheckResult error "项目文件" ("缺少：" + ($missingFiles -join "、")) "请重新拉取完整仓库"
}

# Python：系统解释器用于创建环境，项目解释器才代表后端已准备好。
# Windows Store 的 python 别名可能存在但无法执行，因此失败时继续尝试 py 启动器。
$systemPython = $null
$pythonVersionText = $null
$pythonVersion = $null
foreach ($candidateName in @("python", "py")) {
    $candidatePath = Get-CommandPath $candidateName
    if (-not $candidatePath) {
        continue
    }
    $candidateVersionText = Invoke-VersionCommand $candidatePath @("--version")
    $candidateVersion = ConvertTo-Version $candidateVersionText
    if ($candidateVersion) {
        $systemPython = $candidatePath
        $pythonVersionText = $candidateVersionText
        $pythonVersion = $candidateVersion
        break
    }
}
if ($systemPython -and $pythonVersion -ge [version]"3.10.0") {
    Add-CheckResult pass "系统 Python" "$pythonVersionText（$systemPython）"
} elseif ($systemPython) {
    Add-CheckResult error "系统 Python" "版本不满足 Python 3.10+：$pythonVersionText" "安装 Python 3.10 或更高版本"
} else {
    Add-CheckResult error "系统 Python" "未找到 python 或 py 命令" "安装 Python 3.10+ 并加入 PATH"
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvVersionText = Invoke-VersionCommand $venvPython @("--version")
    $venvVersion = ConvertTo-Version $venvVersionText
    if ($venvVersion -and $venvVersion -ge [version]"3.10.0") {
        Add-CheckResult pass "项目 Python" "$venvVersionText（.venv）"
        if (-not $SkipDependencyCheck) {
            $pipOutput = Invoke-VersionCommand $venvPython @("-m", "pip", "check")
            if ($null -eq $pipOutput) {
                Add-CheckResult error "Python 依赖" "pip check 执行失败" "重新安装 requirements.txt"
            } elseif ($pipOutput -match 'No broken requirements found') {
                $importOutput = Invoke-VersionCommand $venvPython @("-c", "import backend.api")
                if ($null -eq $importOutput) {
                    Add-CheckResult error "Python 依赖" "pip check 通过，但项目入口无法导入" "重新安装 requirements.txt 后执行 python -c `"import backend.api`" 定位错误"
                } else {
                    Add-CheckResult pass "Python 依赖" "pip check 与项目入口导入均通过"
                }
            } else {
                Add-CheckResult error "Python 依赖" $pipOutput "重新安装 requirements.txt 并解决版本冲突"
            }
        } else {
            Add-CheckResult warning "Python 依赖" "已按参数跳过 pip check"
        }
    } else {
        Add-CheckResult error "项目 Python" "项目虚拟环境无法运行或版本过低" "删除损坏的 .venv 后重新创建"
    }
} else {
    Add-CheckResult error "项目 Python" "缺少 .venv\Scripts\python.exe" "执行 python -m venv .venv 并安装 requirements.txt"
}

# Node：便携版优先，缺失时接受系统安装版本。
$portableNode = Join-Path $Root ".venv\nodejs\node.exe"
$portableNpm = Join-Path $Root ".venv\nodejs\npm.cmd"
$nodePath = $null
$npmPath = $null
$nodeSource = ""
if ((Test-Path -LiteralPath $portableNode -PathType Leaf) -and
    (Test-Path -LiteralPath $portableNpm -PathType Leaf)) {
    $nodePath = $portableNode
    $npmPath = $portableNpm
    $nodeSource = "项目便携版"
} else {
    $nodePath = Get-CommandPath "node"
    $npmPath = Get-CommandPath "npm"
    $nodeSource = "系统 PATH"
}

if ($nodePath -and $npmPath) {
    $nodeVersionText = Invoke-VersionCommand $nodePath @("--version")
    $nodeVersion = ConvertTo-Version $nodeVersionText
    $nodeSupported = $nodeVersion -and (
        (($nodeVersion.Major -eq 20) -and $nodeVersion -ge [version]"20.19.0") -or
        (($nodeVersion.Major -eq 22) -and $nodeVersion -ge [version]"22.12.0") -or
        ($nodeVersion.Major -gt 22)
    )
    if ($nodeSupported) {
        Add-CheckResult pass "Node.js" "$nodeVersionText（$nodeSource）"
    } else {
        Add-CheckResult error "Node.js" "Vite 8 不支持当前版本：$nodeVersionText" "安装 Node 20.19+ 或 22.12+"
    }
    $npmVersionText = Invoke-VersionCommand $npmPath @("--version")
    if ($npmVersionText) {
        Add-CheckResult pass "npm" "$npmVersionText（$npmPath）"
    } else {
        Add-CheckResult error "npm" "npm 无法执行" "重新安装 Node.js"
    }
} else {
    Add-CheckResult error "Node.js" "未找到可配套使用的 node 和 npm" "准备 .venv\nodejs 或安装 Node.js 并加入 PATH"
}

$vitePath = Join-Path $Root "frontend\node_modules\.bin\vite.cmd"
if (Test-Path -LiteralPath $vitePath -PathType Leaf) {
    Add-CheckResult pass "前端依赖" "frontend\node_modules 已安装"
} else {
    Add-CheckResult error "前端依赖" "尚未安装前端依赖" "在 frontend 目录执行 npm ci"
}

# API Key 只决定在线 AI 能力，不影响规则检测与确定性写回。
$apiKeySource = $null
if ($env:LLM_API_KEY -and $env:LLM_API_KEY.Trim()) {
    $apiKeySource = "环境变量 LLM_API_KEY"
} elseif ($env:THESIS_LLM_API_KEY -and $env:THESIS_LLM_API_KEY.Trim()) {
    $apiKeySource = "环境变量 THESIS_LLM_API_KEY"
} elseif (Test-LocalApiKey) {
    $apiKeySource = "local_settings.py"
}
if ($apiKeySource) {
    Add-CheckResult pass "在线 AI" "API Key 已配置（$apiKeySource；未显示密钥内容）"
} else {
    Add-CheckResult warning "在线 AI" "未配置 API Key，语义判定、文字改写和问答不可用" "设置 LLM_API_KEY 或使用未跟踪的 local_settings.py"
}

# ONNX 模型：只检查生产代码实际要求的权重和 tokenizer，不在检查阶段加载大模型。
$modelRelativeDir = Get-ConfigString "EMBEDDING_DIR" "models/bge-small-zh-v1.5-onnx"
$precision = (Get-ConfigString "EMBEDDING_ONNX_PRECISION" "fp32").ToLowerInvariant()
$modelFile = if ($precision -eq "int8") { "model-int8.onnx" } else { "model-fp32.onnx" }
$modelDir = Join-Path $Root $modelRelativeDir
$modelAssets = @($modelFile, "tokenizer.json")
$missingModelAssets = @($modelAssets | Where-Object {
    $path = Join-Path $modelDir $_
    -not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0
})
if ($missingModelAssets.Count -eq 0) {
    Add-CheckResult pass "ONNX 模型" "$modelRelativeDir（$precision）资产完整"
} else {
    Add-CheckResult warning "ONNX 模型" ("缺少：" + ($missingModelAssets -join "、")) "按 README 将模型资产放入 $modelRelativeDir"
}

# 知识库：目录名是"语料内容 + 切片参数"的哈希（见 rag/kb_store），Python 侧算最可靠；
# 这里只问"哪些学院需要（重新）构建"，不自己拼路径。
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    Push-Location $Root
    try {
        $missingKnowledgeBases = @(& $venvPython -m backend.rag.kb_store --missing-only 2>$null)
    } finally {
        Pop-Location
    }
    if ($missingKnowledgeBases.Count -eq 0) {
        Add-CheckResult pass "知识库" "所有学院知识库均已构建（按规范文本内容寻址）"
    } else {
        Add-CheckResult warning "知识库" ("尚未构建或与规范文本不一致：" + ($missingKnowledgeBases -join "、")) "模型就绪后调用 /api/kb/build 构建"
    }
} else {
    Add-CheckResult warning "知识库" "缺少项目 Python，无法核对知识库" "先创建 .venv 并安装依赖"
}

$errorCount = @($Results | Where-Object status -eq "error").Count
$warningCount = @($Results | Where-Object status -eq "warning").Count
$baseReady = $errorCount -eq 0
$aiReady = $baseReady -and [bool]$apiKeySource
$ragReady = $aiReady -and $missingModelAssets.Count -eq 0 -and $missingKnowledgeBases.Count -eq 0

$report = [pscustomobject]@{
    project_root = $Root
    checked_at   = (Get-Date).ToString("o")
    capabilities = [pscustomobject]@{
        basic = $baseReady
        ai    = $aiReady
        rag   = $ragReady
    }
    summary = [pscustomobject]@{
        passed   = @($Results | Where-Object status -eq "pass").Count
        warnings = $warningCount
        errors   = $errorCount
    }
    checks = @($Results)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 6
} else {
    Write-Host "项目环境检查：$Root"
    Write-Host ""
    foreach ($item in $Results) {
        $label = switch ($item.status) {
            "pass" { "通过" }
            "warning" { "警告" }
            "error" { "失败" }
        }
        $color = switch ($item.status) {
            "pass" { "Green" }
            "warning" { "Yellow" }
            "error" { "Red" }
        }
        Write-Host ("[{0}] {1}：{2}" -f $label, $item.name, $item.message) -ForegroundColor $color
        if ($item.resolution) {
            Write-Host ("       处理：{0}" -f $item.resolution) -ForegroundColor DarkGray
        }
    }
    Write-Host ""
    Write-Host ("基础模式：{0}" -f $(if ($baseReady) { "可用" } else { "不可用" }))
    Write-Host ("在线 AI：{0}" -f $(if ($aiReady) { "可用" } else { "不可用" }))
    Write-Host ("完整 RAG：{0}" -f $(if ($ragReady) { "可用" } else { "不可用" }))
    Write-Host ("汇总：{0} 通过，{1} 警告，{2} 失败" -f $report.summary.passed, $warningCount, $errorCount)
}

if ($errorCount -gt 0) {
    exit 1
}
exit 0
