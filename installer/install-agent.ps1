$ErrorActionPreference = "Stop"

$BaseUrl = if ($env:ORA_INSTALL_BASE_URL) {
    $env:ORA_INSTALL_BASE_URL.TrimEnd("/")
} else {
    "https://recruit.agentmesh360.com"
}
$AdapterVersion = "0.1.13"
$AdapterSha256 = "4d8c10b1497776ac213eb25a2928971ee92ab8c8b7a5c2dd5b7f489c2757d60b"
$SkillVersion = "0.3.8"
$SkillSha256 = "45cbce03d86dce71fb308722a601f6484fb22eeac08228435be2e9c83bb942e6"
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
$ReleaseRoot = Join-Path $InstallRoot "releases\$AdapterVersion"
$Venv = Join-Path $ReleaseRoot "venv"
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
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) (
    "agentmesh-officialrecruitment-" + [Guid]::NewGuid().ToString("N")
)
$Wheel = Join-Path $WorkDir (
    "official_recruitment_agent-$AdapterVersion-py3-none-any.whl"
)
$SkillFile = Join-Path $WorkDir "SKILL.md"
$PreviousSkipUpdate = $env:ORA_SKIP_UPDATE
$InstallFinalized = $false
$ReleaseReplaced = $false
$PreviousRelease = Join-Path (
    (Join-Path $InstallRoot "update")
) ("previous-$AdapterVersion-" + [Guid]::NewGuid().ToString("N"))

function Assert-AssetHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    if ($Expected.StartsWith("__ORA_")) {
        throw "安装器尚未绑定正式资产摘要，安装已停止。"
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected.ToLowerInvariant()) {
        throw "安装资产校验失败：$([IO.Path]::GetFileName($Path))"
    }
}

function Write-PrivateJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $Parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $Payload = ($Value | ConvertTo-Json -Depth 12) + "`n"
    [IO.File]::WriteAllText(
        $Temporary,
        $Payload,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -Force $Temporary $Path
}

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
    New-Item -ItemType Directory -Force -Path (
        (Join-Path $InstallRoot "releases"),
        (Join-Path $InstallRoot "update"),
        $ReleaseRoot,
        $BinDir,
        $WorkDir
    ) | Out-Null
    Invoke-WebRequest -UseBasicParsing `
        -Uri "$BaseUrl/downloads/official-recruitment-agent.whl" `
        -OutFile $Wheel
    Invoke-WebRequest -UseBasicParsing `
        -Uri "$BaseUrl/downloads/agentmesh-officialrecruitment-skill/SKILL.md" `
        -OutFile $SkillFile
    Assert-AssetHash -Path $Wheel -Expected $AdapterSha256
    Assert-AssetHash -Path $SkillFile -Expected $SkillSha256

    $SkillContent = Get-Content -Raw -Encoding UTF8 $SkillFile
    if ($SkillContent -notmatch "(?m)^name: agentmesh-officialrecruitment\s*$") {
        throw "Skill 文件校验失败，安装已停止。"
    }
    if ($SkillContent -notmatch "(?m)^version: $([regex]::Escape($SkillVersion))\s*$") {
        throw "Skill 版本与安装器不一致，安装已停止。"
    }

    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    $CliExe = Join-Path $Venv "Scripts\ora-workbench.exe"
    $NativeHost = Join-Path $Venv "Scripts\ora-native-host.exe"
    $ReuseRelease = $false
    if (Test-Path $CliExe) {
        try {
            $ExistingVersion = (& $CliExe --version | Out-String).Trim()
            $ReuseRelease = (
                $ExistingVersion -eq "ora-workbench $AdapterVersion"
            )
        } catch {
            $ReuseRelease = $false
        }
    }
    if (-not $ReuseRelease) {
        if (Test-Path $ReleaseRoot) {
            Move-Item $ReleaseRoot $PreviousRelease
        }
        $ReleaseReplaced = $true
        New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
        & $PythonExe @PythonPrefix -m venv $Venv
        & $VenvPython -m pip install `
            --disable-pip-version-check `
            --upgrade `
            --force-reinstall `
            $Wheel
    }
    $ActualVersion = (& $CliExe --version | Out-String).Trim()
    if ($ActualVersion -ne "ora-workbench $AdapterVersion") {
        throw "CLI 版本检查失败，安装已停止。"
    }
    $env:ORA_SKIP_UPDATE = "1"
    $Finalize = (
        & $CliExe install-finalize --skill-file $SkillFile |
        Out-String |
        ConvertFrom-Json
    )
    $InstallFinalized = $true
    if (
        $Finalize.status -ne "ready" -or
        $Finalize.client_version -ne $AdapterVersion -or
        $Finalize.skill_version -ne $SkillVersion -or
        $Finalize.local_profile.status -ne "ready"
    ) {
        throw "客户端安装兼容检查失败。"
    }
    if (Test-Path $PreviousRelease) {
        Remove-Item -Recurse -Force $PreviousRelease
    }

    $LauncherPath = Join-Path $InstallRoot "launcher.py"
    $Launcher = @'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
