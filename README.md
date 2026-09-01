# PycharmProjects 전체 스냅샷 백업 → Tailscale 전송

Windows PC 의 프로젝트 루트를 **하루 한 번 통째로** 압축해 다른 기기로 보내는 백업
스크립트입니다. 받는 쪽은 도착한 압축을 풀어 git 으로 관리하며, 커밋 하나하나가
그 시점의 완전한 복원 지점이 됩니다.

전송에는 **Tailscale Taildrop** 을 씁니다. Tailscale 은 같은 계정으로 로그인한 기기들을
하나의 사설 네트워크로 묶어주는 VPN 이고, Taildrop 은 그 안에서 기기 간에 파일을 직접 밀어
넣는 기능입니다(`tailscale file cp <파일> <기기이름>:`). 클라우드 계정이나 공유 폴더 설정
없이 **기기 이름만으로** 파일을 보낼 수 있어 백업 전송 수단으로 씁니다.

**제외하는 것은 없습니다.** `.git` 이력, `.env`, `venv`, `__pycache__` 까지 전부 들어갑니다.
받는 쪽 저장소가 무한히 커지는 문제는 제외가 아니라 **주기적 초기화**로 막습니다.

---

## 한눈에 보기

한 회차가 실행되면 보내는 쪽 로그에 이렇게 남습니다.

```
[2026-11-26 04:00:01.20] === run start ===
[2026-11-26 04:00:02.44] free space on work volume: 178422 MB
[2026-11-26 04:00:02.51] creating archive: C:\TempBackup\PycharmProjects_2026_11_26_04_00.7z (LZMA2 -mx=5)
[2026-11-26 04:12:44.83] archive ready, 4741108614 bytes
[2026-11-26 04:26:03.71] sent PycharmProjects_2026_11_26_04_00.7z -> wisenesco-23031302
[2026-11-26 04:26:03.77] sent and removed local archive
[2026-11-26 04:26:03.78] === run end (exit 0) ===
```

받는 쪽은 이 압축을 풀어 커밋하고 `2026_11_26_04_00` 태그를 찍습니다.
그 시점으로 돌아가려면 `git checkout 2026_11_26_04_00` 한 줄이면 됩니다.

---

## 왜 이런 구조인가

세 개의 파일이 사슬처럼 연결됩니다. 보내는 쪽과 받는 쪽 모두 같은 모양입니다.

```
작업 스케줄러    →    wscript.exe   →   ts_backup_hidden.vbs  →  ts_backup.bat
(언제 실행할지)      (창 없는 호스트)     (배치를 숨겨서 실행)      (실제 작업)
```

- **왜 파이썬이 아니라 배치인가** — 보내는 쪽이 하는 일은 "압축하고(`7z`) 보내기
  (`tailscale`)"뿐입니다. 둘 다 명령줄 도구라 파이썬을 끼우면 의존성만 늘어납니다.
  반대로 받는 쪽은 파일명 파싱·상태 관리·git 호출이 필요해 PowerShell 로 씁니다.
- **왜 VBS 래퍼가 필요한가** — 작업 스케줄러가 배치를 직접 실행하면 CMD 창이 화면에
  번쩍입니다. VBS 의 `Run(..., 0, True)` 로 감싸면 창 없이 실행되고, 세 번째 인자 `True`
  덕분에 배치의 종료 코드가 스케줄러까지 그대로 올라옵니다.
- **왜 XML 로 등록하는가** — `schtasks` 명령줄로는 "놓친 실행 보충", "중복 실행 방지" 같은
  옵션을 지정할 수 없습니다.

설치 환경의 제약도 설계에 그대로 반영돼 있습니다. 이 시스템은 **관리자 권한이 없는 SSH(cmd)
세션만으로** 설치하고 운영할 수 있어야 했습니다. 편집기 없이 파일을 만들고 고치는 방법까지
[install.md](scripts/install.md) 에 들어 있는 것도 그 때문입니다.

### 왜 하루 한 번 전체인가

