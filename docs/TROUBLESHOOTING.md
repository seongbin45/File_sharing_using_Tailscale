# 문제 해결

증상에서 출발해 원인을 찾도록 구성했습니다. 대부분의 답은 `C:\TempBackup\backup.log` 에 있습니다.

```cmd
type C:\TempBackup\backup.log
```

---

## 실행은 되는데 결과가 이상함

### 매번 같은 폴더만 백업됨

상태 파일 값이 실제 폴더명과 일치하지 않는 것입니다. 값 뒤에 공백이 붙었는지 확인하십시오.

```cmd
for /f "delims=" %A in (C:\Users\DiCiA\backup_state.txt) do @echo [%A]
```

`[A1-1_Project]` 처럼 대괄호가 딱 붙어야 정상입니다. `[A1-1_Project ]` 라면 상태 파일을
`echo %VAR% > file` 형태로 기록한 코드가 어딘가 남아 있는 것입니다. 리디렉션을 앞에 두어야
합니다 (`>"%STATE_FILE%" echo !TARGET_FOLDER!`).

로그의 `target folder: X (previous: Y)` 에서 `previous` 가 항상 비어 있다면 상태 파일을
읽지 못하는 것이니 `STATE_FILE` 경로와 쓰기 권한을 확인하십시오.

### 특정 폴더가 백업 대상에서 빠짐

- **이름이 점으로 시작** — 의도된 동작입니다. 최상위 점 폴더(`.idea` 등)는 건너뜁니다
- **폴더가 아니라 파일** — `BASE_DIR` 바로 아래의 낱개 파일은 순환 대상이 아닙니다.
  `dir /a-d "%BASE_DIR%"` 로 확인하십시오

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

고친 뒤에는 반드시 `findstr` 로 실제 반영을 확인하십시오. 원복은 원본 재복사가 가장 확실합니다.

### 괄호 블록 안에서 `%ERRORLEVEL%` 이 항상 낡은 값

블록 **파싱 시점**에 확장되기 때문입니다. `if not errorlevel 1` 을 쓰면 실행 시점 값을 봅니다.

### 배치에 한글을 넣었더니 로그가 깨짐

`.bat` 과 `.vbs` 는 ASCII 전용입니다. 콘솔 코드페이지(한글 Windows 는 949)와 파일 인코딩이
어긋나면 로그가 깨지고 최악의 경우 파싱이 틀어집니다. 설명은 문서에만 두십시오.

### 줄바꿈이 LF 인 배치 파일이 이상하게 동작

`goto` 나 괄호 블록 파싱이 틀어질 수 있습니다. 저장소의 `.gitattributes` 가 체크아웃 시
CRLF 를 강제하므로, git 을 거치지 않고 파일을 만들 때만 주의하면 됩니다.