try:
    current = json.loads((root / "current.json").read_text(encoding="utf-8"))
    cli = Path(current["cli_path"]).resolve()
except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
    raise SystemExit("受管客户端版本指针无效，请重新运行官网安装器。") from error
if root not in cli.parents or not cli.is_file():
    raise SystemExit("受管客户端入口不在官方安装目录，请重新运行官网安装器。")
os.execv(str(cli), [str(cli), *sys.argv[1:]])
'@
    [IO.File]::WriteAllText(
        $LauncherPath,
        $Launcher,
        [Text.UTF8Encoding]::new($false)
    )
    $CmdPath = Join-Path $BinDir "ora-workbench.cmd"
    $PrefixText = $PythonPrefix -join " "
    $CmdContent = (
        "@echo off`r`n`"$PythonExe`" $PrefixText `"$LauncherPath`" %*`r`n"
    )
    Set-Content -Path $CmdPath -Value $CmdContent -Encoding ASCII

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $PathEntries = @($UserPath -split ";" | Where-Object { $_ })
    if ($PathEntries -notcontains $BinDir) {
        $UpdatedPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
    }
    if (($env:Path -split ";") -notcontains $BinDir) {
        $env:Path = "$BinDir;$env:Path"
    }

    Write-Output "AgentMesh-OfficialRecruitment Skill 与 CLI 适配器已安装。"
    Write-Output "版本：$AdapterVersion；Skill：$SkillVersion"
    Write-Output "CLI：$CmdPath"
    Write-Output "Chrome 本机连接组件：已注册"
    Write-Output "已有 API Key、本机画像和扩展配对资料均已保留。"
    Write-Output "请重新打开宿主 Agent 任务，让它读取新 Skill。"
    Write-Output "首次使用前由你本人配置通用 API Key："
    Write-Output "ora-workbench configure --key <AGENTMESH_API_KEY>"
} catch {
    if (-not $InstallFinalized -and $ReleaseReplaced) {
        if (Test-Path $ReleaseRoot) {
            Remove-Item -Recurse -Force $ReleaseRoot
        }
        if (Test-Path $PreviousRelease) {
            Move-Item $PreviousRelease $ReleaseRoot
        }
    }
    throw
} finally {
    if ($null -eq $PreviousSkipUpdate) {
        Remove-Item Env:ORA_SKIP_UPDATE -ErrorAction SilentlyContinue
    } else {
        $env:ORA_SKIP_UPDATE = $PreviousSkipUpdate
    }
    if (Test-Path $WorkDir) {
        Remove-Item -Recurse -Force $WorkDir
    }
    if (Test-Path $PreviousRelease) {
        Remove-Item -Recurse -Force $PreviousRelease
    }
}
