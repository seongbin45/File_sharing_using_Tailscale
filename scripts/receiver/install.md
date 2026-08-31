# 받는 쪽 설치 절차

무엇을 왜 이렇게 하는지는 [docs/RECEIVER.md](../../docs/RECEIVER.md) 를 먼저 보십시오.
보내는 쪽과 같은 구조(PowerShell → VBS 래퍼 → 작업 스케줄러 XML)이며, 관리자 권한 없이
설치됩니다.

**5단계(XML 인코딩 변환)를 빠뜨리면 스케줄 등록이 실패합니다.**

---

## 1단계 — 사전 확인

```cmd
git --version
tar --version
where wscript
powershell -NoProfile -Command "$PSVersionTable.PSVersion"
```

- **`git` 이 없으면 이 방식은 쓸 수 없습니다.** https://git-scm.com/download/win 에서 설치하고
  설치 중 "Add to PATH" 를 켜십시오.
- `tar` 는 압축 해제에 씁니다. 없으면 스크립트가 `Expand-Archive` 로 대체하지만 느립니다.
- PowerShell 은 5.1 이상이면 됩니다(Windows 10/11 기본).

수신 폴더에 실제로 zip 이 오는지도 확인하십시오.

```cmd
dir "%USERPROFILE%\Downloads\*.zip"
```

`A1-1_Project_2026_08_29_07_59.zip` 같은 이름이 보여야 합니다. 없다면 Taildrop 수신 설정부터
점검해야 하며, 이 스크립트로는 해결되지 않습니다.

---

## 2단계 — 파일 배치

```cmd
mkdir C:\Scripts 2>nul
git clone https://github.com/seongbin45/File_sharing_using_Tailscale.git C:\Scripts\src
copy /y C:\Scripts\src\scripts\receiver\ts_receive.ps1         C:\Scripts\
copy /y C:\Scripts\src\scripts\receiver\ts_receive_hidden.vbs  C:\Scripts\
copy /y C:\Scripts\src\scripts\receiver\ts_receive_task.xml    C:\Scripts\
```

`.ps1` 과 `.vbs` 는 **같은 폴더**에 있어야 합니다. VBS 가 자기 옆의 `.ps1` 을 찾습니다.

---

## 3단계 — 설정 조정

기본값 그대로도 동작합니다. 바꿀 만한 것은 두 가지입니다.

**보내는 쪽 프로젝트 개수를 알고 있다면** `$ExpectedProjectCount` 에 넣으십시오.
그러지 않으면 관리 디렉터리가 채워지는 동안 첫 태그가 이르게 찍힙니다.

```cmd
powershell -NoProfile -Command "$p='C:\Scripts\ts_receive.ps1'; $x=(Get-Content $p -Raw) -replace '\$ExpectedProjectCount = 0','$ExpectedProjectCount = 12'; Set-Content -Path $p -Value $x -Encoding UTF8"
```

**처리한 zip 을 남기고 싶다면** `$KeepProcessedZip` 을 `$true` 로 바꿉니다.
기본값은 삭제이며, 내용은 이미 git 에 들어가 있으므로 잃는 것은 없습니다.

고친 뒤에는 반드시 눈으로 확인하십시오.

```cmd
powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_receive.ps1' -Pattern '^\$(WatchDir|RepoDir|TagMode|ExpectedProjectCount|KeepProcessedZip) '"
```

원복은 원본 재복사가 가장 확실합니다.

```cmd
copy /y C:\Scripts\src\scripts\receiver\ts_receive.ps1 C:\Scripts\
```

---

## 4단계 — 수동 실행 테스트

스케줄에 걸기 전에 손으로 한 번 돌려서 결과를 확인합니다.

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ts_receive.ps1
echo EXITCODE=%ERRORLEVEL%
type C:\TempReceive\receive.log
```

확인할 것:

- `EXITCODE=0`
- 로그에 `ingesting <파일명>` → `committed: <프로젝트> @ <타임스탬프>`
- `.git` 이 든 프로젝트라면 `renamed N nested .git -> .git_archived` 도 함께

zip 이 아직 하나도 안 왔다면 `no archives to ingest` 만 찍히고 정상 종료합니다.

이어서 관리 디렉터리를 직접 확인합니다.

```cmd
set REPO=%USERPROFILE%\Downloads\PycharmProjects

