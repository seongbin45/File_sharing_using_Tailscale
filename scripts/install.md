# SSH(cmd) 환경 설치 절차

비관리자 OpenSSH 세션의 cmd 만으로 배치할 수 있도록 구성했습니다.
**아래 순서를 건너뛰지 마십시오. 특히 4단계(XML 인코딩 변환)를 빠뜨리면 스케줄 등록이 실패합니다.**

---

## 1단계 — 사전 확인

```cmd
tar --version
where wscript
tailscale status
```

- `tar` 는 Windows 10 1803 이상에 기본 포함되어 있습니다. 없으면 이 방식 자체를 쓸 수 없습니다.
- `tailscale status` 출력에서 대상 기기 4개(`wisenesco-23031302`, `laptop-7gmpubqc`,
  `desktop-dvj3pqk`, `desktop-0g92n63`)의 이름 철자와 온라인 여부를 확인하십시오.
- **수신 기기 쪽에서 Taildrop 수신이 켜져 있어야 합니다.** 보내는 쪽이 성공해도 받는 쪽이
  `tailscale file get` 을 돌리지 않거나 자동 저장이 꺼져 있으면 파일은 도착하지 않습니다.

---

## 2단계 — 파일 배치

### 방법 A: git clone (권장)

```cmd
mkdir C:\Scripts 2>nul
git clone -b claude/pycharmprojects-cyclic-backup-qm6355 https://github.com/seongbin45/file_sharing_using_tailscale.git C:\Scripts\src
copy /y C:\Scripts\src\scripts\ts_backup.bat        C:\Scripts\
copy /y C:\Scripts\src\scripts\ts_backup_hidden.vbs C:\Scripts\
copy /y C:\Scripts\src\scripts\ts_backup_task.xml   C:\Scripts\
```

이미 clone 해 둔 경우는 `cd /d C:\Scripts\src && git pull` 후 copy 만 다시 실행합니다.

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
(XML 은 `-Encoding ASCII` 대신 4단계에서 다시 변환하므로 일단 ASCII 로 저장해도 됩니다.)

끝나면 `exit` 로 cmd 에 돌아옵니다.

---

## 3단계 — 동작 테스트 (스케줄 등록 전)

전체 `PycharmProjects` 를 바로 돌리면 수 GB 가 압축·전송됩니다. 반드시 아래 순서를 지키십시오.

```cmd
rem 3-1. 전송 없이 압축만 확인
notepad C:\Scripts\ts_backup.bat
rem  -> BASE_DIR 을 소형 테스트 폴더로, DRY_RUN 을 1 로 변경 후 저장
rem  (SSH 라 notepad 를 못 쓰면 powershell 의 (Get-Content ...) -replace ... 사용)

C:\Scripts\ts_backup.bat
type C:\TempBackup\backup.log
```

확인할 것:
- 로그에 `archive ready, <바이트수> bytes` 가 찍히는가
- `DRY_RUN=1 - skipping transfer` 가 찍히는가

```cmd
rem 3-2. 순환 검증 - 3회 연속 실행 후 상태 파일 확인
C:\Scripts\ts_backup.bat
C:\Scripts\ts_backup.bat
C:\Scripts\ts_backup.bat
for /f "delims=" %A in (C:\Users\DiCiA\backup_state.txt) do @echo [%A]
```

`[ProjectA]` 처럼 **대괄호가 이름에 딱 붙어야** 합니다. `[ProjectA ]` 처럼 공백이 보이면
상태 파일 기록에 문제가 있는 것이며, 이 경우 순환이 첫 폴더에 고착됩니다.
로그에서 `target folder:` 줄이 매 실행마다 달라지는지도 함께 보십시오.

```cmd
rem 3-3. 롤백 검증 - TARGETS 첫 항목을 가짜 이름으로 바꾸고 DRY_RUN=0 으로 실행
rem  로그에 "failed <파일> -> <가짜이름>" 다음 줄에
rem  "sent <파일> -> laptop-7gmpubqc" 가 찍히면 정상

rem 3-4. pending 검증 - TARGETS 전체를 가짜 이름으로 바꾸고 실행
dir C:\TempBackup\pending
rem  zip 이 남아 있어야 하고, 다음 실행 로그에 "pending flushed" 또는
rem  "pending still stuck" 이 새 압축보다 먼저 나와야 함
```

테스트가 끝나면 `BASE_DIR` 을 실제 경로로, `TARGETS` 를 원래 목록으로, `DRY_RUN` 을 `0` 으로
되돌리고 `C:\TempBackup\pending` 안의 테스트 잔여물을 지웁니다.

---

## 4단계 — XML 변환 (필수)

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

## 5단계 — 작업 스케줄러 등록

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

## 6단계 — 등록 검증

```cmd
schtasks /query /tn "TailscaleProjectBackup" /v /fo list
```

- `Scheduled Task State` 가 `Enabled`
- `Next Run Time` 이 1시간 이내
- 트리거 3종이 모두 보이는지

```cmd
schtasks /run /tn "TailscaleProjectBackup"
timeout /t 30
schtasks /query /tn "TailscaleProjectBackup" /v /fo list | findstr /i "Last"
type C:\TempBackup\backup.log
```

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
