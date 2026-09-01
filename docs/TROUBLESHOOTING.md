# 문제 해결

> 시스템 구조와 용어는 [README](../README.md), 설치 절차는
> [install.md](../scripts/install.md) 를 참고하십시오.

증상에서 출발해 원인을 찾도록 구성했습니다. 대부분의 답은 `C:\TempBackup\backup.log` 에 있습니다.

```cmd
type C:\TempBackup\backup.log
```

---

## 실행은 되는데 결과가 이상함

### 여유 공간 부족으로 시작조차 하지 않음

로그에 `FATAL: need at least <N> MB free, refusing to start` 가 찍힙니다.
의도된 동작입니다 — 디스크를 채운 채 반쯤 만들어진 압축을 남기는 것보다 건너뛰는 편이
낫습니다. `WORK_DIR` 볼륨을 비우거나, 대상 크기에 맞게 `MIN_FREE_MB` 를 조정하십시오.

압축 하나가 통째로 올라갈 공간이 필요합니다.

### 한 회차가 너무 오래 걸림

대상 전체를 압축·전송하므로 규모에 비례합니다. 관측 기준으로 압축은 약 44 MB/s,
Taildrop 전송은 약 7 MB/s 입니다(6 GB 기준 압축 2.4분 + 전송 15분).

줄이려면 셋 중 하나입니다.

- `ts_backup_task.xml` 의 `<DaysInterval>` 을 늘려 빈도를 낮춤
- `SEVENZIP_LEVEL` 을 `1` 로 낮춤 — 실측 기준 `-mx=5` 보다 3배 이상 빠르고 zip 보다는 여전히 작음
- `EXCLUDE_LIST` 로 재생성 가능한 폴더를 제외 (`venv`, `node_modules`, `__pycache__`)
- 대상에서 대용량 산출물을 애초에 다른 곳으로 옮김

`.git` 과 `.env` 는 제외하지 마십시오. 백업의 목적 자체가 사라집니다.

### 로그에 `sent ... -> 기기명` 줄이 없는데 전송은 성공함

`:Log` 가 메시지를 직접 `echo` 하고 있는 것입니다. 메시지 안의 `>` 가 파싱 시점에
리디렉션 연산자로 승격되어, 로그 한 줄이 **기기 이름을 가진 파일로 빠져나갑니다.**

```cmd
dir C:\Users\DiCiA\wisenesco-23031302
```

이런 파일이 있으면 그 증상입니다. `:Log` 를 변수 경유 + 지연 확장으로 고쳐야 합니다
(커밋 `ea5bd94`).

### 롤백이 동작하지 않고 1순위 실패 후 그냥 끝남

`if <조건> <명령A> & <명령B>` 형태를 쓰면 `<명령B>` 가 **조건과 무관하게 항상** 실행됩니다.
전송 분기를 이 형태로 쓰면 1순위 실패 직후 정리 단계로 점프해 2~4순위가 죽습니다.
`:TrySend` 서브루틴 + `exit /b` 구조를 쓰십시오.

---

## 종료 코드로 판별하기

| 코드 | 의미 | 조치 |
|---|---|---|
| `0` | 정상 | — |
| `1` | 압축 실패 | `BASE_DIR` 경로, `WORK_DIR` 디스크 여유, `tar` 존재 여부 확인 |
| `2` | 전 기기 전송 실패 | 아래 참조 |

### `2` 가 반복됨

```cmd
tailscale status
dir C:\TempBackup\pending
```

- 대상 기기 이름 철자가 `TARGETS` 와 정확히 일치하는지
- 기기들이 온라인인지
- 로그에 남은 `tailscale` 자체 오류 메시지 확인 (`error looking up IP of ...` 등)

`pending` 은 프로젝트당 최신 1개, 3일 경과분 자동 삭제로 상한이 걸려 있어 무한히 쌓이지는
않습니다. 다만 그동안의 백업은 유실되므로 원인을 방치하지 마십시오.

---

## 작업 스케줄러

### 등록 시 `계정 이름과 보안 식별자 사이에 매핑이 이루어지지 않았습니다`

XML 의 `<UserId>` 가 실재하지 않는 계정입니다. `%USERDOMAIN%` 은 도메인에 가입되지 않은
PC 에서 `WORKGROUP` 을 반환하므로 `WORKGROUP\사용자` 같은 값이 들어갑니다.

원본을 다시 복사한 뒤 `WindowsIdentity` 로 치환하십시오.