프로젝트를 하나씩 순환하며 매시간 보내는 방식도 검토했고, 실제로 그렇게 먼저 만들었습니다.
프로젝트가 23개가 되면서 그 방식의 이점이 사라졌습니다.

| | 순환 (하나씩 매시간) | 전체 스냅샷 (하루 1회) |
|---|---|---|
| 프로젝트별 갱신 주기 | 23시간 | 24시간 |
| 하루 전송량 | 6.6 GB (zip) | **4.74 GB (7z)** |
| 일관된 복원 지점 | **없음** | 매 회차 |
| 새 프로젝트 반영 | 최대 23시간 지연 | 즉시 |
| 삭제된 프로젝트 반영 | **영원히 안 됨** | 자동 |
| 코드 복잡도 | 상태 파일 · 순환 커서 · 한 바퀴 판정 | 없음 |

전송량은 오히려 전체 스냅샷이 적고 갱신 주기는 같은데, 순환 방식에는 "이 순간의 전체"라는
스냅샷이 한 번도 만들어지지 않습니다. 프로젝트가 늘수록 순환은 계속 나빠집니다(30개면 한
바퀴 30시간). 전체 스냅샷은 늘어난 만큼만 커집니다.

**매시간 전체 전송은 불가능합니다.** 4.74 GB × 24회 = 하루 114 GB 입니다.

실측된 한 회차(6.30 GB 대상, 프로젝트 23개, 파일 32,095개):

| 단계 | 소요 |
|---|---|
| 압축 (7z `-mx=5`) → 4.74 GB | 약 12분 |
| Taildrop 전송 (실효 약 5.8 MB/s) | 약 13분 |
| 받는 쪽 해제 · 커밋 · 태그 | 약 16분 |

---

## 사전 요구사항

| 항목 | 확인 명령 |
|---|---|
| **7-Zip** — 양쪽 PC 모두 필요 | `dir "C:\Program Files\7-Zip\7z.exe"` |
| `wscript` — 창 숨김 실행에 사용 | `where wscript` |
| Tailscale — 기기들이 같은 계정으로 연결돼 있어야 함 | `tailscale status` |
| PowerShell 5.1 이상 | `powershell -NoProfile -Command "$PSVersionTable.PSVersion"` |
| 작업 볼륨 여유 공간 | 압축 하나가 통째로 올라갈 만큼 (기본 최소 10 GB) |

**관리자 권한은 필요 없습니다.** 받는 쪽은 여기에 더해 `git` 이 필요합니다.

7-Zip 은 설치해도 **PATH 에 등록되지 않습니다.** 스크립트가 `SEVENZIP` 설정값의 전체 경로로
직접 호출하는 이유입니다. 작업 스케줄러는 맨 환경에서 돌기 때문에 PATH 에 기대면 안 됩니다.
없으면 https://www.7-zip.org 에서 설치하십시오.

---

## 문서

이 저장소는 **보내는 쪽**과 **받는 쪽** 두 부분으로 이루어집니다. 아래 문서는 별도 표시가
없으면 보내는 쪽 이야기입니다.

| 문서 | 언제 보는가 |
|---|---|
| [scripts/install.md](scripts/install.md) | **보내는 쪽을 처음 설치할 때.** SSH(cmd) 명령 전문 |
| [docs/RECEIVER.md](docs/RECEIVER.md) | **받는 쪽.** git 관리 구조, 90일 초기화, 복원 방법 |
| [scripts/receiver/install.md](scripts/receiver/install.md) | 받는 쪽 설치 절차 |
| [docs/PORTING.md](docs/PORTING.md) | 다른 PC 나 다른 대상 폴더에 옮길 때. 고쳐야 할 값 전수 목록 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 뭔가 이상할 때. 증상 → 원인 → 조치 |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | 무엇이 실기에서 검증됐고 무엇이 아닌지 |

---

## 구성

