<#
    ts_console.ps1

    Terminal dashboard for the Tailscale project backup.

    Run it on either machine. It works out which side it is looking at from
    which scripts are present, reads their CONFIG blocks so the values shown
    are the ones actually in effect, and offers the handful of actions that
    are safe to trigger by hand.

    Requires nothing beyond Windows PowerShell 5.1. That is the point: the
    whole system installs with no runtime of its own, and a dashboard is not
    a good enough reason to add one.

    Nothing here deletes or resets anything.

    ENCODING: this file is UTF-8 *with* a BOM, and must stay that way.
    Windows PowerShell 5.1 decodes a BOM-less .ps1 with the system code page,
    which would turn every label below into mojibake. .gitattributes keeps the
    line endings; the BOM is part of the content and survives a clone.
#>

[CmdletBinding()]
param(
    # Seconds between automatic refreshes. Any key interrupts the wait.
    [int]$RefreshSeconds = 10,

    # Print one frame and exit instead of looping. Set automatically when the
    # console cannot be read from, so the dashboard still produces something
    # useful from "ssh host powershell -File ts_console.ps1".
    [switch]$Once
)

$ErrorActionPreference = 'Stop'

# ============================== CONFIG ====================================

$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$SenderBat     = Join-Path $ScriptDir 'ts_backup.bat'
$ReceiverPs1   = Join-Path $ScriptDir 'ts_receive.ps1'
$SenderTask    = 'TailscaleProjectBackup'
$ReceiverTask  = 'TailscaleProjectReceive'

# ==========================================================================

# ------------------------------ native calls -------------------------------

