# SSH(cmd) 환경 설치 절차

비관리자 OpenSSH 세션의 cmd 만으로 배치할 수 있도록 구성했습니다. 편집기 없이 명령줄만으로
파일을 만들고 고치는 방법이 함께 들어 있습니다.

시스템이 무엇을 하고 왜 이런 구조인지는 [README](../README.md) 를 먼저 보십시오.

**아래 순서를 건너뛰지 마십시오. 특히 5단계(XML 인코딩 변환)를 빠뜨리면 스케줄 등록이 실패합니다.**

---

## 1단계 — 사전 확인

```cmd
dir "C:\Program Files\7-Zip\7z.exe"
where wscript
tailscale status
```

- **7-Zip 이 필수입니다.** 없으면 터미널만으로 설치하는 절차가
  [SETUP_TOOLS.md](../docs/SETUP_TOOLS.md) 에 있습니다. 설치해도 PATH 에는 등록되지 않으므로,
  스크립트는 `SEVENZIP` 설정값의 전체 경로로 직접 호출합니다.
  경로가 다르면 3단계에서 그 값을 고쳐야 합니다.
- **받는 PC 에도 7-Zip 이 필요합니다.** `tar` 와 `Expand-Archive` 는 `.7z` 를 못 읽습니다.
- `tailscale status` 출력에서 **전송할 대상 기기들의 이름 철자와 온라인 여부**를 확인하고
  메모해 두십시오. 3단계에서 그대로 입력해야 합니다.
  (이 저장소의 기본값은 `wisenesco-23031302`, `laptop-7gmpubqc`, `desktop-dvj3pqk`,
  `desktop-0g92n63` 이며, 원 작성자 환경의 기기 이름입니다.)
- **수신 기기 쪽에서 Taildrop 수신이 켜져 있어야 합니다.** 보내는 쪽이 성공해도 받는 쪽이
  `tailscale file get` 을 돌리지 않거나 자동 저장이 꺼져 있으면 파일은 도착하지 않습니다.

---

## 2단계 — 파일 배치

### 방법 A: git clone (권장)

```cmd
mkdir C:\Scripts 2>nul
git clone https://github.com/seongbin45/File_sharing_using_Tailscale.git C:\Scripts\src
copy /y C:\Scripts\src\scripts\ts_backup.bat        C:\Scripts\
copy /y C:\Scripts\src\scripts\ts_backup_hidden.vbs C:\Scripts\
copy /y C:\Scripts\src\scripts\ts_backup_task.xml   C:\Scripts\
copy /y C:\Scripts\src\scripts\ts_console.ps1       C:\Scripts\
```

`ts_console.ps1` 은 관리 화면입니다. 설치에 필수는 아니지만 이후 상태 확인이 훨씬 편합니다.

