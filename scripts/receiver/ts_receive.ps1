<#
    ts_receive.ps1

    Receiving side of the Tailscale project backup.

    Taildrop drops "<Root>_<yyyy_MM_dd_HH_mm>.7z" into the watch directory,
    where <Root> is the whole project directory archived in one piece. Each
    archive is therefore a complete, self-consistent snapshot.

    This script unpacks each one into a git-managed directory, commits it and
    tags the commit with the archive's timestamp. Every commit is a full
    restore point.

    Every $ResetAfterDays the management repository is started over: the
    current generation is moved aside as a plain dated folder with no .git,
    and a fresh repository begins. This bounds how far the repository can
    grow without ever excluding anything from the backup.

    Run it periodically from Task Scheduler. See install.md.
    Requires PowerShell 5.1, git on PATH, and 7-Zip at $SevenZip.
#>

[CmdletBinding()]
param()

# ============================== CONFIG ====================================

# Where Taildrop leaves the archives. Scanned non-recursively.
$WatchDir = Join-Path $env:USERPROFILE 'Downloads'

# Git-managed directory holding the unpacked snapshot.
# The name stays fixed; points in time are marked with git tags.
$RepoDir = Join-Path $WatchDir 'PycharmProjects'

# Where a generation goes when the repository is reset. Keep it on the same
# volume as $RepoDir so the move is a rename rather than a 6 GB copy.
$ArchiveRoot = Join-Path $env:USERPROFILE 'PycharmProjects_Archive'

# Reset the repository this many days after the last reset.
$ResetAfterDays = 90

# How many archived generations to keep. 0 = keep them all.
# Each one is a full plain copy, so budget the size of one snapshot per
# generation. Nothing is ever deleted automatically while this is 0.
$KeepArchiveGenerations = 0

# Staging and log live outside the repository so they are never committed.
# Keep this on the same volume as $RepoDir, and out of any path with spaces.
$WorkDir = 'C:\TempReceive'

# Full path to 7z.exe. Deliberately not looked up on PATH: the 7-Zip installer
# does not register itself there, and a scheduled task runs with a plain
# environment. Neither tar nor Expand-Archive can read .7z, so this is a hard
# requirement on the receiving side, not a nicety.
$SevenZip = 'C:\Program Files\7-Zip\7z.exe'

# An archive is ignored until it has been untouched for this long, so a
# transfer still in progress is never ingested half-written. A 6 GB Taildrop
# transfer takes minutes, so this wants to be generous.
$MinAgeSeconds = 120

# $true  -> processed archives move to $WatchDir\_processed
# $false -> processed archives are deleted (the content is in git already)
$KeepProcessedZip = $false

$LogMaxMB = 5

# ==========================================================================

$ErrorActionPreference = 'Stop'

$LogFile      = Join-Path $WorkDir 'receive.log'
$StageRoot    = Join-Path $WorkDir 'stage'
$ProcessedDir = Join-Path $WatchDir '_processed'
$RejectedDir  = Join-Path $WatchDir '_rejected'
$StateFile    = Join-Path $RepoDir '.ts_state.json'
$SummaryFile  = Join-Path $RepoDir 'Day_count.txt'
$RepoName     = Split-Path $RepoDir -Leaf

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
        script - git and 7-Zip both write ordinary progress text to stderr,
        so a perfectly successful command would abort the run.
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
# they have to be, because the working tree is wiped and repopulated from
# each snapshot and would otherwise lose its counters.

