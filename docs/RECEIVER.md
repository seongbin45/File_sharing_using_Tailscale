# 받는 쪽 — 도착한 스냅샷을 git 으로 관리하기

> 보내는 쪽은 [README](../README.md) 를 보십시오. 설치 절차는
> [scripts/receiver/install.md](../scripts/receiver/install.md) 에 있습니다.

Taildrop 이 `Downloads` 에 떨어뜨린 `<루트>_<yyyy_MM_dd_HH_mm>.7z` 를 받아, 고정된 관리
디렉터리에 풀고 커밋합니다. **압축 하나가 프로젝트 루트 전체의 스냅샷**이므로
**커밋 하나하나가 완전한 복원 지점**이고, 매 커밋에 타임스탬프 태그가 붙습니다.

저장소가 무한히 커지는 것은 제외가 아니라 **90일마다의 초기화**로 막습니다.

---

## 디렉터리 구조

```
C:\Users\<사용자>\Downloads\
    PycharmProjects_2026_11_26_04_00.7z    ← Taildrop 이 떨어뜨린 것 (처리 후 삭제)
    _rejected\                             ← 형식이 맞지 않아 걷어낸 압축 파일
    PycharmProjects\                       ← 관리 디렉터리 (git 저장소, 이름 고정)
        .git\                              ← 관리 저장소
        .gitattributes                     ← * -text (줄바꿈 변환 금지)
        .ts_state.json                     ← 상태 원본
        Day_count.txt                      ← 전체 요약
        A1-1_Project\
            Day_count.txt                  ← 이 프로젝트의 기록
            .git_archived\                 ← 원래 프로젝트의 .git (이름만 바뀜)
            ...
        (프로젝트 23개 전부)

C:\Users\<사용자>\PycharmProjects_Archive\
    PycharmProjects_2026_08_28_04_00\      ← 초기화 때 밀려난 세대 (.git 없는 평범한 폴더)

C:\TempReceive\
    receive.log                            ← 실행 로그 (5MB 초과 시 .1 로 회전)
    stage\                                 ← 압축 해제 임시 공간
```

관리 디렉터리 이름은 `PycharmProjects` 로 **고정**입니다. 시점 표시는 폴더 이름이 아니라
git 태그가 맡습니다. 이름을 매번 바꾸면 저장소 경로가 달라져 스크립트가 매번 탐색해야 하고,
이름 변경이 중간에 실패하면 어느 폴더가 현재 관리 디렉터리인지 판정할 수 없게 됩니다.

압축 안의 `PycharmProjects/` **내용물**이 관리 디렉터리의 내용물이 됩니다. 폴더 층이
하나 더 생기지 않습니다.

---

## 실행 흐름

1. 로그 회전, 저장소 준비(`git init`, 없으면 신원·옵션 설정)
2. `Downloads` 를 훑어 `<이름>_<타임스탬프>.7z` 형식 파일을 타임스탬프 오름차순으로 수집
3. **전송이 끝나지 않은 파일은 건너뜀** — 마지막 수정으로부터 120초가 지났고 배타적으로
   열리는 파일만 처리합니다. 4.4 GB 전송에 실측 13분이 걸리므로 넉넉히 잡습니다
4. **초기화 시점이 됐으면 먼저 초기화** (아래 참조)
5. 압축을 임시 공간에 풀고, 안의 모든 `.git` 을 `.git_archived` 로 바꿈
6. 관리 디렉터리를 `.git` 만 남기고 **비운 뒤** 새 내용물을 넣음
7. `Day_count.txt` 두 종류와 `.ts_state.json` 갱신
8. `git add -A` → `git commit -m "snapshot <타임스탬프>"` → `git tag <타임스탬프>`
9. 처리에 성공한 압축 파일 삭제 (또는 `_processed` 로 이동)

실패한 압축 파일은 그 자리에 남겨 다음 회차에 다시 시도합니다.

**7-Zip 이 없으면 아무것도 못 합니다.** Windows 내장 `tar` 도 `Expand-Archive` 도 `.7z` 를
읽지 못하므로 대체 경로가 없습니다. 시작할 때 `$SevenZip` 경로를 확인하고 없으면 종료 코드
`1` 로 끝냅니다.

### 왜 비우고 새로 푸는가

덮어쓰기만 하면 원본에서 **삭제된 파일이 관리 디렉터리에 영원히 남아** 실제 상태와 어긋납니다.
비우고 새로 풀면 커밋이 그 시점의 정확한 스냅샷이 됩니다. 디스크에서 지워도 git 이력에는
남으므로 실제로 잃는 것은 없고, `git checkout <태그>` 로 언제든 되돌릴 수 있습니다.

프로젝트가 통째로 삭제된 경우도 이 방식이라야 반영됩니다.

---

## 90일 초기화

`$ResetAfterDays` (기본 90) 가 지나면, 다음 실행에서 **압축을 풀기 전에** 이렇게 합니다.

