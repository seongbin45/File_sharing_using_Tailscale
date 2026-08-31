<#
    ts_receive.ps1

    Receiving side of the Tailscale project backup.

    Taildrop drops "<Project>_<yyyy_MM_dd_HH_mm>.zip" into the watch directory.
    This script ingests each archive into a git-managed directory, one commit
    per archive, and tags the repository whenever a full cycle completes.

    Run it periodically from Task Scheduler. See install.md.
    Requires PowerShell 5.1 and git on PATH.
#>

[CmdletBinding()]
param()

# ============================== CONFIG ====================================

# Where Taildrop leaves the archives. Scanned non-recursively.
$WatchDir = Join-Path $env:USERPROFILE 'Downloads'

# Git-managed directory holding the unpacked projects.
# The name stays fixed; points in time are marked with git tags.
$RepoDir = Join-Path $WatchDir 'PycharmProjects'

# Staging and log live outside the repository so they are never committed.
# Keep this on the same volume as $RepoDir: moving a freshly unpacked project
# into place is then a rename instead of a full copy.
$WorkDir = 'C:\TempReceive'

# An archive is ignored until it has been untouched for this long, so a
# transfer still in progress is never ingested half-written.
$MinAgeSeconds = 30

# $true  -> processed archives move to $WatchDir\_processed
# $false -> processed archives are deleted (the content is in git already)
$KeepProcessedZip = $false

# When to tag a point in time.
#   'cycle'   every known project has been updated since the previous tag
#   'sameday' every known project's last backup falls on the same date
$TagMode = 'cycle'

# How many projects the sender has. 0 = learn it from what arrives.
# Set a real number to stop the very first tag from firing early, while the
# management directory is still filling up for the first time.
$ExpectedProjectCount = 0

$LogMaxMB = 5

# ==========================================================================

$ErrorActionPreference = 'Stop'

$LogFile      = Join-Path $WorkDir 'receive.log'
$StageRoot    = Join-Path $WorkDir 'stage'
$ProcessedDir = Join-Path $WatchDir '_processed'
$RejectedDir  = Join-Path $WatchDir '_rejected'
$StateFile    = Join-Path $RepoDir '.ts_state.json'
$SummaryFile  = Join-Path $RepoDir 'Day_count.txt'

# Exit code reported to Task Scheduler as Last Result.
#   0 nothing to do, or everything ingested
#   1 fatal - could not even reach a usable repository
#   2 at least one archive failed and was left for the next run
$script:ExitCode = 0

# UTF-8 without a byte order mark. The default -Encoding UTF8 in Windows
# PowerShell writes a BOM, which shows up as stray characters when the log
# or Day_count.txt is read with "type" in cmd.
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ------------------------------- helpers ----------------------------------

function Write-TextLines {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string[]]$Lines)
    [System.IO.File]::WriteAllLines($Path, $Lines, $script:Utf8NoBom)
}

function Write-Log {
    param([string]$Message)
    $line = '[{0}] {1}{2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message, [Environment]::NewLine
    [System.IO.File]::AppendAllText($LogFile, $line, $script:Utf8NoBom)
}

function Rotate-Log {
    if (-not (Test-Path -LiteralPath $LogFile)) { return }
    if ((Get-Item -LiteralPath $LogFile).Length -le ($LogMaxMB * 1MB)) { return }
    $old = "$LogFile.1"
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
    Move-Item -LiteralPath $LogFile -Destination $old
}