| 파일 | 배치 위치 | 역할 |
|---|---|---|
| `scripts/ts_backup.bat` | `C:\Scripts\ts_backup.bat` | 압축 · 전송 · 롤백 · 재시도 |
| `scripts/ts_backup_hidden.vbs` | `C:\Scripts\ts_backup_hidden.vbs` | 창을 숨기고 배치를 실행하는 래퍼 |
| `scripts/ts_backup_task.xml` | `C:\Scripts\ts_backup_task.xml` | 하루 1회 작업 정의 |

런타임 경로:

```
C:\TempBackup\                   압축 파일 생성 위치
C:\TempBackup\pending\           전 기기 전송 실패분 (다음 실행에서 재시도)
C:\TempBackup\backup.log         실행 로그 (5MB 초과 시 .1 로 회전)
C:\Scripts\src\                  이 저장소의 clone (원본 사본 겸 업데이트 경로)
```

`C:\Scripts\src` 는 [install.md](scripts/install.md) 에서 이 저장소를 `git clone` 한 위치입니다.
실제 실행되는 파일은 `C:\Scripts\` 에 복사된 쪽이고, `src` 는 원본 사본으로 남아 있어
설정을 잘못 고쳤을 때 되돌리거나 `git pull` 로 업데이트를 받는 데 씁니다.

순환 커서가 없으므로 **상태 파일이 없습니다.** 매 회차가 서로 독립적입니다.

---

## 실행 흐름

1. 로그 회전, 이전 실행이 중단되며 남긴 고아 압축 파일 삭제
2. 타임스탬프 취득 (`yyyy_MM_dd_HH_mm`)
3. **여유 공간 확인** — `MIN_FREE_MB` 미만이면 시작하지 않고 종료. 디스크를 채운 채
   반쯤 만들어진 압축을 남기는 것이 건너뛰는 것보다 나쁩니다
4. **`pending` 재전송 우선** — 실패해 남은 압축 파일을 먼저 재시도
5. 7-Zip 으로 `BASE_DIR` 전체를 압축 → `PycharmProjects_2026_11_26_04_00.7z`
6. 대상 기기를 순서대로 시도, 첫 성공에서 중단
7. 성공 → 압축 파일 삭제 / 전 기기 실패 → `pending` 으로 이동

```
"C:\Program Files\7-Zip\7z.exe" a -t7z -mx=5 -mmt=on "C:\TempBackup\PycharmProjects_2026_11_26_04_00.7z" "C:\Users\DiCiA\PycharmProjects"
```

7-Zip 은 디렉터리를 받으면 그 부모 기준의 상대 경로로 저장하므로, 압축을 풀면
`PycharmProjects/` 가 그대로 복원됩니다.

---

## 전송 대상과 롤백

```
1순위  wisenesco-23031302
2순위  laptop-7gmpubqc
3순위  desktop-dvj3pqk
4순위  desktop-0g92n63
```

4개 모두 실패하면 압축 파일은 `pending` 에 남고 다음 실행에서 새 압축보다 **먼저**
재전송됩니다. `pending` 은 최신 1개 · 3일 경과분 자동 삭제로 상한이 걸려 있습니다.
각각이 수 GB짜리 전체 스냅샷이므로 여러 개를 쌓아둘 이유가 없습니다.

---

## 종료 코드

작업 스케줄러의 `Last Result` 로 그대로 노출됩니다. VBS 래퍼가 배치 종료를 기다렸다가
같은 코드를 반환합니다.

| 코드 | 의미 |
|---|---|
| `0` | 압축·전송 성공 |
| `1` | 치명적 실패 — 여유 공간 부족, `BASE_DIR` 없음, 압축 실패 |
| `2` | 압축은 됐으나 4개 기기 모두 거부 — `pending` 에 보관, 다음 실행에서 재시도 |

---

## 설정 변수

전부 `ts_backup.bat` 상단 `CONFIG` 블록에 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `BASE_DIR` | `C:\Users\DiCiA\PycharmProjects` | 통째로 압축할 디렉터리 |
| `WORK_DIR` | `C:\TempBackup` | 압축 파일 생성 위치 |
| `MIN_FREE_MB` | `10000` | 이보다 여유가 적으면 시작하지 않음 |
| `SEVENZIP` | `C:\Program Files\7-Zip\7z.exe` | 7z.exe 전체 경로. PATH 에 의존하지 않음 |
| `SEVENZIP_LEVEL` | `5` | LZMA2 압축 수준 (`1` 빠름 / `5` 균형 / `9` 최대) |
| `TARGETS` | 기기 4개 | 공백 구분, 순서가 곧 우선순위 |
| `PENDING_KEEP_DAYS` | `3` | pending 보관 기한 |
| `PENDING_KEEP_COUNT` | `1` | pending 최대 개수 |
| `LOG_MAX_MB` | `5` | 로그 회전 기준 |
| `EXCLUDE_LIST` | 없음 | 제외할 이름을 한 줄에 하나씩 적은 목록 파일 경로 |
| `DRY_RUN` | `0` | `1` 이면 압축만 하고 전송하지 않음 |

실행 시각은 `ts_backup_task.xml` 의 `<StartBoundary>` 로 정합니다(기본 04:00).

### 제외 폴더를 쓰지 않는 이유와, 그래도 써야 한다면

받는 쪽 저장소 증가는 **90일마다 초기화**로 막습니다([RECEIVER.md](docs/RECEIVER.md)).
제외로 막지 않는 이유는, 한번 제외한 것은 어느 시점으로 돌아가도 복원할 수 없기 때문입니다.

그래도 전송량 자체를 줄여야 한다면 제외할 이름을 한 줄에 하나씩 적은 파일을 만들고
`EXCLUDE_LIST` 에 그 경로를 넣으면 됩니다. 경로에 공백이 없어야 합니다.

```cmd
> C:\Scripts\excludes.txt echo venv
>>C:\Scripts\excludes.txt echo .venv
>>C:\Scripts\excludes.txt echo __pycache__
>>C:\Scripts\excludes.txt echo node_modules
```

7-Zip 자체의 `-xr!이름` 스위치를 쓰지 않는 이유는, 배치에 지연 확장이 켜져 있어 **`!` 가
먹히기 때문**입니다. 그대로 적으면 제외가 조용히 무시됩니다.

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

### 일시 중지 / 재개 / 즉시 실행

```cmd
schtasks /change /tn "TailscaleProjectBackup" /disable
schtasks /change /tn "TailscaleProjectBackup" /enable
schtasks /run    /tn "TailscaleProjectBackup"
```

즉시 실행은 6 GB 를 압축·전송하므로 20분 가까이 걸립니다.

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
rd /s /q C:\Scripts
```