function Invoke-Native {
    <#
        Runs an external command and returns @{ Code; Output }.

        $ErrorActionPreference = 'Stop' must not be in effect around the call.
        Windows PowerShell turns anything a native command writes to stderr
        into an ErrorRecord once stderr is redirected, and under 'Stop' that
        becomes a terminating error. git writes ordinary progress to stderr,
        and schtasks reports every failure there - so without this the
        dashboard would die precisely when it had something worth showing.
    #>
    param([Parameter(Mandatory)][string]$Command, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Command @Arguments 2>&1
        return @{ Code = $LASTEXITCODE; Output = @($output | ForEach-Object { "$_" }) }
    } catch {
        return @{ Code = -1; Output = @("$($_.Exception.Message)") }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-GitLines {
    <# Returns the command's output lines, or an empty array if it failed. #>
    param([Parameter(Mandatory)][string]$RepoDir, [Parameter(Mandatory)][string[]]$GitArgs)
    $result = Invoke-Native -Command 'git' -Arguments (@('-C', $RepoDir) + $GitArgs)
    if ($result.Code -ne 0) { return @() }
    return $result.Output
}

# --------------------------- console capability ----------------------------
# Reading keys needs a real console. Over SSH that depends on how the command
# was invoked: an interactive shell has one, "ssh host powershell -File ..."
# has its stdin redirected and every ReadKey throws. Probe once, up front,
# rather than letting the first keypress take the script down.

$script:CanReadKeys = $false
try {
    if (-not [Console]::IsInputRedirected) {
        $null = [Console]::KeyAvailable
        $script:CanReadKeys = $true
    }
} catch { $script:CanReadKeys = $false }

# ------------------------------ text width --------------------------------
# Korean, Japanese and Chinese characters occupy two columns in a terminal
# while counting as one character in .NET. Padding with -f or PadRight lines
# up the numbers and not the display, which is why the Day_count.txt table
# looks ragged. Every column here is measured in display width instead.

function Get-DisplayWidth {
    param([string]$Text)
    if (-not $Text) { return 0 }
    $width = 0
    foreach ($ch in $Text.ToCharArray()) {
        $c = [int]$ch
        $wide = ($c -ge 0x1100) -and (
            ($c -le 0x115F) -or
            ($c -eq 0x2329 -or $c -eq 0x232A) -or
            ($c -ge 0x2E80 -and $c -le 0xA4CF -and $c -ne 0x303F) -or
            ($c -ge 0xAC00 -and $c -le 0xD7A3) -or
            ($c -ge 0xF900 -and $c -le 0xFAFF) -or
            ($c -ge 0xFE30 -and $c -le 0xFE6F) -or
            ($c -ge 0xFF00 -and $c -le 0xFF60) -or
            ($c -ge 0xFFE0 -and $c -le 0xFFE6)
        )
        if ($wide) { $width += 2 } else { $width += 1 }
    }
    return $width
}

function Format-Fixed {
    <# Pads or truncates $Text to exactly $Width display columns. #>
    param([string]$Text, [int]$Width)
    if ($null -eq $Text) { $Text = '' }
    $w = Get-DisplayWidth $Text
    if ($w -le $Width) { return $Text + (' ' * ($Width - $w)) }
    if ($Width -le 1) { return '~'.Substring(0, [Math]::Max($Width, 0)) }
    $out = ''
    $used = 0
    foreach ($ch in $Text.ToCharArray()) {
        $cw = Get-DisplayWidth ([string]$ch)
        if ($used + $cw -gt $Width - 1) { break }
        $out += $ch
        $used += $cw
    }
    $pad = $Width - $used - 1
    if ($pad -lt 0) { $pad = 0 }
    return $out + '~' + (' ' * $pad)
}

# ------------------------------ drawing -----------------------------------

$script:Buffer = New-Object System.Collections.Generic.List[string]

function Get-ConsoleWidth {
    try { [Math]::Max(60, [Math]::Min($Host.UI.RawUI.WindowSize.Width - 1, 120)) }
    catch { 80 }
}

function Add-Line { param([string]$Text = '') ; $script:Buffer.Add($Text) }

function Add-Rule {
    param([string]$Title = '')
    $w = Get-ConsoleWidth
    if ($Title) {
        $t = " $Title "
        $pad = $w - (Get-DisplayWidth $t) - 3
        if ($pad -lt 0) { $pad = 0 }
        Add-Line ('--' + $t + ('-' * $pad))
    } else {
        Add-Line ('-' * $w)
    }
}

function Add-Field {
    param([string]$Label, [string]$Value)
    Add-Line ('  ' + (Format-Fixed $Label 22) + ' ' + $Value)
}

function Get-ConsoleHeight {
    try { [Math]::Max(20, $Host.UI.RawUI.WindowSize.Height) } catch { 40 }
}

function Show-Buffer {
    # Cursor home plus right-padding rather than Clear-Host: over SSH a full
    # clear every refresh flickers badly.
    #
    # Home is the top of the visible window, not (0,0). SetCursorPosition
    # addresses the screen *buffer*, so once the buffer has scrolled - which it
    # does the moment a frame is taller than the window - row 0 is somewhere
    # far above what the operator can see, and the display appears to freeze.
    #
    # The frame is also clipped to the window height for the same reason: a
    # frame that does not fit scrolls the buffer on every refresh, and then no
    # fixed home position is correct.
    $w = Get-ConsoleWidth
    $h = Get-ConsoleHeight
    $lines = @($script:Buffer)
    $script:Buffer.Clear()

    # One-shot output goes out whole and unpadded: it is being read from a
    # transcript or piped somewhere, not redrawn in place.
    if ($script:PlainOutput) {
        foreach ($line in $lines) { Write-Host $line }
        return
    }

    # Leave two rows: one for the trailing blanks, one for the prompt line the
    # shell writes when the script exits.
    $max = $h - 2
    if ($lines.Count -gt $max) {
        $lines = @($lines[0..($max - 2)]) + @("  ... $($lines.Count - $max + 1) 줄 생략 (창을 키우십시오)")
    }

    $top = 0
    try { $top = [Console]::WindowTop } catch { $top = 0 }
    $homed = $false
    try { [Console]::SetCursorPosition(0, $top); $homed = $true } catch { Clear-Host }

    foreach ($line in $lines) { Write-Host (Format-Fixed $line $w) }

    # Erase whatever a previous, longer frame left behind.
    if ($homed) {
        $blanks = [Math]::Min(6, [Math]::Max(0, $max - $lines.Count))
        for ($i = 0; $i -lt $blanks; $i++) { Write-Host (' ' * $w) }
    }
}

function Format-Bytes {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return '{0:N2} GB' -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return '{0:N1} MB' -f ($Bytes / 1MB) }
    if ($Bytes -ge 1KB) { return '{0:N0} KB' -f ($Bytes / 1KB) }
    return "$Bytes B"
}

# ------------------------------ config reading -----------------------------
# The scripts themselves are the source of truth. Reading their CONFIG blocks
# means the dashboard cannot drift out of sync with what actually runs.

function Get-BatValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $pattern = '^\s*set\s+"' + [regex]::Escape($Name) + '=(.*)"\s*$'
    $m = Select-String -LiteralPath $Path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Get-Ps1Value {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $pattern = '^\s*\$' + [regex]::Escape($Name) + "\s*=\s*'([^']*)'"
    $m = Select-String -LiteralPath $Path -Pattern $pattern | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

# ------------------------------ task state ---------------------------------

function Get-TaskState {
    param([string]$TaskName)
    $state = @{ Registered = $false; Enabled = $null; LastRun = $null
                LastResult = $null; NextRun = $null; Missed = $null }
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
        $state.Registered = $true
        $state.Enabled    = ($task.State -ne 'Disabled')
        $state.LastRun    = $info.LastRunTime
        $state.LastResult = $info.LastTaskResult
        $state.NextRun    = $info.NextRunTime
        $state.Missed     = $info.NumberOfMissedRuns
    } catch { }
    return $state
}

function Format-TaskResult {
    param($Code)
    if ($null -eq $Code) { return '-' }
    switch ($Code) {
        0       { '0  정상' }
        1       { '1  치명적 실패' }
        2       { '2  일부 실패 (pending)' }
        267009  { '실행 중' }
        267011  { '아직 실행된 적 없음' }
        default { "$Code" }
    }
}

function Add-TaskSection {
    param([string]$TaskName, [hashtable]$State)
    if (-not $State.Registered) {
        Add-Field '작업 스케줄러' "미등록  ($TaskName)"
        return
    }
    Add-Field '작업 스케줄러' $(if ($State.Enabled) { '사용' } else { '일시 중지됨' })
    Add-Field '마지막 실행' $(if ($State.LastRun) { '{0:yyyy-MM-dd HH:mm}' -f $State.LastRun } else { '-' })
    Add-Field '마지막 결과' (Format-TaskResult $State.LastResult)
    Add-Field '다음 실행' $(if ($State.NextRun) { '{0:yyyy-MM-dd HH:mm}' -f $State.NextRun } else { '-' })
    Add-Field '놓친 실행' "$($State.Missed)"
}

function Get-LogTail {
    param([string]$Path, [int]$Count = 6)
    if (-not (Test-Path -LiteralPath $Path)) { return @('(로그 없음)') }
    try { return @(Get-Content -LiteralPath $Path -Tail $Count) }
    catch { return @('(로그를 읽을 수 없음)') }
}

# ------------------------------ sender panel -------------------------------

function Add-SenderPanel {
    Add-Rule '보내는 쪽'

    $baseDir  = Get-BatValue $SenderBat 'BASE_DIR'
    $workDir  = Get-BatValue $SenderBat 'WORK_DIR'
    $targets  = Get-BatValue $SenderBat 'TARGETS'
    $level    = Get-BatValue $SenderBat 'SEVENZIP_LEVEL'
    $dryRun   = Get-BatValue $SenderBat 'DRY_RUN'
    if (-not $workDir) { $workDir = 'C:\TempBackup' }

    Add-Field '대상' $(if ($baseDir) { $baseDir } else { '(ts_backup.bat 에서 BASE_DIR 을 읽지 못했습니다)' })
    if ($baseDir -and (Test-Path -LiteralPath $baseDir)) {
        $projects = @(Get-ChildItem -LiteralPath $baseDir -Directory -Force -ErrorAction SilentlyContinue |
                      Where-Object { $_.Name -notlike '.*' })
        Add-Field '프로젝트' "$($projects.Count) 개"
    } else {
        Add-Field '프로젝트' '대상 경로 없음'
    }
    Add-Field '압축 수준' "7z -mx=$level$(if ($dryRun -eq '1') { '   [DRY_RUN 켜짐 - 전송 안 함]' })"
    Add-Field '전송 대상' $targets

    try {
        $free = (Get-Item -LiteralPath $workDir).PSDrive.Free
        Add-Field '작업 볼륨 여유' (Format-Bytes $free)
    } catch { Add-Field '작업 볼륨 여유' '-' }

    $pendingDir = Join-Path $workDir 'pending'
    if (Test-Path -LiteralPath $pendingDir) {
        $pending = @(Get-ChildItem -LiteralPath $pendingDir -File -Filter '*.7z' -ErrorAction SilentlyContinue)
        $size = ($pending | Measure-Object Length -Sum).Sum
        if ($pending.Count -gt 0) {
            Add-Field 'pending' "$($pending.Count) 개  $(Format-Bytes $size)   <- 전송 실패분"
        } else {
            Add-Field 'pending' '없음'
        }
    }

    Add-TaskSection $SenderTask (Get-TaskState $SenderTask)

    Add-Line
    Add-Line '  최근 로그'
    foreach ($line in (Get-LogTail (Join-Path $workDir 'backup.log'))) {
        Add-Line ('    ' + $line)
    }
    Add-Line
}

# ------------------------------ receiver panel -----------------------------

function Add-ReceiverPanel {
    Add-Rule '받는 쪽'

    $watchDir = Get-Ps1Value $ReceiverPs1 'WatchDir'
    $workDir  = Get-Ps1Value $ReceiverPs1 'WorkDir'
    $archive  = Get-Ps1Value $ReceiverPs1 'ArchiveRoot'
    $sevenZip = Get-Ps1Value $ReceiverPs1 'SevenZip'
    if (-not $workDir) { $workDir = 'C:\TempReceive' }
    $repoDir = if ($watchDir) { Join-Path $watchDir 'PycharmProjects' } else { $null }

    Add-Field '감시 폴더' $watchDir
    Add-Field '관리 저장소' $repoDir

    if ($watchDir -and (Test-Path -LiteralPath $watchDir)) {
        $waiting = @(Get-ChildItem -LiteralPath $watchDir -File -Filter '*.7z' -ErrorAction SilentlyContinue)
        $size = ($waiting | Measure-Object Length -Sum).Sum
        Add-Field '대기 중인 압축' $(if ($waiting.Count) { "$($waiting.Count) 개  $(Format-Bytes $size)" } else { '없음' })
    }

    if ($repoDir -and (Test-Path -LiteralPath (Join-Path $repoDir '.git'))) {
        $tags = @(Get-GitLines $repoDir @('tag') | Where-Object { $_.Trim() })
        Add-Field '스냅샷' "$($tags.Count) 개"
        if ($tags.Count) {
            Add-Field '최근 스냅샷' (@($tags | Sort-Object)[-1])
        }
        $counts = Get-GitLines $repoDir @('count-objects', '-vH')
        $pack = @($counts | Where-Object { $_ -like 'size-pack:*' }) -replace '^size-pack:\s*', ''
        if ($pack) { Add-Field '.git 크기' ($pack | Select-Object -First 1) }

        $stateFile = Join-Path $repoDir '.ts_state.json'
        if (Test-Path -LiteralPath $stateFile) {
            try {
                $st = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
                Add-Field '누적 스냅샷' "$($st.snapshot_count)"
                Add-Field '초기화 횟수' "$($st.reset_count)"
                if ($st.last_reset) {
                    $days = Get-Ps1Value $ReceiverPs1 'ResetAfterDays'
                    if (-not $days) {
                        $m = Select-String -LiteralPath $ReceiverPs1 -Pattern '^\s*\$ResetAfterDays\s*=\s*(\d+)' | Select-Object -First 1
                        if ($m) { $days = $m.Matches[0].Groups[1].Value }
                    }
                    if ($days) {
                        $due = ([datetime]$st.last_reset).AddDays([int]$days)
                        $left = [int][Math]::Ceiling(($due - (Get-Date)).TotalDays)
                        Add-Field '초기화까지' "$left 일  ($('{0:yyyy-MM-dd}' -f $due))"
                    }
                }
            } catch { Add-Field '상태 파일' '읽을 수 없음' }
        }
    } else {
        Add-Field '스냅샷' '저장소가 아직 없음'
    }

    if ($sevenZip) {
        Add-Field '7-Zip' $(if (Test-Path -LiteralPath $sevenZip) { '있음' } else { "없음!  $sevenZip" })
    }
    if ($archive) {
        $gens = @(Get-ChildItem -LiteralPath $archive -Directory -ErrorAction SilentlyContinue)
        Add-Field '보관 세대' "$($gens.Count) 개  ($archive)"
    }

    Add-TaskSection $ReceiverTask (Get-TaskState $ReceiverTask)

    Add-Line
    Add-Line '  최근 로그'
    foreach ($line in (Get-LogTail (Join-Path $workDir 'receive.log'))) {
        Add-Line ('    ' + $line)
    }
    Add-Line
}

# ------------------------------ sub screens --------------------------------

function Show-Pager {
    param([string[]]$Lines, [string]$Title)
    Clear-Host
    Write-Host "== $Title ==" -ForegroundColor Cyan
    Write-Host
    $w = Get-ConsoleWidth
    foreach ($line in $Lines) { Write-Host (Format-Fixed $line $w) }
    Write-Host
    if ($script:CanReadKeys) {
        Write-Host '아무 키나 누르면 돌아갑니다...' -ForegroundColor DarkGray
        try { [void][Console]::ReadKey($true) } catch { }
    }
    Clear-Host
}

function Show-FullLog {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        Show-Pager @("로그가 없습니다: $Path") '로그'
        return
    }
    Show-Pager (Get-Content -LiteralPath $Path -Tail 40) "로그 (마지막 40줄) - $Path"
}