dir "%REPO%"
type "%REPO%\Day_count.txt"
git -C "%REPO%" log --oneline
```

**가장 중요한 확인** — 프로젝트의 이력이 실제로 추적됐는지 봅니다. 아래 명령이 파일을
여러 개 뱉어야 합니다. 아무것도 안 나오면 `.git` 이 embedded repository 로 처리된 것이며,
그 경우 이력이 통째로 빠진 상태입니다.

```cmd
git -C "%REPO%" ls-files "A1-1_Project/.git_archived/*" | more
```

---

## 5단계 — XML 변환 (필수)

`schtasks /create /xml` 은 XML 이 **UTF-16(Unicode)** 이어야 합니다. git 으로 받은 파일은
UTF-8 이라 그대로 등록하면 실패합니다. 동시에 `__USERID__` 를 실제 계정으로 치환합니다.

```cmd
powershell -NoProfile -Command "$u = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $x = (Get-Content 'C:\Scripts\ts_receive_task.xml' -Raw) -replace '__USERID__', $u; Set-Content -Path 'C:\Scripts\ts_receive_task.xml' -Value $x -Encoding Unicode"

powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_receive_task.xml' -Pattern 'UserId'"
```

`<UserId>PC이름\계정명</UserId>` 이 두 줄 나와야 합니다. `%USERDOMAIN%` 을 쓰면 도메인에
가입되지 않은 PC 에서 `WORKGROUP\계정` 이 되어 등록이 거부되므로 `WindowsIdentity` 를 씁니다.

변환 후에는 UTF-16 이라 `findstr` 은 경고만 내고 아무것도 못 찾습니다. `Select-String` 을
쓰십시오.

---

## 6단계 — 작업 스케줄러 등록

```cmd
schtasks /create /tn "TailscaleProjectReceive" /xml "C:\Scripts\ts_receive_task.xml" /f
```

### 실패했다면 (fallback)

```cmd
schtasks /create /tn "TSReceive_Cycle" /tr "wscript.exe C:\Scripts\ts_receive_hidden.vbs" /sc MINUTE /mo 15 /f
schtasks /create /tn "TSReceive_Logon" /tr "wscript.exe C:\Scripts\ts_receive_hidden.vbs" /sc ONLOGON /f
```

`StartWhenAvailable`(놓친 실행 보충)과 `IgnoreNew`(중복 실행 방지)를 잃지만 동작은 합니다.

---

## 7단계 — 등록 검증

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectReceive').Triggers | Format-List"

schtasks /run /tn "TailscaleProjectReceive"
timeout /t 60
powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName 'TailscaleProjectReceive' | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns"
type C:\TempReceive\receive.log
```

- `LastTaskResult` 가 `0` (`1` = 치명적 실패, `2` = 일부 zip 실패)
- **화면에 창이 뜨지 않아야 합니다** (VBS 래퍼가 하는 일)
- 로그에 스케줄러가 돌린 회차가 남아야 합니다

`findstr /i "Last"` 같은 필터는 쓰지 마십시오. 한글 Windows 에서는 `마지막 결과` 로 출력되어
매칭되지 않습니다.

---

## 운영

```cmd
rem 상태 점검
type C:\TempReceive\receive.log
type "%USERPROFILE%\Downloads\PycharmProjects\Day_count.txt"

rem 일시 중지 / 재개 (시점 이동 중에는 반드시 중지)
schtasks /change /tn "TailscaleProjectReceive" /disable
schtasks /change /tn "TailscaleProjectReceive" /enable

rem 저장소 크기 확인과 정리
git -C "%USERPROFILE%\Downloads\PycharmProjects" count-objects -vH
git -C "%USERPROFILE%\Downloads\PycharmProjects" gc --aggressive --prune=now
```

특정 시점 복원과 `.git_archived` 되돌리기는 [docs/RECEIVER.md](../../docs/RECEIVER.md) 를
보십시오.

---

## 제거

```cmd
schtasks /delete /tn "TailscaleProjectReceive" /f
rd /s /q C:\TempReceive
```

관리 디렉터리(`Downloads\PycharmProjects`)는 백업 본체이므로 자동으로 지우지 않습니다.
필요할 때 직접 지우십시오.
