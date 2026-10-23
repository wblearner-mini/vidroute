<#
.SYNOPSIS
    Install vidroute on Windows and set it up: uv -> vidroute -> `vidroute setup` (one plan, one question).

.DESCRIPTION
    1. Finds uv, or puts uv 0.13.4 into %USERPROFILE%\.vidroute\runtimes\uv (SHA-256 pinned below;
       from GitHub, else PyPI, else the Aliyun PyPI mirror). Nothing else is installed system-wide.
    2. Installs vidroute with `uv tool install` (uv brings its own Python if none is installed) and adds
       %USERPROFILE%\.local\bin to your user PATH.
    3. Runs `vidroute setup`: it shows ONE plan - ffmpeg, local speech/OCR models, the vidroute skill for
       every agent host it finds (Claude Code, Codex, Gemini CLI, Cursor, ...), a self-test - and asks first.

    Re-running the script upgrades vidroute and keeps everything already installed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Source . -AgentHost claude-code -Yes
.EXAMPLE
    irm https://raw.githubusercontent.com/wblearner-mini/vidroute/main/install.ps1 | iex
    (settings through environment variables: VIDROUTE_SOURCE, VIDROUTE_MIRROR=cn, VIDROUTE_YES=1)
#>
[CmdletBinding()]
param(
    # What `uv tool install` installs: a PyPI name, git+https URL, wheel URL or local folder.
    [string]$Source = $(if ($env:VIDROUTE_SOURCE) { $env:VIDROUTE_SOURCE } else { "git+https://github.com/wblearner-mini/vidroute" }),
    # Agent hosts to connect (default: every host vidroute finds); "none" connects none.
    [string[]]$AgentHost = @(),
    # auto: use mirrors in mainland China only when huggingface.co is unreachable; cn: always.
    [ValidateSet("auto", "cn")][string]$Mirror = $(if ($env:VIDROUTE_MIRROR) { $env:VIDROUTE_MIRROR } else { "auto" }),
    # Confirm the setup plan without asking (for scripts and CI).
    [switch]$Yes = ($env:VIDROUTE_YES -eq "1"),
    # Install vidroute only; run `vidroute setup` yourself later.
    [switch]$NoSetup
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times slower with a progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# Pinned uv: same version as vidroute's own bootstrap (UV_VERSION). Hashes from the GitHub release asset
# digests and PyPI (2026-10-23); tests/test_install_scripts.py keeps them in sync with envs/uv.lock.txt.
$UvVersion = "0.13.4"
$UvBuilds = @{
    "x64"   = @{ Zip = "uv-x86_64-pc-windows-msvc.zip";  ZipSha = "ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225";
                 Wheel = "packages/2f/b9/b526eab6c72eacc705a58378f6850fde046b28c86fed01bd42da61367c6b/uv-0.13.4-py3-none-win_amd64.whl";
                 WheelSha = "b2ff80397273fff0e02c20eacae6882834c811827d6fc08f658db156fe754386" }
    "arm64" = @{ Zip = "uv-aarch64-pc-windows-msvc.zip"; ZipSha = "c4bc5a9534b2bb6a12fe29ea748445e1a6f9cb7bb275211c4fb74c52ecf317a5";
                 Wheel = "packages/7c/f7/4d5fa89d3d10ff6a84e547ef6e02c45b2f2d50e66682da778acf60db63f1/uv-0.13.4-py3-none-win_arm64.whl";
                 WheelSha = "deffa5a4afdb48f3e44fb26e80377d256b330ddcad0373daff4b54f6162b43a4" }
}
$PypiMirror = "https://mirrors.aliyun.com/pypi/simple/"
$PythonMirror = "https://registry.npmmirror.com/-/binary/python-build-standalone/"
$VidrouteHome = if ($env:VIDROUTE_HOME) { $env:VIDROUTE_HOME } else { Join-Path $HOME ".vidroute" }

function Say([string]$Text) { Write-Host "vidroute-install: $Text" }

function Stop-Install([int]$Code) {
    if ($PSCommandPath) { exit $Code }  # run as a file: report the exit code
    $global:LASTEXITCODE = $Code        # piped into iex: never close the user's window
}

function Test-Url([string]$Url) {
    try { $null = Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec 4; return $true }
    catch { return [bool]($_.Exception.Response) }  # an HTTP error still means the site answered
}

function Get-Arch {
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if ($arch -eq "ARM64") { return "arm64" }
    return "x64"
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $VidrouteHome "runtimes\uv\uv.exe"),
        (Join-Path $VidrouteHome "runtimes\uv\bin\uv.exe"),
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe")
    )
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe") }
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c -PathType Leaf) { return $c } }
    return $null
}