function Show-Snapshots {
    param([string]$RepoDir)
    if (-not $RepoDir -or -not (Test-Path -LiteralPath (Join-Path $RepoDir '.git'))) {
        Show-Pager @('관리 저장소가 아직 없습니다.') '스냅샷'
        return
    }
    $lines = @(Get-GitLines $RepoDir @('log', '--oneline', '--decorate', '-30'))
    if (-not $lines.Count) { $lines = @('(아직 커밋이 없습니다)') }
    $lines += ''
    $lines += '복원: git -C "' + $RepoDir + '" checkout <태그>'
    $lines += '복귀: git -C "' + $RepoDir + '" checkout -'
    $lines += '주의: 시점 이동 중에는 스케줄 실행을 일시 중지하십시오.'
    Show-Pager $lines '스냅샷 (최근 30개)'
}

function Invoke-TaskRun {
    param([string]$TaskName)
    $r = Invoke-Native -Command 'schtasks' -Arguments @('/run', '/tn', $TaskName)
    Show-Pager (@("작업 실행을 요청했습니다: $TaskName", '') + $r.Output + @(
        '', '한 회차는 규모에 따라 십수 분에서 수십 분이 걸립니다.',
        '진행 상황은 로그(L)로 확인하십시오.')) '즉시 실행'
}

