# PycharmProjects 순환 백업 → Tailscale 전송

Windows PC 의 프로젝트 폴더들을 **자동으로, 한 번에 하나씩 차례로** 압축해 다른 기기로
보내는 백업 스크립트입니다. 매시간, 컴퓨터를 켤 때, 절전에서 깨어날 때 실행됩니다.

전송에는 **Tailscale Taildrop** 을 씁니다. Tailscale 은 같은 계정으로 로그인한 기기들을
하나의 사설 네트워크로 묶어주는 VPN 이고, Taildrop 은 그 안에서 기기 간에 파일을 직접 밀어
넣는 기능입니다(`tailscale file cp <파일> <기기이름>:`). 클라우드 계정이나 공유 폴더 설정
없이 **기기 이름만으로** 파일을 보낼 수 있어 백업 전송 수단으로 씁니다.

`.env` 와 `.git` 이력은 백업 대상에 반드시 포함됩니다(기본값은 제외 폴더 없음).

---

## 한눈에 보기

한 회차가 실행되면 로그에 이렇게 남습니다.

```
[2026-08-30 16:34:43.57] === run start ===
[2026-08-30 16:34:43.94] target folder: A1-2_Project (previous: A1-1_Project)
[2026-08-30 16:34:43.94] creating archive: C:\TempBackup\A1-2_Project_2026_08_30_16_34.zip
[2026-08-30 16:34:50.91] archive ready, 343628118 bytes
[2026-08-30 16:35:37.93] sent A1-2_Project_2026_08_30_16_34.zip -> wisenesco-23031302
[2026-08-30 16:35:37.97] sent and removed local archive
[2026-08-30 16:35:37.98] === run end (exit 0) ===
```

직전 회차가 `A1-1_Project` 였으므로 이번엔 그 다음인 `A1-2_Project` 차례입니다. 압축해서
1순위 기기로 보내고, 보낸 뒤 로컬 압축 파일은 지웁니다.

---

## 왜 이런 구조인가

세 개의 파일이 사슬처럼 연결됩니다.

```
작업 스케줄러    →    wscript.exe   →   ts_backup_hidden.vbs  →  ts_backup.bat
(언제 실행할지)      (창 없는 호스트)     (배치를 숨겨서 실행)      (실제 작업)
```

단계마다 이유가 있습니다.

- **왜 파이썬이 아니라 배치인가** — 하는 일이 "압축하고(`tar`) 보내기(`tailscale`)"뿐입니다.
  둘 다 Windows 명령줄에서 바로 실행되는 도구라, 파이썬을 끼우면 런타임 의존성만 늘어납니다.
- **왜 VBS 래퍼가 필요한가** — 작업 스케줄러가 배치를 직접 실행하면 매시간 CMD 창이 화면에
  번쩍입니다. VBS 의 `Run(..., 0, True)` 로 감싸면 창 없이 실행되고, 세 번째 인자 `True`
  덕분에 배치의 종료 코드가 스케줄러까지 그대로 올라옵니다.
- **왜 XML 로 등록하는가** — `schtasks` 명령줄로는 "놓친 실행 보충", "중복 실행 방지" 같은
  옵션을 지정할 수 없습니다. XML 정의를 임포트하면 트리거 3종과 이 옵션들을 한 번에 넣습니다.
- **왜 한 번에 하나씩만 보내는가** — 매시간 전체를 통째로 보내면 같은 데이터를 반복 전송하게
  됩니다. 하나씩 돌면 한 회차가 짧게 끝나고 부하가 시간에 걸쳐 분산됩니다.

설치 환경의 제약도 설계에 그대로 반영돼 있습니다. 이 시스템은 **관리자 권한이 없는 SSH(cmd)
세션만으로** 설치하고 운영할 수 있어야 했습니다. 그래서 SYSTEM 권한 실행 대신 현재 사용자
권한으로 등록하고, 창 숨김을 VBS 로 우회하며, 모든 설치 절차가 명령줄만으로 완결됩니다.
편집기 없이 파일을 만들고 고치는 방법까지 [install.md](scripts/install.md) 에 들어 있는 것도
그 때문입니다.

