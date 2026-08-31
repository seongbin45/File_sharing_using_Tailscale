# 받는 쪽 — 도착한 압축을 git 으로 관리하기

> 보내는 쪽은 [README](../README.md) 를 보십시오. 설치 절차는
> [scripts/receiver/install.md](../scripts/receiver/install.md) 에 있습니다.

Taildrop 이 `Downloads` 에 떨어뜨린 `<프로젝트>_<yyyy_MM_dd_HH_mm>.zip` 을 받아,
고정된 관리 디렉터리에 풀고 **압축 하나당 커밋 하나**를 남깁니다. 모든 프로젝트가 한 바퀴
갱신되면 그 시점에 **git 태그**를 찍습니다.

---

## 디렉터리 구조

```
C:\Users\<사용자>\Downloads\
    A1-1_Project_2026_08_29_07_59.zip   ← Taildrop 이 떨어뜨린 것 (처리 후 삭제)
    _rejected\                          ← 형식이 맞지 않아 걷어낸 zip
    PycharmProjects\                    ← 관리 디렉터리 (git 저장소, 이름 고정)
        .git\                           ← 관리 저장소
        .gitattributes                  ← * -text (줄바꿈 변환 금지)
        .ts_state.json                  ← 상태 원본
        Day_count.txt                   ← 전체 요약
        A1-1_Project\
            Day_count.txt               ← 이 프로젝트의 기록
            .git_archived\              ← 원래 프로젝트의 .git (이름만 바뀜)
            ...
        A1-2_Project\
            ...

C:\TempReceive\
    receive.log                         ← 실행 로그 (5MB 초과 시 .1 로 회전)
    stage\                              ← 압축 해제 임시 공간
```

관리 디렉터리 이름은 `PycharmProjects` 로 **고정**입니다. 시점 표시는 폴더 이름이 아니라
git 태그가 맡습니다. 이름을 매번 바꾸면 저장소 경로가 달라져 스크립트가 매번 탐색해야 하고,
이름 변경이 중간에 실패하면 어느 폴더가 현재 관리 디렉터리인지 판정할 수 없게 됩니다.

---

## 실행 흐름

1. 로그 회전, 저장소 준비(`git init`, 없으면 신원·옵션 설정)
2. `Downloads` 를 훑어 `<프로젝트>_<타임스탬프>.zip` 형식 파일을 타임스탬프 오름차순으로 수집
3. **전송이 끝나지 않은 파일은 건너뜀** — 마지막 수정으로부터 30초가 지났고 배타적으로 열리는
   파일만 처리합니다
4. 압축을 임시 공간에 풀고, 안의 `.git` 을 `.git_archived` 로 바꿈 (아래 참조)
5. 관리 디렉터리의 해당 프로젝트 폴더를 **지우고** 새로 푼 것을 넣음
6. `Day_count.txt` 두 개와 `.ts_state.json` 갱신
7. `git add -A` → `git commit -m "<프로젝트> @ <타임스탬프>"`
8. 처리에 성공한 zip 삭제 (또는 `_processed` 로 이동)
9. 한 바퀴가 완료됐으면 태그 생성

실패한 zip 은 그 자리에 남겨 다음 회차에 다시 시도합니다.

### 왜 폴더를 지우고 새로 푸는가

덮어쓰기만 하면 원본에서 **삭제된 파일이 관리 디렉터리에 영원히 남아** 실제 상태와 어긋납니다.
지우고 새로 풀면 커밋이 그 시점의 정확한 스냅샷이 됩니다. 디스크에서 지워도 git 이력에는
남으므로 실제로 잃는 것은 없고, `git checkout <태그>` 로 언제든 되돌릴 수 있습니다.

---

## `.git_archived` — 반드시 알아야 할 것

git 은 하위 디렉터리에 `.git` 이 있으면 그것을 **별도 저장소(embedded repository)** 로 보고
gitlink 한 줄만 기록합니다. **내부 파일은 하나도 추적되지 않습니다.**

프로젝트의 `.git` 이력 보존이 이 백업의 핵심 요구사항이므로, 압축을 풀 때 프로젝트 안의
모든 `.git` 디렉터리를 `.git_archived` 로 바꿔 넣습니다. 평범한 폴더가 되어 전부 추적됩니다.

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

git -C "%REPO%" tag                       rem 태그 목록 (= 완료된 한 바퀴들)
git -C "%REPO%" log --oneline -20         rem 커밋 목록 (= 압축 하나하나)

git -C "%REPO%" checkout 2026_08_29_07_59 rem 그 시점 전체로 이동
git -C "%REPO%" checkout -                rem 원래 자리로 복귀
```

태그 시점으로 이동한 상태에서는 **스케줄 실행을 잠시 멈추십시오.** detached HEAD 상태에서
새 커밋이 쌓이면 정리가 번거로워집니다.

```cmd
schtasks /change /tn "TailscaleProjectReceive" /disable
schtasks /change /tn "TailscaleProjectReceive" /enable
```

---

## Day_count.txt

요청하신 대로 두 곳에 만듭니다. 기존 내용은 지우지 않고 파일만 추가합니다.

### 각 프로젝트 폴더 안

```
project        : A1-1_Project
backup_count   : 7
first_backup   : 2026_08_22_09_00
last_backup    : 2026_08_29_07_59
last_processed : 2026-08-29 08:02:13
```

프로젝트 폴더는 갱신할 때마다 통째로 비워지므로, 이 파일도 매번 다시 만들어집니다.
누적 횟수가 초기화되지 않는 것은 실제 상태를 `.ts_state.json` 이 들고 있기 때문입니다.
이 텍스트 파일은 사람이 읽으라고 만드는 사본입니다.

### 관리 디렉터리 최상위

```
PycharmProjects backup summary
updated      : 2026-08-29 08:02:13
cycle_count  : 3
last_tag     : 2026_08_29_07_59
projects     : 12

