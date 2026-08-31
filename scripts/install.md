# SSH(cmd) 환경 설치 절차

비관리자 OpenSSH 세션의 cmd 만으로 배치할 수 있도록 구성했습니다. 편집기 없이 명령줄만으로
파일을 만들고 고치는 방법이 함께 들어 있습니다.

시스템이 무엇을 하고 왜 이런 구조인지는 [README](../README.md) 를 먼저 보십시오.

**아래 순서를 건너뛰지 마십시오. 특히 5단계(XML 인코딩 변환)를 빠뜨리면 스케줄 등록이 실패합니다.**

---

## 1단계 — 사전 확인

```cmd
tar --version
where wscript
tailscale status
```

- `tar` 는 Windows 10 1803 이상에 기본 포함되어 있습니다. 없으면 이 방식 자체를 쓸 수 없습니다.
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
```

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
**최소한 `BASE_DIR`, `STATE_FILE`, `TARGETS` 세 개는 반드시 확인해야 합니다.**
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
powershell -NoProfile -Command "Select-String -Path 'C:\Scripts\ts_backup.bat' -Pattern '^set .(BASE_DIR|STATE_FILE|WORK_DIR|TARGETS)='"
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

로그에 `archive ready, <바이트수> bytes` 와 `DRY_RUN=1 - skipping transfer` 가 나와야 합니다.

### 4-2. 순환

```cmd
C:\Scripts\ts_backup.bat
C:\Scripts\ts_backup.bat
for /f "delims=" %A in (C:\Users\DiCiA\backup_state.txt) do @echo [%A]
```

로그의 `target folder:` 가 실행마다 달라져야 하고, 상태 파일은 `[ProjectA]` 처럼
**대괄호가 이름에 딱 붙어야** 합니다. `[ProjectA ]` 처럼 뒤에 공백이 보이면 순환이
첫 폴더에 고착됩니다.

### 4-3. 실제 전송

`DRY_RUN` 을 `0` 으로 되돌리고 실행한 뒤, **수신 기기에서 파일이 실제로 도착했는지**
확인하십시오. 전송 성공 코드와 실제 도착은 별개입니다.

### 4-4. 롤백과 pending

`TARGETS` 의 첫 항목만 존재하지 않는 이름으로 바꿔 실행하면 로그에
`failed ... -> <가짜이름>` 다음 줄에 `sent ... -> <2순위 기기>` 가 나와야 합니다.

`TARGETS` 전체를 가짜 이름으로 바꾸면 종료 코드가 `2` 가 되고 zip 이 `pending` 에 남습니다.

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

### 실패했다면 (fallback)

XML 스키마 오류나 권한 오류가 나면, 트리거를 3개 작업으로 쪼개 등록합니다.
`StartWhenAvailable`(놓친 실행 보충)과 `IgnoreNew`(중복 실행 방지)를 잃지만 동작은 합니다.

```cmd
schtasks /create /tn "TSBackup_Hourly" /tr "wscript.exe C:\Scripts\ts_backup_hidden.vbs" /sc HOURLY /mo 1 /f
schtasks /create /tn "TSBackup_Logon"  /tr "wscript.exe C:\Scripts\ts_backup_hidden.vbs" /sc ONLOGON /f
schtasks /create /tn "TSBackup_Resume" /tr "wscript.exe C:\Scripts\ts_backup_hidden.vbs" /sc ONEVENT /ec System /mo "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]" /f
```

`/sc ONSTART` 는 쓰지 않습니다. 비관리자 세션에서 접근 거부가 나기 쉽고,
사용자 컨텍스트 작업은 어차피 로그온 전에는 실행되지 않기 때문에 `ONLOGON` 이 실질적으로 동일합니다.

---

## 7단계 — 등록 검증

`schtasks /query /v` 는 표시 언어를 따라가고 이벤트 트리거를 `N/A` 로만 보여주므로,
확인은 PowerShell 쪽이 정확합니다.

```cmd
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName 'TailscaleProjectBackup').Triggers | Format-List"
```

`TimeTrigger`(Repetition PT1H), `LogonTrigger`, `EventTrigger` 세 개가 나와야 합니다.

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
- 절전 트리거는 실제로 절전 → 복귀시킨 뒤 로그에 실행 기록이 남는지로 확인합니다

---

## 제거

```cmd
schtasks /delete /tn "TailscaleProjectBackup" /f
rd /s /q C:\TempBackup
del C:\Users\DiCiA\backup_state.txt
rd /s /q C:\Scripts
```