---

## 사전 요구사항

| 항목 | 확인 명령 |
|---|---|
| `tar` — Windows 10 1803 이상 기본 포함 | `tar --version` |
| `wscript` — 창 숨김 실행에 사용 | `where wscript` |
| Tailscale — 기기들이 같은 계정으로 연결돼 있어야 함 | `tailscale status` |
| PowerShell 5.1 이상 | `powershell -NoProfile -Command "$PSVersionTable.PSVersion"` |

**관리자 권한은 필요 없습니다.**

수신 기기 쪽에서 Taildrop 수신이 준비돼 있어야 파일이 실제로 도착합니다. 기기 종류별 조건은
[docs/PORTING.md](docs/PORTING.md) 의 "수신 기기 쪽 전제" 를 보십시오.

---

## 문서

이 저장소는 **보내는 쪽**과 **받는 쪽** 두 부분으로 이루어집니다. 아래 문서는 별도 표시가
없으면 보내는 쪽 이야기입니다.

| 문서 | 언제 보는가 |
|---|---|
| [scripts/install.md](scripts/install.md) | **보내는 쪽을 처음 설치할 때.** SSH(cmd) 명령 전문. 여기부터 시작하십시오 |
| [docs/RECEIVER.md](docs/RECEIVER.md) | **받는 쪽.** 도착한 압축을 git 으로 관리하는 구조와 복원 방법 |
| [scripts/receiver/install.md](scripts/receiver/install.md) | 받는 쪽 설치 절차 |
| [docs/PORTING.md](docs/PORTING.md) | 다른 PC 나 다른 대상 폴더에 옮길 때. 고쳐야 할 값 전수 목록 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 뭔가 이상할 때. 증상 → 원인 → 조치 |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | 실제로 동작하는지 근거가 궁금할 때, 코드를 고친 뒤 회귀 테스트할 때 |

---

## 구성

| 파일 | 배치 위치 | 역할 |
|---|---|---|
| `scripts/ts_backup.bat` | `C:\Scripts\ts_backup.bat` | 순환 · 압축 · 전송 · 롤백 · 보존 전체 로직 |
| `scripts/ts_backup_hidden.vbs` | `C:\Scripts\ts_backup_hidden.vbs` | 창을 숨기고 배치를 실행하는 래퍼 |
| `scripts/ts_backup_task.xml` | `C:\Scripts\ts_backup_task.xml` | 트리거 3종 작업 정의 |

런타임 경로:

```
C:\TempBackup\                   압축 파일 생성 위치
C:\TempBackup\pending\           전 기기 전송 실패분 (다음 실행에서 재시도)
C:\TempBackup\backup.log         실행 로그 (5MB 초과 시 .1 로 회전)
C:\Users\DiCiA\backup_state.txt  마지막으로 백업한 폴더명
C:\Scripts\src\                  이 저장소의 clone (원본 사본 겸 업데이트 경로)
```

