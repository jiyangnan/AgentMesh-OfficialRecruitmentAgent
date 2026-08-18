$ErrorActionPreference = "Stop"

$BaseUrl = if ($env:ORA_INSTALL_BASE_URL) {
    $env:ORA_INSTALL_BASE_URL.TrimEnd("/")
} else {
    "https://recruit.agentmesh360.com"
}
$AdapterVersion = "0.1.12"
$LocalAppData = if ($env:LOCALAPPDATA) {
    $env:LOCALAPPDATA
} else {
    Join-Path $HOME "AppData\Local"
}
$InstallRoot = if ($env:ORA_AGENT_HOME) {
    $env:ORA_AGENT_HOME
} else {
    Join-Path $LocalAppData "AgentMesh360\OfficialRecruitment"
}
$BinDir = if ($env:ORA_BIN_DIR) {
    $env:ORA_BIN_DIR
} else {
    Join-Path $LocalAppData "AgentMesh360\bin"
}
$SkillsRoot = if ($env:ORA_SKILLS_DIR) {
    $env:ORA_SKILLS_DIR
} else {
    Join-Path $HOME ".agents\skills"
}
$Venv = Join-Path $InstallRoot "venv"
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) (
    "agentmesh-officialrecruitment-" + [Guid]::NewGuid().ToString("N")
)
$Wheel = Join-Path $WorkDir (
    "official_recruitment_agent-$AdapterVersion-py3-none-any.whl"
)
$SkillFile = Join-Path $WorkDir "SKILL.md"

$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
$PythonPrefix = @()
if ($PythonCommand) {
    $PythonExe = $PythonCommand.Source
    $PythonPrefix = @("-3")
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
    }
    if (-not $PythonCommand) {
        throw "未找到 Python。请先安装 Python 3.11 或更高版本。"
    }
    $PythonExe = $PythonCommand.Source
}

try {
    & $PythonExe @PythonPrefix -c (
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) " +
        "else '需要 Python 3.11 或更高版本。')"
    )
    New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir, $WorkDir |
        Out-Null
    Invoke-WebRequest -UseBasicParsing `
        -Uri "$BaseUrl/downloads/official-recruitment-agent.whl" `
        -OutFile $Wheel
    Invoke-WebRequest -UseBasicParsing `
        -Uri "$BaseUrl/downloads/agentmesh-officialrecruitment-skill/SKILL.md" `
        -OutFile $SkillFile

    $SkillNameLine = Get-Content -Encoding UTF8 $SkillFile |
        Where-Object { $_.Trim() -eq "name: agentmesh-officialrecruitment" } |
        Select-Object -First 1
    if (-not $SkillNameLine) {
        throw "Skill 文件校验失败，安装已停止。"
    }

    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        & $PythonExe @PythonPrefix -m venv $Venv
    }
    & $VenvPython -m pip install `
        --disable-pip-version-check `
        --upgrade `
        --force-reinstall `
        $Wheel

    $CliExe = Join-Path $Venv "Scripts\ora-workbench.exe"
    & $CliExe extension host install
    $CmdPath = Join-Path $BinDir "ora-workbench.cmd"
    $CmdContent = "@echo off`r`n`"$CliExe`" %*`r`n"
    Set-Content -Path $CmdPath -Value $CmdContent -Encoding ASCII

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $PathEntries = @($UserPath -split ";" | Where-Object { $_ })
    if ($PathEntries -notcontains $BinDir) {
        $UpdatedPath = if ($UserPath) {
            "$UserPath;$BinDir"
        } else {
            $BinDir
        }
        [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
    }
    if (($env:Path -split ";") -notcontains $BinDir) {
        $env:Path = "$BinDir;$env:Path"
    }

    $SkillTargets = @($SkillsRoot)
    foreach ($HostRoot in @(
        (Join-Path $HOME ".codex"),
        (Join-Path $HOME ".claude"),
        (Join-Path $HOME ".openclaw\workspace")
    )) {
        if (Test-Path $HostRoot) {
            $SkillTargets += Join-Path $HostRoot "skills"
        }
    }
    foreach ($TargetRoot in $SkillTargets | Select-Object -Unique) {
        $Target = Join-Path $TargetRoot "agentmesh-officialrecruitment"
        New-Item -ItemType Directory -Force -Path $Target | Out-Null
        Copy-Item -Force $SkillFile (Join-Path $Target "SKILL.md")
    }

    Write-Output "AgentMesh-OfficialRecruitment Skill 与 CLI 适配器已安装。"
    Write-Output "CLI：$CmdPath"
    Write-Output "Chrome 本机连接组件：已注册"
    Write-Output "Skill：$(Join-Path $SkillsRoot 'agentmesh-officialrecruitment\SKILL.md')"
    Write-Output "请重新打开宿主 Agent 任务，让它读取新 Skill。"
    Write-Output "首次使用前由你本人配置通用 API Key："
    Write-Output "ora-workbench configure --key <AGENTMESH_API_KEY>"
} finally {
    if (Test-Path $WorkDir) {
        Remove-Item -Recurse -Force $WorkDir
    }
}