function Read-State {
    $state = @{
        snapshot_count = 0
        last_snapshot  = ''
        last_reset     = ''
        reset_count    = 0
        projects       = @{}
    }
    if (-not (Test-Path -LiteralPath $StateFile)) { return $state }
    try {
        $raw = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        foreach ($k in @('snapshot_count', 'reset_count')) {
            if ($null -ne $raw.$k) { $state[$k] = [int]$raw.$k }
        }
        foreach ($k in @('last_snapshot', 'last_reset')) {
            if ($null -ne $raw.$k) { $state[$k] = [string]$raw.$k }
        }
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
    $lines = @(
        "$RepoName backup summary",
        "updated        : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "snapshot_count : $($State.snapshot_count)",
        "last_snapshot  : $(if ($State.last_snapshot) { $State.last_snapshot } else { '(none)' })",
        "last_reset     : $(if ($State.last_reset) { $State.last_reset } else { '(none)' })",
        "reset_count    : $($State.reset_count)",
        "projects       : $($State.projects.Count)",
        "",
        ('{0,-40} {1,6}  {2,-18} {3}' -f 'project', 'count', 'last_backup', 'first_backup'),
        ('-' * 92)
    )
    foreach ($name in ($State.projects.Keys | Sort-Object)) {
        $e = $State.projects[$name]
        $lines += ('{0,-40} {1,6}  {2,-18} {3}' -f $name, $e.count, $e.last, $e.first)
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
    }
    Write-GitAttributes
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

function Write-GitAttributes {
    # These are backups: store bytes verbatim, never normalise line endings.
    # Recreated after every ingest, because the working tree is wiped first.
    Write-TextLines -Path (Join-Path $RepoDir '.gitattributes') -Lines @('* -text')
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

function Expand-Snapshot {
    <#
        There is no fallback here on purpose. Windows tar cannot read .7z and
        neither can Expand-Archive, so if 7-Zip is missing the run must fail
        loudly rather than half-succeed.
    #>
    param([Parameter(Mandatory)][string]$ArchivePath, [Parameter(Mandatory)][string]$Destination)
    $result = Invoke-Native -Command $SevenZip -Arguments @('x', '-y', '-bso0', '-bsp0', "-o$Destination", $ArchivePath)
    foreach ($line in $result.Output) { if ("$line".Trim()) { Write-Log "    7z: $line" } }
    # 1 = warnings (a file it could not fully restore); the tree is still usable.
    # 2 = fatal, 7 = bad command line, 8 = out of memory, 255 = user abort.
    if ($result.Code -le 1) { return $true }
    Write-Log "    FAIL: 7-Zip exited $($result.Code)"
    return $false
}

# ------------------------------- reset ------------------------------------

function Invoke-RepoReset {
    <#
        Moves the current generation out of the way as a plain dated folder
        with no .git, then starts a fresh repository. The archived copy is an
        ordinary snapshot of files - it carries no history of its own, which
        is the point: it is there to be read, not to be committed to.
    #>
    param([Parameter(Mandatory)][hashtable]$State)

    $stamp = if ($State.last_snapshot) { $State.last_snapshot } else { Get-Date -Format 'yyyy_MM_dd_HH_mm' }
    New-Item -ItemType Directory -Path $ArchiveRoot -Force | Out-Null
    $dest = Join-Path $ArchiveRoot ("{0}_{1}" -f $RepoName, $stamp)
    if (Test-Path -LiteralPath $dest) {
        $dest = "{0}__{1}" -f $dest, (Get-Date -Format 'HHmmss')
    }

    Write-Log "reset: moving current generation to $dest"
    Move-Item -LiteralPath $RepoDir -Destination $dest

    $archivedGit = Join-Path $dest '.git'
    if (Test-Path -LiteralPath $archivedGit) {
        Write-Log "reset: dropping history from the archived generation"
        if (-not (Remove-Tree -Path $archivedGit)) {
            Write-Log "reset: WARN could not remove $archivedGit"
        }
    }

    New-Item -ItemType Directory -Path $RepoDir -Force | Out-Null
    if (-not (Initialize-Repo)) {
        throw "reset: could not initialise the fresh repository"
    }

    # Counters carry across a reset; anything tied to the discarded history
    # does not. The projects table is rebuilt by the next ingest anyway.
    $State.reset_count = [int]$State.reset_count + 1
    $State.last_reset  = (Get-Date).ToString('o')
    Write-State -State $State

    Write-Log "reset: generation $($State.reset_count) archived, fresh repository ready"
    Remove-OldGenerations
}

function Remove-OldGenerations {
    if ($KeepArchiveGenerations -le 0) { return }
    if (-not (Test-Path -LiteralPath $ArchiveRoot)) { return }
    $gens = @(Get-ChildItem -LiteralPath $ArchiveRoot -Directory |
              Where-Object { $_.Name -like "$RepoName`_*" } |
              Sort-Object Name -Descending)
    if ($gens.Count -le $KeepArchiveGenerations) { return }
    foreach ($old in $gens[$KeepArchiveGenerations..($gens.Count - 1)]) {
        Write-Log "reset: removing old generation $($old.Name)"
        Remove-Tree -Path $old.FullName | Out-Null
    }
}

function Test-ResetDue {
    param([Parameter(Mandatory)][hashtable]$State)
    if ($ResetAfterDays -le 0) { return $false }
    if (-not $State.last_reset) { return $false }
    # Nothing committed yet means nothing worth archiving.
    if (-not (Get-GitOutput @('rev-parse', '--verify', 'HEAD'))) { return $false }
    try {
        $due = ([datetime]$State.last_reset).AddDays($ResetAfterDays)
    } catch {
        Write-Log "WARN: unreadable last_reset, resetting the clock"
        return $false
    }
    return ((Get-Date) -ge $due)
}

# ------------------------------ ingest ------------------------------------

function Clear-RepoWorkingTree {
    <# Empties the repository of everything except .git itself. #>
    foreach ($item in @(Get-ChildItem -LiteralPath $RepoDir -Force)) {
        if ($item.Name -eq '.git') { continue }
        if ($item.PSIsContainer) {
            if (-not (Remove-Tree -Path $item.FullName)) { return $false }
        } else {
            Remove-Item -LiteralPath $item.FullName -Force
        }
    }
    return $true
}

function Import-Snapshot {
    <# $true ingested, $false retry next run, $null permanently rejected. #>
    param(
        [Parameter(Mandatory)][System.IO.FileInfo]$Zip,
        [Parameter(Mandatory)][string]$Stamp,
        [Parameter(Mandatory)][hashtable]$State
    )

    $stage = Join-Path $StageRoot ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null

    try {
        if (-not (Expand-Snapshot -ArchivePath $Zip.FullName -Destination $stage)) { return $false }

        $tops = @(Get-ChildItem -LiteralPath $stage -Force)
        if ($tops.Count -ne 1 -or -not $tops[0].PSIsContainer) {
            Write-Log "    REJECT: archive does not contain exactly one top-level folder"
            return $null
        }
        $root = $tops[0]

        $renamed = Rename-NestedGitDirs -Root $root.FullName
        Write-Log "    renamed $renamed nested .git -> .git_archived"

        if (-not (Clear-RepoWorkingTree)) {
            Write-Log "    FAIL: could not clear the working tree"
            return $false
        }

        # The snapshot's contents become the repository's contents, so the
        # tree does not gain a redundant folder level.
        foreach ($item in @(Get-ChildItem -LiteralPath $root.FullName -Force)) {
            Move-Item -LiteralPath $item.FullName -Destination (Join-Path $RepoDir $item.Name)
        }
        Write-GitAttributes

        # Rebuild the project table from what this snapshot actually contains.
        # A project deleted upstream is absent here and drops out, which is
        # the behaviour a whole-folder snapshot is supposed to give.
        $present = @(Get-ChildItem -LiteralPath $RepoDir -Directory -Force |
                     Where-Object { $_.Name -ne '.git' })
        $projects = @{}
        foreach ($dir in $present) {
            $entry = $State.projects[$dir.Name]
            if (-not $entry) { $entry = @{ count = 0; first = $Stamp; last = $Stamp } }
            $entry.count = [int]$entry.count + 1
            $entry.last  = $Stamp
            if (-not $entry.first) { $entry.first = $Stamp }
            $projects[$dir.Name] = $entry
            Write-ProjectDayCount -Project $dir.Name -Entry $entry
        }
        $State.projects = $projects
        $State.snapshot_count = [int]$State.snapshot_count + 1
        $State.last_snapshot = $Stamp
        if (-not $State.last_reset) { $State.last_reset = (Get-Date).ToString('o') }

        Write-SummaryDayCount -State $State
        Write-State -State $State

        Write-Log "    staging $($present.Count) projects, this may take a while"
        if ((Invoke-Git @('add', '-A')) -ne 0) {
            Write-Log "    FAIL: git add"
            return $false
        }
        if (-not (Test-RepoDirty)) {
            Write-Log "    nothing changed, no commit"
        } else {
            if ((Invoke-Git @('commit', '-m', "snapshot $Stamp")) -ne 0) {
                Write-Log "    FAIL: git commit"
                return $false
            }
            Write-Log "    committed: snapshot $Stamp"
        }

        # Every commit is a full snapshot, so every one gets a tag.
        if (@(Get-GitOutput @('tag', '-l', $Stamp) | Where-Object { $_.Trim() }).Count) {
            Write-Log "    tag $Stamp already exists"
        } elseif ((Invoke-Git @('tag', '-a', $Stamp, '-m', "snapshot $Stamp")) -eq 0) {
            Write-Log "    tagged $Stamp"
        } else {
            Write-Log "    WARN: could not create tag $Stamp"
        }
        return $true
    } finally {
        Remove-Tree -Path $stage | Out-Null
    }
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
    if (-not (Test-Path -LiteralPath $SevenZip)) {
        Write-Log "FATAL: 7-Zip not found at $SevenZip"
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

    $pattern = '^(?<name>.+)_(?<stamp>\d{4}_\d{2}_\d{2}_\d{2}_\d{2})$'
    $candidates = @(
        Get-ChildItem -LiteralPath $WatchDir -File -Filter '*.7z' -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match $pattern } |
            Sort-Object { [regex]::Match($_.BaseName, $pattern).Groups['stamp'].Value }
    )

    if ($candidates.Count -eq 0) {
        Write-Log 'no archives to ingest'
        Write-Log '=== run end (exit 0) ==='
        exit 0
    }

    $state = Read-State

    # Reset before ingesting, not after: the fresh repository is then filled
    # by this run instead of sitting empty until the next archive arrives.
    if (Test-ResetDue -State $state) {
        Write-Log "reset: $ResetAfterDays days since $($state.last_reset)"
        Invoke-RepoReset -State $state
    }

    foreach ($zip in $candidates) {
        if (-not (Test-FileReady -File $zip)) {
            Write-Log "skipping $($zip.Name) - still being written or too fresh"
            continue
        }
        $stamp = [regex]::Match($zip.BaseName, $pattern).Groups['stamp'].Value
        Write-Log "ingesting $($zip.Name)"

        # One bad archive must not abort the whole run.
        try {
            $result = Import-Snapshot -Zip $zip -Stamp $stamp -State $state
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
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    Write-Log $_.ScriptStackTrace
    $script:ExitCode = 1
}

Write-Log "=== run end (exit $script:ExitCode) ==="
exit $script:ExitCode