---

## 설계 노트

### 스크립트 본문은 ASCII 전용

`.bat` 과 `.vbs` 에는 한글을 넣지 않습니다. cmd 콘솔 코드페이지(949)와 파일 인코딩이
어긋나면 로그가 깨지고, 최악의 경우 배치 파싱이 틀어집니다. 설명은 문서에만 둡니다.

### 부팅·절전 해제 트리거를 두지 않는 이유

순환 방식일 때는 한 회차가 수십 초라 로그온이나 절전 해제마다 한 번 더 돌려도 부담이
없었습니다. 전체 스냅샷은 한 회차가 6 GB · 20분이라 그럴 수 없습니다.
`StartWhenAvailable` 이 그 역할을 대신합니다 — 04:00 에 PC 가 꺼져 있었다면 다음에 켜졌을 때
한 번 보충 실행됩니다.

### 배치 문법에서 실제로 물렸던 함정들

- `if <조건> <명령A> & <명령B>` 에서 **B 는 조건과 무관하게 항상 실행**됩니다.
  전송 롤백을 이 형태로 쓰면 1순위 실패 후 곧장 정리 단계로 점프해 2~4순위가 죽습니다.
  `:TrySend` 서브루틴 + `exit /b` 로 구성한 이유입니다.
- 괄호 블록 안의 `%ERRORLEVEL%` 는 블록 **파싱 시점**에 확장되어 항상 낡은 값입니다.
  `if not errorlevel 1` 을 쓰면 실행 시점 값을 봅니다.
