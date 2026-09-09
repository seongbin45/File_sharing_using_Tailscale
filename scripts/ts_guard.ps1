<#
    ts_guard.ps1

    The only thing an SSH key restricted with command="..." can run.

    WHY THIS EXISTS

    The console used to send arbitrary PowerShell as -EncodedCommand. Pinning
    that key to a forced command would have bought nothing: the forced command
    would still have been "run whatever base64 arrives". A restriction that
    still permits arbitrary code is decoration.

    So the protocol changed. The console sends a VERB, this script holds the
    code, and the verb grammar has no argument that can carry code:

        whoami
        status
        log <1-500>
        task run | enable | disable

    Nothing here takes free text. That is the whole security property, and it
    is why "log" takes a bounded integer rather than a path, and why "task"
    takes no task name - the names are configuration here, on the machine
    being protected, not something the caller supplies.

    WHAT IT BUYS

    With this as a forced command plus no-pty, a stolen console cannot open a
    shell on this machine. It can read status and logs and start the backup
    that was going to run anyway. That is a very different loss.

    HOW IT IS INVOKED

    Restricted (normal):  sshd runs it; the verb arrives in SSH_ORIGINAL_COMMAND.
    Unrestricted:         the console sends this whole file followed by
                          "Invoke-Guard -Command '<verb>'", so machines with
                          nothing installed still work and there is exactly
                          one copy of this logic.

    Output is always base64 of UTF-8 JSON, so nothing depends on the console
    code page, on PowerShell's choice of stdout encoding, or on the log
    file's own encoding. This project has been bitten by all three.

    ENCODING: UTF-8 with BOM. Windows PowerShell 5.1 reads a BOM-less .ps1
    with the system code page and mangles every Korean string below.
#>

param([string]$Command = $env:SSH_ORIGINAL_COMMAND)

# ============================== CONFIG ====================================

# Task names live here, not in the request. A caller that could name the task
# could start any scheduled task on this machine.
$GuardSenderTask   = 'TailscaleProjectBackup'
$GuardReceiverTask = 'TailscaleProjectReceive'

$GuardScriptsDir   = 'C:\Scripts'
$GuardSenderWork   = 'C:\TempBackup'
$GuardReceiverWork = 'C:\TempReceive'

$GuardMaxLogLines  = 500

# ==========================================================================

# Never 'Stop': git and schtasks write ordinary output to stderr, and under
# 'Stop' a redirected stderr line becomes a terminating error.
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference    = 'SilentlyContinue'