function Invoke-Native {
    <#
        Runs an external command and returns @{ Code; Output }.

        $ErrorActionPreference = 'Stop' must not be in effect here. Windows
        PowerShell turns anything a native command writes to stderr into an
        ErrorRecord when 2>&1 is used, and under 'Stop' that terminates the
        script - git and tar both write ordinary progress text to stderr, so
        a perfectly successful command would abort the run.
    #>
    param([Parameter(Mandatory)][string]$Command, [Parameter(Mandatory)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Command @Arguments 2>&1
        return @{ Code = $LASTEXITCODE; Output = @($output) }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-Git {
    <# Runs git inside $RepoDir. Returns the exit code, logs any output. #>
    param([Parameter(Mandatory)][string[]]$GitArgs, [switch]$Quiet)
    $result = Invoke-Native -Command 'git' -Arguments (@('-C', $RepoDir) + $GitArgs)
    if (-not $Quiet) {
        foreach ($line in $result.Output) {
            if ("$line".Trim()) { Write-Log "    git: $line" }
        }
    }
    return $result.Code
}

function Get-GitOutput {
    <# Returns git's stdout as a string array, or $null when git failed. #>
    param([Parameter(Mandatory)][string[]]$GitArgs)
    $result = Invoke-Native -Command 'git' -Arguments (@('-C', $RepoDir) + $GitArgs)
    if ($result.Code -ne 0) { return $null }
    return @($result.Output | ForEach-Object { "$_" })
}

function Test-RepoDirty {
    $lines = Get-GitOutput @('status', '--porcelain')
    if ($null -eq $lines) { return $false }
    return [bool](@($lines | Where-Object { $_.Trim() }).Count)
}

function Remove-Tree {
    <#
        Deletes a directory tree. Falls back to robocopy mirroring an empty
        directory, which is the reliable way to remove paths longer than
        MAX_PATH - venv and node_modules routinely produce those.
    #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
        return $true
    } catch {
        Write-Log "    Remove-Item failed, falling back to robocopy: $($_.Exception.Message)"
    }
    $empty = Join-Path $WorkDir ('empty_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $empty -Force | Out-Null
    Invoke-Native -Command 'robocopy' -Arguments @($empty, $Path, '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') | Out-Null
    Remove-Item -LiteralPath $empty -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Path  -Recurse -Force -ErrorAction SilentlyContinue
    return (-not (Test-Path -LiteralPath $Path))
}

function Test-FileReady {
    <# True once the file has stopped changing and can be opened exclusively. #>
    param([Parameter(Mandatory)][System.IO.FileInfo]$File)
    if (((Get-Date) - $File.LastWriteTime).TotalSeconds -lt $MinAgeSeconds) { return $false }
    try {
        $stream = [System.IO.File]::Open($File.FullName, 'Open', 'Read', 'None')
        $stream.Close()
        return $true
    } catch {
        return $false
    }
}

# ------------------------------ state -------------------------------------
# .ts_state.json is the machine-readable source of truth. The Day_count.txt
# files are human-readable renderings of it, regenerated on every ingest -
# they have to be, because a project folder is wiped and repopulated each
# time and would otherwise lose its counter.

function Read-State {
    $state = @{ cycle_count = 0; last_tag = ''; projects = @{} }
    if (-not (Test-Path -LiteralPath $StateFile)) { return $state }
    try {
        $raw = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        if ($null -ne $raw.cycle_count) { $state.cycle_count = [int]$raw.cycle_count }
        if ($null -ne $raw.last_tag)    { $state.last_tag    = [string]$raw.last_tag }
        if ($null -ne $raw.projects) {
            foreach ($p in $raw.projects.PSObject.Properties) {
                $state.projects[$p.Name] = @{
                    count = [int]$p.Value.count
                    first = [string]$p.Value.first
                    last  = [string]$p.Value.last
                }
            }
        }
    } catch {
        Write-Log "WARN: state file unreadable, starting fresh: $($_.Exception.Message)"
    }
    return $state
}

function Write-State {
    param([Parameter(Mandatory)][hashtable]$State)
    Write-TextLines -Path $StateFile -Lines @(($State | ConvertTo-Json -Depth 5))
}

function Write-ProjectDayCount {
    param(
        [Parameter(Mandatory)][string]$Project,
        [Parameter(Mandatory)][hashtable]$Entry
    )
    $path = Join-Path (Join-Path $RepoDir $Project) 'Day_count.txt'
    Write-TextLines -Path $path -Lines @(
        "project        : $Project",
        "backup_count   : $($Entry.count)",
        "first_backup   : $($Entry.first)",
        "last_backup    : $($Entry.last)",
        "last_processed : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "",
        "Written by ts_receive.ps1. Not part of the original project.",
        "This project's own repository is stored as .git_archived - copy the",
        "folder out of the management directory and rename it back to .git."
    )
}

function Write-SummaryDayCount {
    param([Parameter(Mandatory)][hashtable]$State)
    $tag = if ($State.last_tag) { $State.last_tag } else { '(none)' }
    $lines = @(
        "PycharmProjects backup summary",
        "updated      : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "cycle_count  : $($State.cycle_count)",
        "last_tag     : $tag",
        "projects     : $($State.projects.Count)",
        "",
        ('{0,-32} {1,6}  {2,-18} {3}' -f 'project', 'count', 'last_backup', 'first_backup'),
        ('-' * 84)
    )
    foreach ($name in ($State.projects.Keys | Sort-Object)) {
        $e = $State.projects[$name]
        $lines += ('{0,-32} {1,6}  {2,-18} {3}' -f $name, $e.count, $e.last, $e.first)
    }
    Write-TextLines -Path $SummaryFile -Lines $lines
}

# ------------------------------ repository --------------------------------

function Initialize-Repo {
    if (-not (Test-Path -LiteralPath $RepoDir)) {
        New-Item -ItemType Directory -Path $RepoDir -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RepoDir '.git'))) {
        Write-Log "initialising git repository at $RepoDir"
        if ((Invoke-Git @('init')) -ne 0) { return $false }
        # These are backups: store bytes verbatim, never normalise line endings.
        Write-TextLines -Path (Join-Path $RepoDir '.gitattributes') -Lines @('* -text')
    }
    Invoke-Git @('config', 'core.autocrlf', 'false') -Quiet | Out-Null
    Invoke-Git @('config', 'core.longpaths', 'true')  -Quiet | Out-Null
    if (-not (Get-GitOutput @('config', 'user.name'))) {
        Invoke-Git @('config', 'user.name', 'ts-receive') -Quiet | Out-Null
    }
    if (-not (Get-GitOutput @('config', 'user.email'))) {
        Invoke-Git @('config', 'user.email', 'ts-receive@localhost') -Quiet | Out-Null
    }
    return $true
}

function Rename-NestedGitDirs {
    <#
        A directory containing .git is treated by the outer repository as an
        embedded repository: git records a gitlink and tracks none of the
        files inside. Since preserving each project's history is the whole
        point of this backup, the nested .git is renamed so it becomes an
        ordinary directory and is stored in full.
    #>
    param([Parameter(Mandatory)][string]$Root)
    $renamed = 0
    $dirs = @(Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force -Filter '.git' -ErrorAction SilentlyContinue)
    foreach ($d in $dirs) {
        $target = Join-Path $d.Parent.FullName '.git_archived'
        if (Test-Path -LiteralPath $target) {
            Write-Log "    WARN: .git_archived already exists, leaving .git as is: $($d.FullName)"
            continue
        }
        Rename-Item -LiteralPath $d.FullName -NewName '.git_archived'
        $renamed++
    }
    return $renamed
}

function Expand-Zip {
    param([Parameter(Mandatory)][string]$ZipPath, [Parameter(Mandatory)][string]$Destination)
    # bsdtar reads zip and is far faster than Expand-Archive on large trees.
    $result = Invoke-Native -Command 'tar' -Arguments @('-x', '-f', $ZipPath, '-C', $Destination)
    foreach ($line in $result.Output) { if ("$line".Trim()) { Write-Log "    tar: $line" } }
    # 1 = warnings (locked or odd entries); the archive is still usable.
    if ($result.Code -le 1) { return $true }
    Write-Log "    tar exited $($result.Code), retrying with Expand-Archive"
    try {
        Expand-Archive -LiteralPath $ZipPath -DestinationPath $Destination -Force
        return $true
    } catch {
        Write-Log "    Expand-Archive failed: $($_.Exception.Message)"
        return $false
    }
}

# ------------------------------ ingest ------------------------------------

function Import-Archive {
    <# $true ingested, $false retry next run, $null permanently rejected. #>
    param(
        [Parameter(Mandatory)][System.IO.FileInfo]$Zip,
        [Parameter(Mandatory)][string]$Stamp,
        [Parameter(Mandatory)][hashtable]$State
    )

    $stage = Join-Path $StageRoot ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null

    try {
        if (-not (Expand-Zip -ZipPath $Zip.FullName -Destination $stage)) { return $false }

        $tops = @(Get-ChildItem -LiteralPath $stage -Force)
        if ($tops.Count -ne 1 -or -not $tops[0].PSIsContainer) {
            Write-Log "    REJECT: archive does not contain exactly one top-level folder"
            return $null
        }
        $top = $tops[0]
        $project = $top.Name

        $renamed = Rename-NestedGitDirs -Root $top.FullName
        if ($renamed -gt 0) { Write-Log "    renamed $renamed nested .git -> .git_archived" }

        # Carry the counter forward before the old copy is discarded.
        $entry = $State.projects[$project]
        if (-not $entry) { $entry = @{ count = 0; first = $Stamp; last = $Stamp } }

        $dest = Join-Path $RepoDir $project
        if (Test-Path -LiteralPath $dest) {
            if (-not (Remove-Tree -Path $dest)) {
                Write-Log "    FAIL: could not clear $dest"
                return $false
            }
        }
        Move-Item -LiteralPath $top.FullName -Destination $dest

        $entry.count = [int]$entry.count + 1
        $entry.last  = $Stamp
        if (-not $entry.first) { $entry.first = $Stamp }
        $State.projects[$project] = $entry

        Write-ProjectDayCount -Project $project -Entry $entry
        Write-SummaryDayCount -State $State
        Write-State -State $State

        if ((Invoke-Git @('add', '-A')) -ne 0) {
            Write-Log "    FAIL: git add"
            return $false
        }
        if (-not (Test-RepoDirty)) {
            Write-Log "    nothing changed, no commit"
            return $true
        }
        $message = "$project @ $Stamp"
        if ((Invoke-Git @('commit', '-m', $message)) -ne 0) {
            Write-Log "    FAIL: git commit"
            return $false
        }
        Write-Log "    committed: $message"
        return $true
    } finally {
        Remove-Tree -Path $stage | Out-Null
    }
}

function Update-CycleTag {
    param([Parameter(Mandatory)][hashtable]$State)

    if ($State.projects.Count -eq 0) { return }
    if ($ExpectedProjectCount -gt 0 -and $State.projects.Count -lt $ExpectedProjectCount) {
        Write-Log "cycle: $($State.projects.Count)/$ExpectedProjectCount projects known, waiting"
        return
    }

    $stamps = @($State.projects.Values | ForEach-Object { $_.last })
    $newest = @($stamps | Sort-Object)[-1]

    if ($TagMode -eq 'sameday') {
        $days = @($stamps | ForEach-Object { $_.Substring(0, 10) } | Sort-Object -Unique)
        if ($days.Count -ne 1) { return }
    } else {
        foreach ($s in $stamps) {
            if ($State.last_tag -and ($s -le $State.last_tag)) { return }
        }
    }

    if (@(Get-GitOutput @('tag', '-l', $newest) | Where-Object { $_.Trim() }).Count) {
        Write-Log "cycle: tag $newest already exists, skipping"
        return
    }
    if ((Invoke-Git @('tag', '-a', $newest, '-m', "full cycle complete ($TagMode)")) -ne 0) {
        Write-Log "cycle: FAILED to create tag $newest"
        return
    }
    $State.cycle_count = [int]$State.cycle_count + 1
    $State.last_tag = $newest
    Write-SummaryDayCount -State $State
    Write-State -State $State
    Invoke-Git @('add', '-A') | Out-Null
    if (Test-RepoDirty) {
        Invoke-Git @('commit', '-m', "cycle $($State.cycle_count) complete @ $newest") | Out-Null
    }
    Write-Log "cycle $($State.cycle_count) complete, tagged $newest"
}

# ------------------------------- main -------------------------------------

New-Item -ItemType Directory -Path $WorkDir   -Force | Out-Null
New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
Rotate-Log
Write-Log '=== run start ==='

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Log 'FATAL: git not found on PATH'
        Write-Log '=== run end (exit 1) ==='
        exit 1
    }
    if (-not (Test-Path -LiteralPath $WatchDir)) {
        Write-Log "FATAL: watch directory not found: $WatchDir"
        Write-Log '=== run end (exit 1) ==='
        exit 1
    }
    if (-not (Initialize-Repo)) {
        Write-Log 'FATAL: could not initialise the repository'
        Write-Log '=== run end (exit 1) ==='
        exit 1
    }

    $pattern = '^(?<project>.+)_(?<stamp>\d{4}_\d{2}_\d{2}_\d{2}_\d{2})$'
    $candidates = @(
        Get-ChildItem -LiteralPath $WatchDir -File -Filter '*.zip' -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match $pattern } |
            Sort-Object { [regex]::Match($_.BaseName, $pattern).Groups['stamp'].Value }
    )

    if ($candidates.Count -eq 0) {
        Write-Log 'no archives to ingest'
        Write-Log '=== run end (exit 0) ==='
        exit 0
    }

    $state = Read-State

    foreach ($zip in $candidates) {
        if (-not (Test-FileReady -File $zip)) {
            Write-Log "skipping $($zip.Name) - still being written or too fresh"
            continue
        }
        $stamp = [regex]::Match($zip.BaseName, $pattern).Groups['stamp'].Value
        Write-Log "ingesting $($zip.Name)"

        # One bad archive must not abort the whole run.
        try {
            $result = Import-Archive -Zip $zip -Stamp $stamp -State $state
        } catch {
            Write-Log "    ERROR: $($_.Exception.Message)"
            $result = $false
        }

        if ($result -eq $true) {
            if ($KeepProcessedZip) {
                New-Item -ItemType Directory -Path $ProcessedDir -Force | Out-Null
                Move-Item -LiteralPath $zip.FullName -Destination $ProcessedDir -Force
                Write-Log '    archived zip -> _processed'
            } else {
                Remove-Item -LiteralPath $zip.FullName -Force
                Write-Log '    removed zip'
            }
        } elseif ($null -eq $result) {
            New-Item -ItemType Directory -Path $RejectedDir -Force | Out-Null
            Move-Item -LiteralPath $zip.FullName -Destination $RejectedDir -Force
            Write-Log '    moved zip -> _rejected'
            $script:ExitCode = 2
        } else {
            Write-Log '    left zip in place, will retry next run'
            $script:ExitCode = 2
        }
    }

    Update-CycleTag -State $state
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    Write-Log $_.ScriptStackTrace
    $script:ExitCode = 1
}

Write-Log "=== run end (exit $script:ExitCode) ==="
exit $script:ExitCode