1. 현재 관리 디렉터리를 `PycharmProjects_Archive\PycharmProjects_<마지막 스냅샷 타임스탬프>`
   로 통째로 옮김
2. 옮겨진 사본 안의 `.git` 을 **삭제** — 평범한 폴더 스냅샷으로 남김
3. 관리 디렉터리를 새로 만들고 `git init`
4. 이어서 오늘 도착한 압축을 풀어 새 저장소의 첫 커밋으로 삼음

압축을 풀기 **전에** 초기화하는 이유는, 그래야 새 저장소가 빈 채로 하루를 기다리지 않고
그 자리에서 채워지기 때문입니다.

### 초기화가 실제로 지우는 것

- 관리 저장소의 모든 커밋과 태그 — 그 저장소로는 과거 시점 이동 불가
- 남는 것은 밀려난 세대의 **평범한 폴더 하나**(그 시점의 파일 그대로)와, 새로 시작하는
  저장소뿐입니다

밀려난 세대는 git 이 아니므로 그 안에서 시점 이동은 안 됩니다. **파일을 그대로 읽는 용도**
입니다. 이 동작은 의도된 것입니다.

세대는 기본적으로 **자동 삭제하지 않습니다** (`$KeepArchiveGenerations = 0`).
백업을 스크립트가 말없이 지우는 것보다 디스크가 차는 편이 낫다는 판단입니다.
개수를 제한하려면 값을 지정하십시오.

### 용량이 어떻게 되는가

**`.git` 을 지워도 작업 트리 크기 아래로는 내려가지 않습니다.** 초기화 직후 첫 커밋을 하면
`.git` 이 다시 찹니다. mp3·mp4·png 는 git 이 압축도 델타도 못 하기 때문입니다.

실측(대상 6.30 GB / 32,095 파일 / 스냅샷 1개 기준):

```
작업 트리                     6.30 GB
.git (19,322 objects)         4.03 GiB
                             ---------
초기화 직후 바닥값            약 10.3 GB
세대 하나당 추가              약 6.3 GB  (.git 을 지우므로 작업 트리 크기만)
```

초기화는 **증가를 끊는 장치**이지 용량을 줄이는 장치가 아닙니다.

```cmd
git -C "%USERPROFILE%\Downloads\PycharmProjects" count-objects -vH
```

---

## `.git_archived` — 반드시 알아야 할 것

git 은 하위 디렉터리에 `.git` 이 있으면 그것을 **별도 저장소(embedded repository)** 로 보고
gitlink 한 줄만 기록합니다. **내부 파일은 하나도 추적되지 않습니다.**

프로젝트의 `.git` 이력 보존이 이 백업의 핵심 요구사항이므로, 압축을 풀 때 안의 모든 `.git`
디렉터리를 `.git_archived` 로 바꿔 넣습니다. 평범한 폴더가 되어 전부 추적됩니다.

### 프로젝트를 실제로 되살리려면

```cmd
rem 1) 관리 디렉터리 밖으로 복사한다 (안에서 이름을 되돌리면 다시 embedded repo 가 된다)
robocopy "C:\Users\<사용자>\Downloads\PycharmProjects\A1-1_Project" "D:\복원\A1-1_Project" /E

rem 2) 이름을 되돌린다
ren "D:\복원\A1-1_Project\.git_archived" ".git"

rem 3) 확인
git -C "D:\복원\A1-1_Project" log --oneline -5
```

`Day_count.txt` 는 받는 쪽이 덧붙인 파일이므로 필요 없으면 지우면 됩니다.

---

## 특정 시점으로 되돌리기

```cmd
set REPO=C:\Users\<사용자>\Downloads\PycharmProjects

git -C "%REPO%" tag                       rem 태그 목록 (= 스냅샷 하나하나)
git -C "%REPO%" log --oneline -20

git -C "%REPO%" checkout 2026_11_26_04_00 rem 그 시점 전체로 이동
git -C "%REPO%" checkout -                rem 원래 자리로 복귀
```

태그 시점으로 이동한 상태에서는 **스케줄 실행을 잠시 멈추십시오.** detached HEAD 상태에서
새 커밋이 쌓이면 정리가 번거로워집니다.

```cmd
schtasks /change /tn "TailscaleProjectReceive" /disable
schtasks /change /tn "TailscaleProjectReceive" /enable
```

초기화 이전 시점이 필요하면 `PycharmProjects_Archive` 의 해당 세대 폴더를 직접 보십시오.

---

## Day_count.txt

두 곳에 만듭니다. 기존 내용은 지우지 않고 파일만 추가합니다.

### 각 프로젝트 폴더 안

```
project        : A1-1_Project
backup_count   : 37
first_backup   : 2026_10_20_04_00
last_backup    : 2026_11_26_04_00
last_processed : 2026-11-26 04:31:07
```

`backup_count` 는 **이 프로젝트가 포함된 스냅샷의 수**입니다. 중간에 추가된 프로젝트는
그만큼 작고, `first_backup` 으로 언제부터 백업 대상이 됐는지 알 수 있습니다.