function Save-Verified([string]$Url, [string]$Sha256, [string]$OutFile) {
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 600
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutFile).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) { Remove-Item -LiteralPath $OutFile -Force; throw "SHA-256 mismatch for $Url" }
}

function Expand-Entries([string]$Archive, [string[]]$Names, [string]$Dest) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($name in $Names) {
            $entry = $zip.Entries | Where-Object { $_.FullName -eq $name } | Select-Object -First 1
            if (-not $entry) { throw "$name is missing from $Archive" }
            $target = Join-Path $Dest ([System.IO.Path]::GetFileName($name))
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }
}

function Install-Uv([bool]$ChinaFirst) {
    $build = $UvBuilds[(Get-Arch)]
    $dest = Join-Path $VidrouteHome "runtimes\uv"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("vidroute-uv-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zipSource = @{ Url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$($build.Zip)"; Sha = $build.ZipSha;
                    Names = @("uv.exe", "uvx.exe"); File = "uv.zip" }
    $wheelNames = @("uv-$UvVersion.data/scripts/uv.exe", "uv-$UvVersion.data/scripts/uvx.exe")
    $pypi = @{ Url = "https://files.pythonhosted.org/$($build.Wheel)"; Sha = $build.WheelSha; Names = $wheelNames; File = "uv.whl" }
    $aliyun = @{ Url = "https://mirrors.aliyun.com/pypi/$($build.Wheel)"; Sha = $build.WheelSha; Names = $wheelNames; File = "uv.whl" }
    $sources = if ($ChinaFirst) { @($aliyun, $zipSource, $pypi) } else { @($zipSource, $pypi, $aliyun) }
    try {
        foreach ($s in $sources) {
            try {
                Say "downloading uv $UvVersion from $($s.Url)"
                $file = Join-Path $tmp $s.File
                Save-Verified $s.Url $s.Sha $file
                Expand-Entries $file $s.Names $dest
                return (Join-Path $dest "uv.exe")
            } catch { Say "  failed: $($_.Exception.Message)" }
        }
    } finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    throw "Could not download uv. Install it yourself (winget install -e --id astral-sh.uv) and run this script again."
}

# --- 1. network --------------------------------------------------------------------------------
$china = $Mirror -eq "cn"
if (-not $china -and -not (Test-Url "https://huggingface.co/") -and (Test-Url "https://www.modelscope.cn/")) {
    $china = $true
    Say "huggingface.co is unreachable: using mirrors in mainland China (all downloads stay hash-checked)"
}
if ($china) {
    if (-not $env:UV_DEFAULT_INDEX) { $env:UV_DEFAULT_INDEX = $PypiMirror }
    if (-not $env:UV_PYTHON_INSTALL_MIRROR) { $env:UV_PYTHON_INSTALL_MIRROR = $PythonMirror }
}

# --- 2. uv ---------------------------------------------------------------------------------------
$uv = Find-Uv
if ($uv) { Say "using uv at $uv" } else { $uv = Install-Uv $china }

# --- 3. vidroute -------------------------------------------------------------------------------
if ($Source -notmatch "^[a-z+]+://" -and (Test-Path -LiteralPath $Source)) { $Source = (Resolve-Path -LiteralPath $Source).Path }
Say "installing vidroute from $Source"
& $uv tool install --force --reinstall $Source
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed (exit $LASTEXITCODE)" }
$bin = (& $uv tool dir --bin).Trim()
& $uv tool update-shell | Out-Null
if (($env:Path -split ";") -notcontains $bin) { $env:Path = "$bin;$env:Path" }
$vidroute = Join-Path $bin "vidroute.exe"
Say "vidroute installed: $vidroute"

# --- 4. setup ----------------------------------------------------------------------------------
if ($NoSetup) {
    Say "next: vidroute setup   (open a new terminal first so PATH includes $bin)"
    Stop-Install 0
    return
}
$setupArgs = @("setup")
foreach ($h in $AgentHost) { $setupArgs += @("--host", $h) }
if ($china) { $setupArgs += @("--mirror", "cn") }
if ($Yes) { $setupArgs += "--yes" }
& $vidroute @setupArgs
$code = $LASTEXITCODE
if ($code -eq 0) { Say "done. Open a new terminal to use 'vidroute' directly; restart your agent tools to load the skill." }
Stop-Install $code