function Invoke-TaskToggle {
    param([string]$TaskName, [bool]$CurrentlyEnabled)
    $verb = if ($CurrentlyEnabled) { '/disable' } else { '/enable' }
    $r = Invoke-Native -Command 'schtasks' -Arguments @('/change', '/tn', $TaskName, $verb)
    Show-Pager (@("$TaskName $(if ($CurrentlyEnabled) { '일시 중지' } else { '재개' })", '') + $r.Output) '스케줄 전환'
}

# ------------------------------- main --------------------------------------

$hasSender   = Test-Path -LiteralPath $SenderBat
$hasReceiver = Test-Path -LiteralPath $ReceiverPs1

if (-not $hasSender -and -not $hasReceiver) {
    Write-Host "ts_backup.bat 도 ts_receive.ps1 도 $ScriptDir 에 없습니다." -ForegroundColor Red
    Write-Host '이 스크립트는 둘 중 하나와 같은 폴더에 두어야 합니다.'
    exit 1
}

if (-not $script:CanReadKeys -and -not $Once) {
    Write-Host '이 세션에서는 키 입력을 읽을 수 없어 한 번만 출력합니다.' -ForegroundColor DarkYellow
    Write-Host '대화형으로 쓰려면 SSH 로 접속한 뒤 셸에서 직접 실행하십시오:' -ForegroundColor DarkGray
    Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ts_console.ps1' -ForegroundColor DarkGray
    Write-Host
    $Once = $true
}