project                           count  last_backup         first_backup
------------------------------------------------------------------------------------
A1-1_Project                          7  2026_08_29_07_59    2026_08_22_09_00
A1-2_Project                          6  2026_08_29_06_59    2026_08_22_10_00
```

---

## 태그 정책

`$TagMode` 로 고릅니다.

| 값 | 판정 기준 |
|---|---|
| `cycle` (기본) | 모든 프로젝트가 **직전 태그 이후** 한 번 이상 갱신됨 |
| `sameday` | 모든 프로젝트의 마지막 백업 **날짜(yyyy_MM_dd)** 가 같음 |

`sameday` 는 요청하신 "모든 폴더가 같은 날짜로 백업된 경우"를 그대로 옮긴 것입니다.
다만 **프로젝트가 24개를 넘으면 이 조건은 영원히 성립하지 않습니다.** 보내는 쪽이 매시간
한 개씩만 보내므로 하루에 최대 24개만 갱신되고, 25번째 프로젝트가 갱신될 때쯤이면 첫 번째의
날짜가 이미 어제가 되기 때문입니다. 기본값을 `cycle` 로 둔 이유입니다.

태그 이름은 그 바퀴에서 **가장 최근** 백업의 타임스탬프입니다(`2026_08_29_07_59`).

### 첫 태그가 이르게 찍히는 문제

관리 디렉터리가 처음 채워지는 동안에는 "아는 프로젝트"가 실제 전체가 아닙니다.
첫 zip 하나만 들어와도 "아는 프로젝트 전부가 갱신됨"이 성립해 태그가 찍힙니다.

보내는 쪽 프로젝트 개수를 알고 있다면 `$ExpectedProjectCount` 에 그 수를 넣으십시오.
그 수만큼 알게 되기 전에는 태그를 찍지 않습니다.

```cmd
dir /b /ad C:\Users\DiCiA\PycharmProjects | find /c /v ""
```

---

## 설정 변수

전부 `ts_receive.ps1` 상단 `CONFIG` 블록에 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `$WatchDir` | `%USERPROFILE%\Downloads` | zip 이 떨어지는 곳. 하위 폴더는 훑지 않음 |
| `$RepoDir` | `$WatchDir\PycharmProjects` | 관리 디렉터리. 이름 고정 |
| `$WorkDir` | `C:\TempReceive` | 임시 공간과 로그 |
| `$MinAgeSeconds` | `30` | 이만큼 조용해진 파일만 처리 (전송 중 파일 방지) |
| `$KeepProcessedZip` | `$false` | `$true` 면 처리한 zip 을 `_processed` 로 이동 |
| `$TagMode` | `cycle` | `cycle` 또는 `sameday` |
| `$ExpectedProjectCount` | `0` | `0` = 도착하는 대로 학습 |
| `$LogMaxMB` | `5` | 로그 회전 기준 |

---

## 종료 코드

작업 스케줄러의 `Last Result` 로 노출됩니다.

| 코드 | 의미 |
|---|---|
| `0` | 처리할 것이 없었거나 전부 성공 |
| `1` | 치명적 실패 — git 이 없거나 저장소를 준비하지 못함 |
| `2` | 일부 zip 실패. 그 자리에 남겨 다음 회차에 재시도 (또는 `_rejected` 로 이동) |

---

## 저장소 용량 — 반드시 감안할 것

**git 은 모든 버전의 모든 파일을 보관합니다.** `venv` 나 `node_modules` 처럼 파일 수가 많고
바이너리가 섞인 폴더가 포함된 채로 매 바퀴 커밋되면 저장소가 원본의 몇 배로 불어납니다.

| 대응 | 방법 |
|---|---|
| 주기적 정리 | `git -C "%REPO%" gc --aggressive --prune=now` |
| 애초에 덜 받기 | 보내는 쪽 `TAR_EXCLUDES` 에 `venv`, `node_modules` 추가 ([README](../README.md#설정-변수)) |
| 이력 끊기 | 저장소를 통째로 다른 곳에 옮겨 보관하고 관리 디렉터리를 새로 시작 |

현재 크기 확인:

```cmd
git -C "C:\Users\<사용자>\Downloads\PycharmProjects" count-objects -vH
```

---

## 설계 노트

### 왜 PowerShell 인가

보내는 쪽은 "압축하고 보내기" 두 명령뿐이라 배치가 맞았습니다. 받는 쪽은 파일명 파싱,
날짜 비교, 개수 집계, JSON 상태 관리, git 호출이 필요합니다. 배치로는 코드가 몇 배로 늘고
깨지기 쉬워집니다.

### 전송 중인 파일을 잡지 않기

Taildrop 이 쓰고 있는 중간에 열면 깨진 압축을 커밋하게 됩니다. 마지막 수정 시각이
`$MinAgeSeconds` 보다 오래됐고 배타적으로 열리는 파일만 처리합니다.

### 긴 경로

`venv` 와 `node_modules` 는 260자 제한을 넘기는 경로를 흔히 만듭니다.
`Remove-Item` 이 실패하면 빈 폴더를 `robocopy /MIR` 로 미러링하는 방식으로 지웁니다.
저장소에는 `core.longpaths true` 를 설정합니다.

### `* -text`

백업이므로 git 이 줄바꿈을 정규화하면 안 됩니다. 저장소 생성 시 `.gitattributes` 에
`* -text` 를 써서 바이트 그대로 보관합니다.

### 중복 실행

작업 정의에 `MultipleInstancesPolicy=IgnoreNew` 를 두어, 앞선 회차가 대용량 압축을 푸는
중이면 다음 트리거는 건너뜁니다.