프로젝트 폴더는 갱신할 때마다 통째로 비워지므로 이 파일도 매번 다시 만들어집니다.
누적 횟수가 초기화되지 않는 것은 실제 상태를 `.ts_state.json` 이 들고 있기 때문입니다.

### 관리 디렉터리 최상위

```
PycharmProjects backup summary
updated        : 2026-11-26 04:31:07
snapshot_count : 37
last_snapshot  : 2026_11_26_04_00
last_reset     : 2026-08-28T04:31:02.1234567+09:00
reset_count    : 1
projects       : 23

project                                   count  last_backup         first_backup
--------------------------------------------------------------------------------------------
A1-1_Project                                 37  2026_11_26_04_00    2026_10_20_04_00
A1-2_Project                                 37  2026_11_26_04_00    2026_10_20_04_00
```

프로젝트 목록은 **가장 최근 스냅샷에 실제로 들어 있던 것**만 나옵니다. 원본에서 삭제된
프로젝트는 목록에서 빠집니다.

---

## 설정 변수

전부 `ts_receive.ps1` 상단 `CONFIG` 블록에 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `$WatchDir` | `%USERPROFILE%\Downloads` | 압축 파일이 떨어지는 곳. 하위 폴더는 훑지 않음 |
| `$RepoDir` | `$WatchDir\PycharmProjects` | 관리 디렉터리. 이름 고정 |
| `$ArchiveRoot` | `%USERPROFILE%\PycharmProjects_Archive` | 밀려난 세대를 두는 곳. **같은 볼륨에 두어야** 이동이 복사가 아닌 이름 변경이 됨 |
| `$ResetAfterDays` | `90` | 초기화 주기. `0` 이면 초기화하지 않음 |
| `$KeepArchiveGenerations` | `0` | 보관할 세대 수. `0` = 무제한(자동 삭제 안 함) |
| `$SevenZip` | `C:\Program Files\7-Zip\7z.exe` | 7z.exe 전체 경로. **필수** |
| `$WorkDir` | `C:\TempReceive` | 임시 공간과 로그. 공백 없는 경로여야 함 |
| `$MinAgeSeconds` | `120` | 이만큼 조용해진 파일만 처리 (전송 중 파일 방지) |
| `$KeepProcessedZip` | `$false` | `$true` 면 처리한 압축 파일을 `_processed` 로 이동 |
| `$LogMaxMB` | `5` | 로그 회전 기준 |

---

## 종료 코드

작업 스케줄러의 `Last Result` 로 노출됩니다.

| 코드 | 의미 |
|---|---|
| `0` | 처리할 것이 없었거나 전부 성공 |
| `1` | 치명적 실패 — git 또는 7-Zip 이 없거나 저장소를 준비하지 못함 |
| `2` | 일부 압축 파일 실패. 그 자리에 남겨 다음 회차에 재시도 (또는 `_rejected` 로 이동) |

---

## 설계 노트

### 왜 PowerShell 인가

보내는 쪽은 "압축하고 보내기" 두 명령뿐이라 배치가 맞았습니다. 받는 쪽은 파일명 파싱,
날짜 비교, JSON 상태 관리, git 호출이 필요합니다. 배치로는 코드가 몇 배로 늘고
깨지기 쉬워집니다.

### 전송 중인 파일을 잡지 않기

Taildrop 이 쓰고 있는 중간에 열면 깨진 압축을 커밋하게 됩니다. 마지막 수정 시각이
`$MinAgeSeconds` 보다 오래됐고 배타적으로 열리는 파일만 처리합니다.

### 긴 경로

`venv` 와 `node_modules` 는 260자 제한을 넘기는 경로를 흔히 만듭니다.
`Remove-Item` 이 실패하면 빈 폴더를 `robocopy /MIR` 로 미러링하는 방식으로 지웁니다.
저장소에는 `core.longpaths true` 를 설정합니다.

### `* -text`

백업이므로 git 이 줄바꿈을 정규화하면 안 됩니다. `.gitattributes` 에 `* -text` 를 써서
바이트 그대로 보관합니다. 작업 트리를 비울 때마다 사라지므로 매 회차 다시 씁니다.

### 한 회차가 오래 걸린다

**첫 회차만 오래 걸립니다.** 실측(32,095개 파일 / 6.30 GB):

| | 첫 회차 | 이후 |
|---|---|---|
| 압축 해제 | 1분 30초 | 1분 32초 |
| 스테이징 · 커밋 | 약 14분 | 1분 22초 |
| **합계** | **약 16분** | **2분 55초** |

첫 커밋은 전량을 새로 객체화하지만 이후에는 git 이 바뀐 것만 저장합니다. 압축 해제는
스냅샷 크기에 비례하므로 회차와 무관하게 일정합니다. 초기화 직후 한 회차가 다시 16분이
되는 것도 같은 이유입니다.

작업 정의의 `MultipleInstancesPolicy=IgnoreNew` 가 앞선 회차와 겹치는 것을 막습니다.