`C:\Scripts\src` 는 원본 사본으로 남겨 둡니다. 설정을 잘못 고쳤을 때 되돌리거나
(`copy /y C:\Scripts\src\scripts\ts_backup.bat C:\Scripts\`) 업데이트를 받을 때
(`cd /d C:\Scripts\src && git pull`) 씁니다.

### 방법 B: 붙여넣기 (git 이 없을 때)

cmd 는 줄바꿈을 명령 종료로 처리하므로 여러 줄 명령을 그대로 붙여넣을 수 없습니다.
**먼저 `powershell` 을 입력해 대화형 PowerShell 로 진입한 뒤** 붙여넣으십시오.

```powershell
mkdir C:\Scripts -Force
@'
<여기에 scripts/ts_backup.bat 내용을 그대로 붙여넣기>
'@ | Set-Content -Path C:\Scripts\ts_backup.bat -Encoding ASCII
```

`@'` ... `'@` 는 작은따옴표 here-string 이라 내부의 `%`, `!`, `"`, `>` 가 전부 리터럴로
처리됩니다. `ts_backup_hidden.vbs`, `ts_backup_task.xml` 도 같은 방식으로 만듭니다.
(XML 은 `-Encoding ASCII` 대신 5단계에서 다시 변환하므로 일단 ASCII 로 저장해도 됩니다.)

끝나면 `exit` 로 cmd 에 돌아옵니다.

---

## 3단계 — 설정 조정

`C:\Scripts\ts_backup.bat` 상단의 `CONFIG` 블록을 자기 환경에 맞게 고칩니다.
**최소한 `BASE_DIR`, `TARGETS`, `SEVENZIP` 세 개는 반드시 확인해야 합니다.**
각 값의 의미는 [README 의 설정 변수](../README.md#설정-변수), 이식 시 고칠 값 전체는
[PORTING.md](../docs/PORTING.md) 를 보십시오.

SSH 라 편집기를 쓰기 어렵다면 PowerShell 치환으로 한 줄씩 고칩니다.
`기존값` 자리에는 파일에 실제로 들어 있는 문자열을 넣습니다.

```cmd
powershell -NoProfile -Command "$p='C:\Scripts\ts_backup.bat'; $x=(Get-Content $p -Raw) -replace 'BASE_DIR=C:\\Users\\DiCiA\\PycharmProjects','BASE_DIR=D:\Work\Projects'; Set-Content -Path $p -Value $x -Encoding ASCII"
```

**찾는 쪽(첫 번째 인자)은 정규식이라 `\` 를 `\\` 로 써야 하고, 바꿀 값(두 번째 인자)은
그냥 문자열이라 `\` 를 하나만 씁니다.** 양쪽 다 `\\` 로 쓰면 경로에 백슬래시가 두 개씩
박힌 채로 저장됩니다.

고친 뒤에는 **반드시 눈으로 확인**하십시오. 치환이 안 먹었는데 모르고 넘어가는 것이
이 설치 과정에서 가장 흔한 실수입니다.

```cmd
powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_backup.bat' -Pattern '^set .(BASE_DIR|WORK_DIR|MIN_FREE_MB|SEVENZIP|TARGETS)='"
```

잘못 고쳤으면 원본을 다시 복사해 처음부터 하면 됩니다.

```cmd
copy /y C:\Scripts\src\scripts\ts_backup.bat C:\Scripts\
```

---

## 4단계 — 동작 테스트 (스케줄 등록 전)

프로젝트 폴더가 크면 한 회차에 수백 MB 가 오갑니다. 스케줄에 걸기 전에 아래 네 가지를
순서대로 확인하십시오. 각 항목이 무엇을 보증하는지는
[VERIFICATION.md](../docs/VERIFICATION.md) 에 실제 로그와 함께 정리돼 있습니다.

### 4-1. 압축만 (전송 없음)

`DRY_RUN` 을 `1` 로 바꾸고 실행합니다.

```cmd
powershell -NoProfile -Command "$p='C:\Scripts\ts_backup.bat'; $x=(Get-Content $p -Raw) -replace 'DRY_RUN=0','DRY_RUN=1'; Set-Content -Path $p -Value $x -Encoding ASCII"
C:\Scripts\ts_backup.bat
type C:\TempBackup\backup.log
```

로그에 `free space on work volume: <MB>`, `creating archive: ... (LZMA2 -mx=5)`,
`archive ready, <바이트수> bytes`, `DRY_RUN=1 - skipping transfer` 가 나와야 합니다.

### 4-2. 규모 판단

여기서 나온 **압축 크기와 압축에 걸린 시간**을 확인하십시오. 이 값이 하루 1회로
감당 가능한지가 이 설계의 전제입니다.

전송 시간은 압축 크기를 관측 속도(약 7 MB/s)로 나눈 값이 대략의 기준입니다.
6 GB 라면 15분 안팎입니다. 감당이 안 되면 `ts_backup_task.xml` 의 `<DaysInterval>` 을
늘리거나 `EXCLUDE_LIST` 를 도입해야 합니다.

압축 시간이 지나치게 길면 `SEVENZIP_LEVEL` 을 `1` 로 낮추십시오. 실측 기준으로 `-mx=1` 은
`-mx=5` 보다 3배 이상 빠르면서도 여전히 zip 보다 작습니다.

작업 볼륨 여유 공간이 압축 하나를 담을 만큼 있는지도 함께 보십시오.
`MIN_FREE_MB` 는 그 방어선입니다.

### 4-3. 실제 전송

`DRY_RUN` 을 `0` 으로 되돌리고 실행한 뒤, **수신 기기에서 파일이 실제로 도착했는지**
확인하십시오. 전송 성공 코드와 실제 도착은 별개입니다.

### 4-4. 롤백과 pending

`TARGETS` 의 첫 항목만 존재하지 않는 이름으로 바꿔 실행하면 로그에
`failed ... -> <가짜이름>` 다음 줄에 `sent ... -> <2순위 기기>` 가 나와야 합니다.

`TARGETS` 전체를 가짜 이름으로 바꾸면 종료 코드가 `2` 가 되고 압축 파일이 `pending` 에 남습니다.

```cmd
dir C:\TempBackup\pending
```

원복 후 다시 실행하면 새 압축보다 **먼저** `pending flushed` 가 찍히고 폴더가 비워집니다.

테스트가 끝나면 원본을 다시 복사해 설정을 되돌린 뒤 3단계의 실제 값으로 다시 고치고,
`C:\TempBackup\pending` 의 테스트 잔여물을 지우십시오.

---

## 5단계 — XML 변환 (필수)

`schtasks /create /xml` 은 XML 파일이 **UTF-16(Unicode)** 이어야 합니다.
git clone 이나 붙여넣기로 만든 파일은 UTF-8 이라 그대로 등록하면 실패합니다.
동시에 `__USERID__` 자리표시자를 실제 계정으로 치환합니다.

```cmd
powershell -NoProfile -Command "$u = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $x = (Get-Content 'C:\Scripts\ts_backup_task.xml' -Raw) -replace '__USERID__', $u; Set-Content -Path 'C:\Scripts\ts_backup_task.xml' -Value $x -Encoding Unicode"
```

계정명은 `WindowsIdentity` 에서 가져옵니다. `%USERDOMAIN%` 은 도메인에 가입되지 않은 PC에서
`WORKGROUP` 을 반환하고, `WORKGROUP\dicia` 같은 값은 실재하지 않는 계정이라 등록 시
`계정 이름과 보안 식별자 사이에 매핑이 이루어지지 않았습니다` 오류가 납니다.

치환 결과 확인 (변환 후에는 UTF-16 이라 `findstr` 은 경고만 내고 아무것도 못 찾습니다):

```cmd
powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_backup_task.xml' -Pattern 'UserId'"
```

`<UserId>PC이름\계정명</UserId>` 이 두 줄 나와야 합니다. 값이 잘못됐다면 원본을 다시
복사한 뒤(`copy /y C:\Scripts\src\scripts\ts_backup_task.xml C:\Scripts\`) 치환을 다시 하십시오.
이미 치환된 파일에 다시 치환을 걸어도 `__USERID__` 가 남아 있지 않아 아무 일도 일어나지 않습니다.

---

## 6단계 — 작업 스케줄러 등록

```cmd
schtasks /create /tn "TailscaleProjectBackup" /xml "C:\Scripts\ts_backup_task.xml" /f
```

### 이미 등록돼 있다면 — 반드시 다시 등록하십시오

**스크립트를 업데이트했는데 작업은 그대로 두는 것이 이 시스템에서 가장 위험한 실수입니다.**
트리거는 XML 에 들어 있고 `ts_backup.bat` 을 덮어써도 바뀌지 않습니다. 순환 설계 시절의
매시간 트리거가 남은 채 전체 스냅샷 로직이 들어가면 **6 GB 압축·전송이 한 시간마다** 돕니다.
`IgnoreNew` 가 중복 실행만 막을 뿐, 하루 종일 회차가 이어집니다.

먼저 지금 걸린 트리거가 무엇인지 보십시오.

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectBackup').Triggers | Format-List"
```

`Repetition` 이 있거나 트리거가 둘 이상이면 낡은 정의입니다. 재등록하는 동안 회차가
시작되지 않도록 먼저 끄고,

```cmd
schtasks /change /tn "TailscaleProjectBackup" /disable
```

5단계부터 다시 하십시오. `/f` 가 기존 정의를 덮어쓰고, 새 XML 의 `<Enabled>true</Enabled>`
가 위에서 끈 것을 다시 켭니다.

### 실패했다면 (fallback)

XML 스키마 오류나 권한 오류가 나면 명령줄로 등록합니다.

```cmd
schtasks /create /tn "TailscaleProjectBackup" /tr "wscript.exe C:\Scripts\ts_backup_hidden.vbs" /sc DAILY /st 04:00 /f
```

이 경우 `StartWhenAvailable`(놓친 실행 보충)과 `IgnoreNew`(중복 실행 방지)를 잃습니다.
PC 가 04:00 에 꺼져 있으면 그날 백업은 그냥 건너뛰게 되므로, XML 등록이 실패한 원인을
찾아 고치는 편이 낫습니다.

---

## 7단계 — 등록 검증

`schtasks /query /v` 는 표시 언어를 따라가고 이벤트 트리거를 `N/A` 로만 보여주므로,
확인은 PowerShell 쪽이 정확합니다.

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectBackup').Triggers | Format-List"
```

`CalendarTrigger` 하나가 `StartBoundary` 04:00 · `DaysInterval` 1 로 나와야 합니다.
보내는 쪽에는 부팅·절전 해제 트리거가 일부러 없습니다 — 한 회차가 무겁기 때문이며,
그 역할은 `StartWhenAvailable` 이 대신합니다.

```cmd
schtasks /run /tn "TailscaleProjectBackup"
timeout /t 90
powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName 'TailscaleProjectBackup' | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns"
type C:\TempBackup\backup.log
```

`findstr /i "Last"` 같은 필터는 쓰지 마십시오. 한글 Windows 에서는 `마지막 결과` 로 출력되어
아무것도 매칭되지 않습니다.

- `Last Result` 가 `0` (`1` = 압축 실패, `2` = 전송 실패로 pending 보관)
- **본체 모니터에 CMD 창이 뜨지 않아야 합니다** (VBS 래퍼가 하는 일)
- 6 GB 규모면 한 회차가 20분 안팎이므로 `timeout /t 90` 뒤에는 아직 실행 중일 수 있습니다.
  `LastTaskResult` 가 `267009` 면 진행 중이라는 뜻이니 로그를 보며 기다리십시오

---

## 제거

```cmd
schtasks /delete /tn "TailscaleProjectBackup" /f
rd /s /q C:\TempBackup
rd /s /q C:\Scripts
```