```cmd
copy /y C:\Scripts\src\scripts\ts_backup_task.xml C:\Scripts\
powershell -NoProfile -Command "$u = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $x = (Get-Content 'C:\Scripts\ts_backup_task.xml' -Raw) -replace '__USERID__', $u; Set-Content -Path 'C:\Scripts\ts_backup_task.xml' -Value $x -Encoding Unicode"
```

### 등록 시 XML 파싱 오류

파일이 UTF-16 이 아닙니다. `schtasks /create /xml` 은 UTF-16(Unicode) 만 받습니다.
git 이나 curl 로 받은 파일은 UTF-8 이므로 위 치환 명령(`-Encoding Unicode`)을 반드시 거쳐야
합니다. 오류에 `(행,열)` 이 찍히면 그 위치의 요소가 스키마와 맞지 않는 것입니다.

### `findstr` 로 XML 내용을 확인할 수 없음

```
FINDSTR: 경고 - 입력 파일이 유니코드 형식입니다.
```

정상입니다. 오히려 인코딩 변환이 성공했다는 증거입니다. `Select-String` 을 쓰십시오.

```cmd
powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_backup_task.xml' -Pattern 'UserId'"
```

### `schtasks /query /v` 가 트리거를 `N/A` 로만 보여줌

`schtasks` 의 표시 한계입니다. 등록 자체는 정상일 수 있습니다. PowerShell 로 확인하십시오.

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectBackup').Triggers | Format-List"
```

### `findstr /i "Last"` 가 아무것도 못 찾음

한글 Windows 에서는 `마지막 결과` 로 출력되어 영문 키워드가 매칭되지 않습니다.
표시 언어에 의존하지 않는 방법을 쓰십시오.

```cmd
powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName 'TailscaleProjectBackup' | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns"
```

### 실행될 때 CMD 창이 화면에 뜸

작업의 동작(Action)이 `wscript.exe`(창 숨김) 가 아니라 `cmd.exe` 나 `.bat` 을 직접
가리키고 있을 가능성이 큽니다.

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectBackup').Actions | Format-List Execute, Arguments"
```

`Execute` 가 `wscript.exe` 여야 합니다.

### 작업이 등록됐는데 실행되지 않음

```cmd
powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName 'TailscaleProjectBackup' | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns"
```

- `NumberOfMissedRuns` 가 늘어남 → PC 가 꺼져 있었음. `StartWhenAvailable` 이 `True` 면
  다음 부팅 시 보충 실행됩니다
- `LastTaskResult` 가 `267009` → 현재 실행 중
- 매시간 트리거가 겹쳐도 `MultipleInstances: IgnoreNew` 로 중복 실행이 억제됩니다.
  앞선 실행이 오래 걸리면 그 회차는 건너뜁니다

---

## 스크립트를 고칠 때 자주 걸리는 것

### 셸에서 `set "TARGETS=..."` 를 해도 반영되지 않음

`ts_backup.bat` 은 실행할 때마다 상단에서 `TARGETS` 를 **자기가 다시 설정**합니다.
셸 환경 변수는 덮어써집니다. 배치 파일 자체를 고쳐야 합니다.

```cmd
powershell -NoProfile -Command "$p='C:\Scripts\ts_backup.bat'; $x=(Get-Content $p -Raw) -replace 'TARGETS=기존값','TARGETS=새값'; Set-Content -Path $p -Value $x -Encoding ASCII"
findstr /c:"set \"TARGETS=" C:\Scripts\ts_backup.bat
```