function Emit($obj) {
    $json = $obj | ConvertTo-Json -Depth 6 -Compress
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

function Deny($reason) {
    Emit ([ordered]@{ ok = $false; denied = $true; reason = $reason })
}

# ------------------------------------------------------------- helpers

function TaskInfo($name) {
    $t = $null; $i = $null
    try {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $i = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
    } catch { return @{ registered = $false } }
    return [ordered]@{
        registered  = $true
        enabled     = ($t.State -ne 'Disabled')
        last_run    = $(if ($i.LastRunTime) { '{0:yyyy-MM-dd HH:mm}' -f $i.LastRunTime })
        last_result = $i.LastTaskResult
        next_run    = $(if ($i.NextRunTime) { '{0:yyyy-MM-dd HH:mm}' -f $i.NextRunTime })
        missed      = $i.NumberOfMissedRuns
    }
}

function LogTail($path, $count) {
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    return @(Get-Content -LiteralPath $path -Tail $count)
}

function FreeBytes($path) {
    try { return [int64](Get-Item -LiteralPath $path).PSDrive.Free } catch { return $null }
}

function BatVal($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*set\s+"' + [regex]::Escape($name) + '=(.*)"\s*$'
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Ps1Val($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*\$' + [regex]::Escape($name) + "\s*=\s*'([^']*)'"
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Ps1Num($path, $name) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*\$' + [regex]::Escape($name) + '\s*=\s*(\d+)'
    $m = Select-String -LiteralPath $path -Pattern $pattern | Select-Object -First 1
    if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    return $null
}

# ------------------------------------------------------------ which side

function SenderBat   { Join-Path $GuardScriptsDir 'ts_backup.bat' }
function ReceiverPs1 { Join-Path $GuardScriptsDir 'ts_receive.ps1' }

function IsSender   { Test-Path -LiteralPath (SenderBat) }
function IsReceiver { Test-Path -LiteralPath (ReceiverPs1) }

function GuardWorkDir {
    if (IsSender)   { $v = BatVal (SenderBat) 'WORK_DIR'; if ($v) { return $v } return $GuardSenderWork }
    if (IsReceiver) { $v = Ps1Val (ReceiverPs1) 'WorkDir'; if ($v) { return $v } return $GuardReceiverWork }
    return $null
}

function GuardLogPath {
    $work = GuardWorkDir
    if (-not $work) { return $null }
    if (IsSender) { return (Join-Path $work 'backup.log') }
    return (Join-Path $work 'receive.log')
}

function GuardTaskName {
    if (IsSender) { return $GuardSenderTask }
    if (IsReceiver) { return $GuardReceiverTask }
    return $null
}

# ------------------------------------------------------------- payloads

function SenderBlock {
    $bat  = SenderBat
    $work = BatVal $bat 'WORK_DIR'
    if (-not $work) { $work = $GuardSenderWork }
    $base = BatVal $bat 'BASE_DIR'

    $projects = $null
    if ($base -and (Test-Path -LiteralPath $base)) {
        $projects = @(Get-ChildItem -LiteralPath $base -Directory -Force |
                      Where-Object { $_.Name -notlike '.*' }).Count
    }

    $pendingDir = Join-Path $work 'pending'
    $pending = @()
    if (Test-Path -LiteralPath $pendingDir) {
        $pending = @(Get-ChildItem -LiteralPath $pendingDir -File -Filter '*.7z')
    }
    $targets = BatVal $bat 'TARGETS'

    return [ordered]@{
        base_dir      = $base
        projects      = $projects
        level         = (BatVal $bat 'SEVENZIP_LEVEL')
        dry_run       = ((BatVal $bat 'DRY_RUN') -eq '1')
        targets       = @($(if ($targets) { $targets -split '\s+' } else { @() }))
        free_bytes    = (FreeBytes $work)
        pending_count = $pending.Count
        pending_bytes = [int64](($pending | Measure-Object Length -Sum).Sum)
    }
}

function ReceiverBlock {
    $ps1  = ReceiverPs1
    $work = Ps1Val $ps1 'WorkDir'
    if (-not $work) { $work = $GuardReceiverWork }

    $watch      = Ps1Val $ps1 'WatchDir'
    $archive    = Ps1Val $ps1 'ArchiveRoot'
    $seven      = Ps1Val $ps1 'SevenZip'
    $resetAfter = Ps1Num $ps1 'ResetAfterDays'
    $repo = $(if ($watch) { Join-Path $watch 'PycharmProjects' })

    $waiting = @()
    if ($watch -and (Test-Path -LiteralPath $watch)) {
        $waiting = @(Get-ChildItem -LiteralPath $watch -File -Filter '*.7z')
    }

    $tags = @(); $gitSize = $null; $last = $null
    if ($repo -and (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
        $tags = @(& git -C $repo tag 2>$null | Where-Object { $_.Trim() })
        if ($tags.Count) { $last = @($tags | Sort-Object)[-1] }
        $counts = @(& git -C $repo count-objects -vH 2>$null)
        $line = @($counts | Where-Object { $_ -like 'size-pack:*' })
        if ($line.Count) { $gitSize = ($line[0] -replace '^size-pack:\s*', '') }
    }

    $snapCount = $null; $resetCount = $null; $dueDays = $null; $dueDate = $null
    $stateFile = $(if ($repo) { Join-Path $repo '.ts_state.json' })
    if ($stateFile -and (Test-Path -LiteralPath $stateFile)) {
        try {
            $st = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $snapCount  = [int]$st.snapshot_count
            $resetCount = [int]$st.reset_count
            if ($st.last_reset -and $resetAfter) {
                $due = ([datetime]$st.last_reset).AddDays($resetAfter)
                $dueDays = [int][Math]::Ceiling(($due - (Get-Date)).TotalDays)
                $dueDate = '{0:yyyy-MM-dd}' -f $due
            }
        } catch { }
    }

    $generations = 0
    if ($archive -and (Test-Path -LiteralPath $archive)) {
        $generations = @(Get-ChildItem -LiteralPath $archive -Directory).Count
    }

    return [ordered]@{
        watch_dir      = $watch
        repo_dir       = $repo
        waiting_count  = $waiting.Count
        waiting_bytes  = [int64](($waiting | Measure-Object Length -Sum).Sum)
        snapshots      = $tags.Count
        last_snapshot  = $last
        git_size       = $gitSize
        snapshot_count = $snapCount
        reset_count    = $resetCount
        reset_due_days = $dueDays
        reset_due_date = $dueDate
        seven_zip      = $(if ($seven) { Test-Path -LiteralPath $seven } else { $false })
        generations    = $generations
        free_bytes     = (FreeBytes $work)
    }
}

# ------------------------------------------------------------- dispatch

function Invoke-Guard {
    param([string]$Command)

    $cmd = ($Command | Out-String).Trim()

    # No command at all is what an interactive shell request looks like.
    # With no-pty in authorized_keys sshd has already refused the pty; this
    # refuses the session itself, and says why rather than hanging.
    if (-not $cmd) {
        Deny '이 키로는 셸을 열 수 없습니다. 허용된 동작: whoami, status, log <n>, task run|enable|disable'
        return
    }

    # One line only. A newline is how you would try to append a second
    # command, so it is rejected outright rather than parsed.
    if ($cmd -match '[\r\n]') {
        Deny '명령은 한 줄이어야 합니다.'
        return
    }

    switch -Regex ($cmd) {

        '^whoami$' {
            Emit ([ordered]@{
                ok         = $true
                whoami     = $env:USERNAME
                computer   = $env:COMPUTERNAME
                powershell = $PSVersionTable.PSVersion.ToString()
                scripts_present = (Test-Path -LiteralPath $GuardScriptsDir)
                guard      = $true
                role       = $(if (IsSender) { 'sender' } elseif (IsReceiver) { 'receiver' } else { '' })
            })
            return
        }

        '^status$' {
            $out = [ordered]@{
                ok    = $true
                guard = $true
                script_present = ((IsSender) -or (IsReceiver))
                task  = (TaskInfo (GuardTaskName))
            }
            if (IsSender)   { $out['sender']   = (SenderBlock) }
            if (IsReceiver) { $out['receiver'] = (ReceiverBlock) }
            $log = GuardLogPath
            $out['log'] = $(if ($log) { LogTail $log 12 } else { @() })
            Emit $out
            return
        }

        '^log (\d{1,4})$' {
            # A bounded integer, not a path. There is deliberately no way to
            # ask this for an arbitrary file.
            $n = [int]$Matches[1]
            if ($n -lt 1) { $n = 1 }
            if ($n -gt $GuardMaxLogLines) { $n = $GuardMaxLogLines }
            $log = GuardLogPath
            Emit ([ordered]@{
                ok = $true; guard = $true
                log = $(if ($log) { LogTail $log $n } else { @() })
            })
            return
        }

        '^task (run|enable|disable)$' {
            $verb = $Matches[1]
            $task = GuardTaskName
            if (-not $task) {
                Deny '이 기기에서 관리할 작업을 찾지 못했습니다.'
                return
            }
            # The task name comes from this file, never from the request.
            if ($verb -eq 'run') {
                $out = & schtasks /run /tn $task 2>&1 | ForEach-Object { "$_" }
            } else {
                $out = & schtasks /change /tn $task "/$verb" 2>&1 | ForEach-Object { "$_" }
            }
            Emit ([ordered]@{
                ok = ($LASTEXITCODE -eq 0); guard = $true
                code = $LASTEXITCODE; output = @($out)
            })
            return
        }

        default {
            Deny "허용되지 않은 명령입니다: $cmd"
            return
        }
    }
}

# Run when invoked as a file (sshd's forced command). Stays quiet when the
# body is sent inline with -EncodedCommand, where $PSCommandPath is empty and
# the console appends its own Invoke-Guard call.
if ($PSCommandPath) { Invoke-Guard -Command $Command }