`C:\Scripts\src` 는 [install.md](scripts/install.md) 에서 이 저장소를 `git clone` 한 위치입니다.
실제 실행되는 파일은 `C:\Scripts\` 에 복사된 쪽이고, `src` 는 원본 사본으로 남아 있어
설정을 잘못 고쳤을 때 되돌리거나 `git pull` 로 업데이트를 받는 데 씁니다.

---

## 실행 흐름

1. 로그 회전, 이전 실행이 중단되며 남긴 고아 zip 삭제
2. 타임스탬프 취득 (`yyyy_MM_dd_HH_mm`)
3. **`pending` 재전송 우선** — 실패해 쌓인 zip 을 오래된 순으로 재시도, 성공분만 삭제
4. `pending` 보존 정책 적용 (경과일 / 프로젝트당 개수)
5. 상태 파일을 읽어 **다음 순번 폴더**를 선정하고, 상태 파일을 **즉시** 갱신
   (`BASE_DIR` 바로 아래의 `.idea` 같은 점으로 시작하는 폴더는 프로젝트가 아니므로 건너뜁니다)
6. `tar` 로 원본을 직접 압축 → `프로젝트명_2026_08_29_07_36.zip`
7. 대상 기기를 순서대로 시도, 첫 성공에서 중단
8. 성공 → zip 삭제 / 전 기기 실패 → `pending` 으로 이동

**상태 파일을 전송 성공 후가 아니라 폴더 선정 직후에 갱신하는 이유**: 특정 폴더가 계속
실패할 때 커서가 멈추면 나머지 프로젝트가 영원히 백업되지 않습니다. 실패분의 책임은
`pending` 재시도 로직이 집니다.

---

## 백업 대상 범위

한 회차에 압축되는 것은 정확히 이것입니다.

```
tar -a -c -f "C:\TempBackup\A1-1_Project_2026_08_30_16_43.zip" -C "C:\Users\DiCiA\PycharmProjects" "A1-1_Project"
```

즉 `BASE_DIR\<대상폴더>\` **이하 전부**입니다. 압축 파일 내부 경로가 `A1-1_Project/...` 로
시작하므로, 풀면 프로젝트 폴더가 그대로 복원됩니다.

**포함** — 제외 패턴이 비어 있으므로 그 폴더 안의 모든 것. `.git` 이력, `.env`,
`venv`, `__pycache__`, `.idea`, `node_modules` 전부 들어갑니다.

**제외** — 두 가지뿐입니다.

1. `BASE_DIR` 바로 아래의 **낱개 파일**. 순환은 디렉터리만 훑습니다.
   `dir /a-d "%BASE_DIR%"` 로 사각지대가 있는지 확인하십시오
2. `BASE_DIR` 바로 아래의 **점으로 시작하는 폴더**(`PycharmProjects\.idea` 등).
   프로젝트가 아니라 편집기 작업공간 설정이므로 한 회차를 낭비하지 않도록 건너뜁니다.
   프로젝트 **안쪽**의 `.git`, `.idea` 는 그대로 포함됩니다

`BASE_DIR` 아래의 모든 디렉터리가 순환 대상이며, 매 실행마다 하나씩 차례로 처리됩니다.
한 바퀴를 다 돌면 첫 폴더로 돌아옵니다. **한 바퀴에 걸리는 시간 = 폴더 개수 × 실행 주기**
입니다.

```cmd
dir /b /ad C:\Users\DiCiA\PycharmProjects | find /c /v ""
```

폴더를 새로 만들면 자동으로 순환에 합류하고, 삭제하면 커서가 첫 폴더로 되돌아가며
자가 복구됩니다.

---

## 전송 대상과 롤백

```
1순위  wisenesco-23031302
2순위  laptop-7gmpubqc
3순위  desktop-dvj3pqk
4순위  desktop-0g92n63
```

4개 모두 실패하면 zip 은 `pending` 에 남고 다음 실행에서 새 압축보다 **먼저** 재전송됩니다.

---

## 종료 코드

작업 스케줄러의 `Last Result` 로 그대로 노출됩니다. VBS 래퍼가 배치 종료를 기다렸다가
같은 코드를 반환하므로, 스케줄러 화면만 봐도 상태를 알 수 있습니다.

| 코드 | 의미 |
|---|---|
| `0` | 압축·전송 성공 (또는 백업할 폴더가 없어 할 일 없음) |
| `1` | 치명적 실패 — 쓸 수 있는 압축 파일을 만들지 못함 |
| `2` | 압축은 됐으나 4개 기기 모두 거부 — `pending` 에 보관, 다음 실행에서 재시도 |

`2` 가 반복해서 찍히면 Tailscale 연결이나 수신 기기 설정을 점검해야 합니다.

---

## 설정 변수

전부 `ts_backup.bat` 상단 `CONFIG` 블록에 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `BASE_DIR` | `C:\Users\DiCiA\PycharmProjects` | 프로젝트 폴더들이 있는 루트 |
| `STATE_FILE` | `C:\Users\DiCiA\backup_state.txt` | 순환 커서 |
| `WORK_DIR` | `C:\TempBackup` | 작업 디렉터리 |
| `TARGETS` | 기기 4개 | 공백 구분, 순서가 곧 우선순위 |
| `PENDING_KEEP_DAYS` | `3` | pending 보관 기한 |
| `PENDING_KEEP_PER_PROJECT` | `1` | 프로젝트당 pending 최대 개수 |
| `LOG_MAX_MB` | `5` | 로그 회전 기준 |
| `TAR_EXCLUDES` | 없음 | 압축 제외 패턴 |
| `DRY_RUN` | `0` | `1` 이면 압축만 하고 전송하지 않음 |

### 나중에 제외 폴더를 추가하려면

`TAR_EXCLUDES` 한 줄만 바꾸면 됩니다. `bsdtar` 패턴이며 `*/이름/*` 형태를 씁니다.

```bat
set "TAR_EXCLUDES=--exclude=*/venv/* --exclude=*/.venv/* --exclude=*/__pycache__/* --exclude=*/node_modules/* --exclude=*/.idea/*"
```

`.git` 과 `.env` 는 백업 목적상 **제외 목록에 넣지 마십시오.**

---

## 운영

### 상태 점검

```cmd
type C:\TempBackup\backup.log
powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName 'TailscaleProjectBackup' | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns"
```

`LastTaskResult` 가 `0` 이고 `NumberOfMissedRuns` 가 늘지 않으면 정상입니다.
표시 언어에 의존하는 `schtasks /query /v | findstr Last` 는 한글 Windows 에서 동작하지 않습니다.

### 일시 중지 / 재개

```cmd
schtasks /change /tn "TailscaleProjectBackup" /disable
schtasks /change /tn "TailscaleProjectBackup" /enable
```

### 즉시 한 회차 실행

```cmd
schtasks /run /tn "TailscaleProjectBackup"
```

### 특정 프로젝트를 다음 차례로 지정

상태 파일에는 **마지막으로 처리한** 폴더명이 들어 있고, 다음 실행은 그 **다음** 폴더를
집습니다. 따라서 원하는 폴더의 바로 앞 폴더명을 써 넣으면 됩니다. 순서는 `dir /b /ad` 기준입니다.

```cmd
dir /b /ad C:\Users\DiCiA\PycharmProjects
>C:\Users\DiCiA\backup_state.txt echo 원하는폴더의_바로_앞_폴더명
```

첫 폴더부터 다시 시작하려면 상태 파일을 지우면 됩니다.

```cmd
del C:\Users\DiCiA\backup_state.txt
```

리디렉션을 `echo` **앞**에 두는 것에 유의하십시오. `echo 값 > 파일` 로 쓰면 값 뒤에 공백이
붙어 다음 실행의 비교가 실패합니다.

### 설정 변경

`C:\Scripts\ts_backup.bat` 상단을 고칩니다. 셸에서 `set "TARGETS=..."` 를 해도 배치가
실행 시 자기 값을 다시 설정하므로 반영되지 않습니다.

```cmd
powershell -NoProfile -Command "$p='C:\Scripts\ts_backup.bat'; $x=(Get-Content $p -Raw) -replace '기존값','새값'; Set-Content -Path $p -Value $x -Encoding ASCII"
findstr /c:"set \"TARGETS=" C:\Scripts\ts_backup.bat
```

경로처럼 `\` 가 들어가는 값은 **찾는 쪽만 `\\` 로 이스케이프**하고 바꿀 값은 `\` 하나로 씁니다.
고친 뒤에는 반드시 위처럼 실제 반영을 눈으로 확인하십시오.

원복은 저장소 사본 재복사가 가장 확실합니다.

```cmd
copy /y C:\Scripts\src\scripts\ts_backup.bat C:\Scripts\
```

### 제거

```cmd
schtasks /delete /tn "TailscaleProjectBackup" /f
rd /s /q C:\TempBackup
del C:\Users\DiCiA\backup_state.txt
rd /s /q C:\Scripts
```

---

## 설계 노트

### 스크립트 본문은 ASCII 전용

`.bat` 과 `.vbs` 에는 한글을 넣지 않습니다. cmd 콘솔 코드페이지(949)와 파일 인코딩이
어긋나면 로그가 깨지고, 최악의 경우 배치 파싱이 틀어집니다. 설명은 이 문서에만 둡니다.

### 배치 문법에서 실제로 물렸던 함정들

- `if <조건> <명령A> & <명령B>` 에서 **B 는 조건과 무관하게 항상 실행**됩니다.
  전송 롤백을 이 형태로 쓰면 1순위 실패 후 곧장 정리 단계로 점프해 2~4순위가 죽습니다.
  `:TrySend` 서브루틴 + `exit /b` 로 구성한 이유입니다.
- 괄호 블록 안의 `%ERRORLEVEL%` 는 블록 **파싱 시점**에 확장되어 항상 낡은 값입니다.
  `if not errorlevel 1` 을 쓰면 실행 시점 값을 봅니다.
- `echo %VAR% > file` 은 값 뒤에 **공백 한 칸**을 붙여 기록합니다. 그 값을 다시 읽어
  비교하면 영원히 불일치합니다. 리디렉션을 앞에 두어(`>file echo %VAR%`) 회피합니다.
- `echo %~1` 처럼 인자를 직접 출력하면, 인자 안의 `>` 가 **파싱 시점에 리디렉션
  연산자로 승격**됩니다. `sent x.zip -> host` 라는 로그 한 줄이 로그가 아니라
  `host` 라는 이름의 파일로 조용히 빠져나갑니다(실제로 겪은 버그).
  값을 변수에 담아 `!MSG!` 로 출력하면 됩니다. 지연 확장은 리디렉션 판정이
  끝난 뒤에 일어나므로 특수문자가 무해해집니다.
- `tar ... -C <경로> *` 의 `*` 는 `-C` 대상이 아닌 현재 디렉터리 기준으로 해석될 수
  있습니다. 폴더명을 명시(`-C "%BASE_DIR%" "%TARGET%"`)하면 압축 해제 시 프로젝트
  폴더가 그대로 복원되기까지 합니다.
- `bsdtar` 는 잠긴 파일(git index, sqlite 등) 때문에 경고와 함께 종료 코드 `1` 을
  반환하지만 아카이브는 정상입니다. `2` 이상만 치명 오류로 처리합니다.
- `schtasks /create /xml` 은 XML 이 **UTF-16** 이어야 합니다. UTF-8 이면 등록이 실패합니다.

### robocopy 스테이징을 쓰지 않는 이유

제외 폴더가 없는 설정에서 임시 복사는 디스크 쓰기와 I/O 를 2배로 만들 뿐입니다.
제외가 필요해지면 `bsdtar --exclude` 로 동일하게 처리됩니다.

### 중복 실행 방지

트리거 3종이 겹칠 수 있으므로 작업 정의에 `MultipleInstancesPolicy=IgnoreNew` 를 두었습니다.
별도의 락 파일을 쓰지 않는 이유는, 잘못 남은 락이 백업을 영구히 멈추는 쪽이 더 위험하기 때문입니다.

---

## 범위 밖

**보내는 쪽 스크립트는 수신 기기의 파일을 정리하지 않습니다.** 파일명에 타임스탬프가 붙어
덮어쓰기가 일어나지 않으므로, 그대로 두면 수신 기기에 매시간 한 개씩 쌓입니다.
받는 쪽에서 이를 풀어 git 으로 관리하고 zip 을 걷어내는 스크립트가
[docs/RECEIVER.md](docs/RECEIVER.md) 에 있습니다.

**수신 기기의 Taildrop 수신 준비**는 여전히 범위 밖입니다. 보내는 쪽이 성공 코드를 받아도 받는 쪽이
준비되어 있지 않으면 파일은 도착하지 않습니다. [docs/PORTING.md](docs/PORTING.md) 의
"수신 기기 쪽 전제" 참조.

---

## 검증 상태

실제 기기에서 압축·전송, 폴더 순환, 롤백, pending 재전송, 스케줄러 등록·실행까지 확인했습니다.
창 숨김 동작과 절전 해제 트리거 등 물리 화면이나 장시간 관찰이 필요한 항목은 미검증으로
남아 있습니다. 항목별 근거와 실제 로그는 [docs/VERIFICATION.md](docs/VERIFICATION.md) 에
있습니다.