경로처럼 `\` 가 들어가는 값을 바꿀 때는, **찾는 쪽은 정규식이라 `\\` 로, 바꿀 값은 문자열이라
`\` 하나로** 씁니다. 양쪽 다 `\\` 로 쓰면 백슬래시가 두 개씩 박힌 채 저장됩니다.

고친 뒤에는 반드시 `findstr` 로 실제 반영을 확인하십시오. 원복은 원본 재복사가 가장 확실합니다.

### 괄호 블록 안에서 `%ERRORLEVEL%` 이 항상 낡은 값

블록 **파싱 시점**에 확장되기 때문입니다. `if not errorlevel 1` 을 쓰면 실행 시점 값을 봅니다.

### 배치에 한글을 넣었더니 로그가 깨짐

`.bat` 과 `.vbs` 는 ASCII 전용입니다. 콘솔 코드페이지(한글 Windows 는 949)와 파일 인코딩이
어긋나면 로그가 깨지고 최악의 경우 파싱이 틀어집니다. 설명은 문서에만 두십시오.

### 줄바꿈이 LF 인 배치 파일이 이상하게 동작

`goto` 나 괄호 블록 파싱이 틀어질 수 있습니다. 저장소의 `.gitattributes` 가 체크아웃 시
CRLF 를 강제하므로, git 을 거치지 않고 파일을 만들 때만 주의하면 됩니다.

---

## 7-Zip 관련

### `FATAL: 7-Zip not found at ...`

`SEVENZIP`(보내는 쪽) 또는 `$SevenZip`(받는 쪽) 경로에 `7z.exe` 가 없습니다.

```cmd
dir "C:\Program Files\7-Zip\7z.exe"
```

없으면 [SETUP_TOOLS.md](SETUP_TOOLS.md) 의 절차로 설치하고, 경로가 다르면 설정값을 고치십시오.

### `where 7z` 가 아무것도 못 찾음

**정상입니다.** 7-Zip 설치본은 자기를 PATH 에 등록하지 않습니다. 그래서 스크립트가
전체 경로로 직접 호출합니다. 작업 스케줄러는 맨 환경에서 돌기 때문에, 어떤 셸에서
`7z` 가 실행되더라도(예: conda 환경) 그것에 기대면 안 됩니다.

PowerShell 에서는 `where` 가 `Where-Object` 의 별칭이라 아예 다른 명령이 실행됩니다.
`where.exe 7z` 또는 `Get-Command 7z` 를 쓰십시오.

### 제외 목록이 무시됨

7-Zip 자체의 `-xr!이름` 스위치를 배치에 직접 적으면 **지연 확장이 `!` 를 먹어** 제외가
조용히 사라집니다. 그래서 이 스크립트는 목록 파일(`EXCLUDE_LIST`)만 받습니다.
목록 파일 경로에 공백이 있어도 인식되지 않으니 공백 없는 경로에 두십시오.

로그의 `excluding names listed in ...` 줄로 실제 적용 여부를 확인할 수 있습니다.
`WARN: EXCLUDE_LIST not found` 가 찍혔다면 경로가 틀린 것입니다.

### 받는 쪽에서 압축을 못 품

Windows 내장 `tar` 도 `Expand-Archive` 도 `.7z` 를 읽지 못합니다. 대체 경로가 없으므로
받는 PC 에도 7-Zip 이 반드시 설치되어 있어야 합니다.

### 받는 쪽이 `no archives to ingest` 만 찍고 아무것도 안 함

압축 파일은 분명히 도착했는데 스크립트가 못 찾는 경우입니다. **거의 항상 프로필이 다른
것입니다.** Taildrop 은 대화형으로 로그인한 사용자의 프로필에 저장하는데, SSH 세션이나
작업 스케줄러는 다른 계정으로 돌 수 있습니다.

로그 앞부분의 두 줄로 바로 확인됩니다.

```
running as WISENESCO-23031\Emergency (profile C:\Users\Emergency)
watching C:\Users\WISENESCO\Downloads -> C:\Users\WISENESCO\Downloads\PycharmProjects
```

`running as` 의 프로필과 `watching` 의 경로가 다른 계정을 가리켜도 **그 자체는 문제가
아닙니다.** 중요한 것은 `watching` 이 압축 파일이 실제로 떨어지는 곳인지입니다.

```powershell
Get-ChildItem C:\Users -Directory
Get-ChildItem 'C:\Users\<확인한사용자>\Downloads' -Filter *.7z
```

`$WatchDir` 는 이런 이유로 `$env:USERPROFILE` 에서 유도하지 않고 절대 경로로 적게 되어
있습니다. 실제 위치에 맞게 고치십시오.

다른 계정의 프로필을 읽고 쓰려면 권한이 필요합니다. 확인:

```powershell
try {
    New-Item -ItemType File -Path 'C:\Users\<사용자>\Downloads\_writetest.tmp' -Force -ErrorAction Stop | Out-Null
    Remove-Item 'C:\Users\<사용자>\Downloads\_writetest.tmp' -Force
    "WRITE = OK"
} catch { "WRITE = FAIL : $($_.Exception.Message)" }
```

`FAIL` 이면 경로만 고쳐서는 안 됩니다. 작업을 해당 계정으로 등록하거나, Taildrop 수신
폴더를 두 계정이 모두 접근 가능한 공용 경로로 옮겨야 합니다.