$script:PlainOutput = [bool]$Once
if (-not $Once) { Clear-Host }
$running = $true

while ($running) {
    $senderState   = if ($hasSender)   { Get-TaskState $SenderTask }   else { $null }
    $receiverState = if ($hasReceiver) { Get-TaskState $ReceiverTask } else { $null }

    Add-Line ("  Tailscale 프로젝트 백업   |   $env:COMPUTERNAME\$env:USERNAME   |   " +
              (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Add-Rule

    if ($hasSender)   { Add-SenderPanel }
    if ($hasReceiver) { Add-ReceiverPanel }

    Add-Rule
    if (-not $Once) {
        $keys = @('[R] 즉시 실행', '[P] 일시중지/재개', '[L] 로그', '[F] 새로고침')
        if ($hasReceiver) { $keys += '[S] 스냅샷' }
        $keys += '[Q] 종료'
        Add-Line ('  ' + ($keys -join '   '))
        Add-Line ("  ${RefreshSeconds}초마다 자동 새로고침")
    }

    Show-Buffer

    if ($Once) { break }

    # Wait for a key, but wake up on the refresh interval so the numbers stay
    # live without the operator having to do anything.
    $deadline = (Get-Date).AddSeconds($RefreshSeconds)
    $key = $null
    while ((Get-Date) -lt $deadline) {
        try {
            if ([Console]::KeyAvailable) { $key = [Console]::ReadKey($true); break }
        } catch {
            # The console went away underneath us (the SSH session dropped).
            $running = $false
            break
        }
        Start-Sleep -Milliseconds 150
    }
    if (-not $running) { break }
    if (-not $key) { continue }

    # One machine may host both sides; prefer whichever is present.
    $activeTask  = if ($hasSender) { $SenderTask } else { $ReceiverTask }
    $activeState = if ($hasSender) { $senderState } else { $receiverState }

    switch ($key.Key) {
        'Q' { $running = $false }
        'F' { }
        'R' {
            if ($activeState.Registered) { Invoke-TaskRun $activeTask }
            else { Show-Pager @("$activeTask 이(가) 등록되어 있지 않습니다.") '즉시 실행' }
        }
        'P' {
            if ($activeState.Registered) { Invoke-TaskToggle $activeTask $activeState.Enabled }
            else { Show-Pager @("$activeTask 이(가) 등록되어 있지 않습니다.") '스케줄 전환' }
        }
        'L' {
            # Fall back to the documented defaults rather than letting a
            # CONFIG block the regex could not read blow up Join-Path.
            $log = if ($hasSender) {
                $d = Get-BatValue $SenderBat 'WORK_DIR'
                if (-not $d) { $d = 'C:\TempBackup' }
                Join-Path $d 'backup.log'
            } else {
                $d = Get-Ps1Value $ReceiverPs1 'WorkDir'
                if (-not $d) { $d = 'C:\TempReceive' }
                Join-Path $d 'receive.log'
            }
            Show-FullLog $log
        }
        'S' {
            if ($hasReceiver) {
                $watchDir = Get-Ps1Value $ReceiverPs1 'WatchDir'
                if ($watchDir) { Show-Snapshots (Join-Path $watchDir 'PycharmProjects') }
                else { Show-Pager @('ts_receive.ps1 에서 WatchDir 을 읽지 못했습니다.') '스냅샷' }
            }
        }
        default { }
    }
}

if (-not $Once) {
    Clear-Host
    Write-Host '종료했습니다.'
}