- `echo %VAR% > file` 은 값 뒤에 **공백 한 칸**을 붙여 기록합니다. 리디렉션을 앞에 두어
  (`>file echo %VAR%`) 회피합니다.
- `echo %~1` 처럼 인자를 직접 출력하면, 인자 안의 `>` 가 **파싱 시점에 리디렉션
  연산자로 승격**됩니다. `sent x.zip -> host` 라는 로그 한 줄이 로그가 아니라
  `host` 라는 이름의 파일로 조용히 빠져나갑니다(실제로 겪은 버그).
  값을 변수에 담아 `!MSG!` 로 출력하면 됩니다.
- `bsdtar` 는 잠긴 파일(git index, sqlite 등) 때문에 경고와 함께 종료 코드 `1` 을
  반환하지만 아카이브는 정상입니다. `2` 이상만 치명 오류로 처리합니다.
- `schtasks /create /xml` 은 XML 이 **UTF-16** 이어야 합니다. UTF-8 이면 등록이 실패합니다.

### 왜 zip 이 아니라 7z 인가

`음원+악보병합 프로젝트` 하나로 실측한 값입니다.

| 방식 | 크기 | zip 대비 | 압축 시간 |
|---|---|---|---|
| zip (`tar -a`) | 829.2 MB | — | 47.6초 |
| 7z `-mx=1` | 745.6 MB | **−10.1%** | **22.6초** |
| 7z `-mx=5` | 656.9 MB | **−20.8%** | 73.5초 |

`-mx=1` 은 zip 보다 **작으면서 동시에 두 배 빠릅니다.** Windows 내장 `tar` 의 deflate 는
단일 스레드인데 7-Zip 은 `-mmt` 로 모든 코어를 쓰기 때문입니다. zip 을 유지할 이유가 없습니다.

`-mx=5` 를 기본으로 둔 것은, 전송까지 포함한 총 소요 시간이 zip 과 거의 같으면서 데이터는
21% 적기 때문입니다. 새벽 4시 무인 실행에 실행 제한이 4시간이라 압축에 몇 분 더 쓰는 것은
실질 비용이 아닙니다. 빠른 완료가 더 중요하면 `SEVENZIP_LEVEL` 을 `1` 로 낮추면 됩니다.

`-mx=9` 는 이 데이터에서 권하지 않습니다. 용량의 상당 부분이 mp4·mp3·png 라 알고리즘이
손댈 여지가 적은데, 압축 시간과 메모리만 몇 배로 늘어납니다.

### robocopy 스테이징을 쓰지 않는 이유

임시 복사는 디스크 쓰기와 I/O 를 2배로 만들 뿐입니다. 6 GB 규모에서는 특히 그렇습니다.
제외가 필요해지면 `bsdtar --exclude` 로 동일하게 처리됩니다.

---

## 범위 밖

**수신 기기의 Taildrop 수신 준비**는 이 저장소의 범위 밖입니다. 보내는 쪽이 성공 코드를
받아도 받는 쪽이 준비되어 있지 않으면 파일은 도착하지 않습니다.
[docs/PORTING.md](docs/PORTING.md) 의 "수신 기기 쪽 전제" 참조.

---

## 검증 상태

**순환 방식은 실기에서 전부 검증됐고, 현재의 전체 스냅샷 방식은 아직 검증되지 않았습니다.**
압축·전송·롤백·pending 재시도·스케줄러 등록은 순환 방식에서 확인된 것이고, 전체 스냅샷으로
바뀌면서 달라진 부분(6 GB 단일 파일 전송, 여유 공간 확인, 하루 1회 트리거)과 받는 쪽 전체는
확인이 필요합니다. 항목별 근거는 [docs/VERIFICATION.md](docs/VERIFICATION.md) 에 있습니다.
