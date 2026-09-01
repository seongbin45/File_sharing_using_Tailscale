# 다른 PC / 다른 대상으로 이식하기

> 시스템이 무엇을 하고 왜 이런 구조인지는 [README](../README.md) 를 먼저 보십시오.

이 백업 시스템은 `PycharmProjects` 전용이 아닙니다. **"여러 폴더가 들어 있는 부모 디렉터리"**
라면 무엇이든 대상이 될 수 있습니다. 사진 폴더, 문서 폴더, 다른 워크스페이스 모두 동일하게
동작합니다.

이식할 때 손대야 하는 값을 전부 아래에 모았습니다. **여기 없는 값은 고칠 필요가 없습니다.**

---

## 전제 조건

| 항목 | 확인 명령 | 비고 |
|---|---|---|
| **7-Zip** | `dir "C:\Program Files\7-Zip\7z.exe"` | 양쪽 PC 모두 필수. PATH 에 등록되지 않으므로 전체 경로를 설정값으로 씀 |
| `wscript` | `where wscript` | 창 숨김 래퍼 실행에 필요 |
| `tailscale` | `tailscale status` | PATH 에 잡혀 있어야 함 |
| PowerShell | `powershell -NoProfile -Command "$PSVersionTable.PSVersion"` | 5.1 이상. 타임스탬프·보존 정리에 사용 |

관리자 권한은 필요 없습니다. 일반 사용자 권한의 SSH(cmd) 세션만으로 전부 설치됩니다.

---

## 1. `scripts/ts_backup.bat`

상단 `CONFIG` 블록만 고치면 됩니다. 그 아래 로직은 건드릴 필요가 없습니다.

| 변수 | 예시 값 | 설명 |
|---|---|---|
| `BASE_DIR` | `C:\Users\DiCiA\PycharmProjects` | **필수 변경.** 통째로 압축할 디렉터리 |
| `WORK_DIR` | `C:\TempBackup` | 압축 파일 생성 위치. `BASE_DIR` 전체가 올라갈 여유 공간이 필요 |
| `MIN_FREE_MB` | `10000` | 이보다 여유가 적으면 시작하지 않음. 대상 크기에 맞게 조정 |
| `SEVENZIP` | `C:\Program Files\7-Zip\7z.exe` | **설치 경로가 다르면 필수 변경** |
| `SEVENZIP_LEVEL` | `5` | LZMA2 수준. `1` 은 빠르고, `9` 는 시간·메모리를 크게 씀 |
| `TARGETS` | `wisenesco-23031302 laptop-7gmpubqc ...` | **필수 변경.** 공백 구분, 순서가 곧 우선순위. `tailscale status` 의 이름과 정확히 일치해야 함 |
| `PENDING_KEEP_DAYS` | `3` | 전송 실패분 보관 기한 |
| `PENDING_KEEP_COUNT` | `1` | 전송 실패분 최대 개수 |
| `LOG_MAX_MB` | `5` | 로그 회전 기준 |
| `EXCLUDE_LIST` | (비어 있음) | 제외할 이름을 한 줄에 하나씩 적은 목록 파일 경로. 아래 참고 |
| `DRY_RUN` | `0` | `1` 이면 압축만 하고 전송하지 않음. 최초 테스트용 |

순환 커서가 없으므로 상태 파일도 없습니다. 매 회차가 서로 독립적입니다.

### 제외를 쓰려면

제외할 이름을 한 줄에 하나씩 적은 텍스트 파일을 만들고, 그 경로를 `EXCLUDE_LIST` 에 넣습니다.
경로에 공백이 없어야 합니다.

```
venv
.venv
__pycache__
node_modules
```

7-Zip 자체의 `-xr!이름` 스위치는 쓰지 마십시오. 배치에 지연 확장이 켜져 있어 `!` 가 먹히고
제외가 조용히 무시됩니다.

`.git` 과 `.env` 를 제외하면 백업의 의미가 사라지므로 넣지 마십시오.

---

## 2. `scripts/ts_backup_task.xml`

| 위치 | 값 | 설명 |
|---|---|---|
| `<UserId>` (2곳) | `__USERID__` | 설치 시 치환. 직접 편집하지 말 것 — [install.md](../scripts/install.md) 4단계 참조 |
| `<Arguments>` | `"C:\Scripts\ts_backup_hidden.vbs"` | **스크립트를 다른 폴더에 둘 경우 변경** |
| `<Description>` | 대상 경로가 문장에 박혀 있음 | 선택. 작업 스케줄러 목록에 표시됨 |
| `<URI>` | `\TailscaleProjectBackup` | 작업 이름을 바꾸려면 `schtasks /tn` 값과 함께 변경 |
| `<StartBoundary>` | `2026-01-01T04:00:00` | **실행 시각.** 날짜 부분은 과거이기만 하면 무방하고, 시각 부분이 매일 언제 도는지를 정합니다 |
| `<DaysInterval>` | `1` | 며칠에 한 번 돌지 |

### 트리거를 바꾸려면

- **실행 시각 변경** → `<StartBoundary>` 의 시각 부분
- **이틀에 한 번** → `<DaysInterval>` 을 `2` 로

보내는 쪽에는 부팅·절전 해제 트리거가 **일부러 없습니다.** 한 회차가 대상 전체를 압축·전송
하므로(6 GB 기준 20분) 로그온이나 절전 해제마다 한 번 더 도는 것은 부담만 큽니다.
`StartWhenAvailable` 이 그 역할을 대신해, 지정 시각에 PC 가 꺼져 있었다면 다음에 켜졌을 때
한 번 보충 실행됩니다.

`ONSTART`(시스템 시작 시)도 쓰지 않습니다. 비관리자 세션에서 등록이 거부되기 쉽고,
사용자 컨텍스트 작업은 어차피 로그온 전에 실행되지 않습니다.

---

## 3. `scripts/ts_backup_hidden.vbs`

**수정할 것이 없습니다.** 자기 자신이 놓인 폴더에서 `ts_backup.bat` 을 찾으므로,
두 파일을 같은 폴더에 함께 두기만 하면 어느 경로에서도 동작합니다.

---

## 4. 설치 경로

문서 전반에서 `C:\Scripts` 를 씁니다. 바꾸려면 세 곳이 함께 바뀌어야 합니다.

1. 파일을 복사할 실제 경로
2. `ts_backup_task.xml` 의 `<Arguments>`
3. [install.md](../scripts/install.md) 의 명령어들

---

## 이식 절차 요약

```
1. 전제 조건 확인            (7-Zip / wscript / tailscale / powershell / 여유 공간)
2. 파일 3개 배치             (bat, vbs, xml 을 같은 폴더에)
3. ts_backup.bat CONFIG 수정 (최소 BASE_DIR, TARGETS, SEVENZIP)
4. DRY_RUN=1 로 압축만 테스트 → 압축 크기와 소요 시간 확인
5. DRY_RUN=0 으로 전송 테스트 → 수신 기기 도착 확인
6. 롤백 테스트               (1순위를 가짜 이름으로)
7. XML 치환 후 schtasks 등록
8. schtasks /run 으로 즉시 실행 → LastTaskResult 확인
```

4단계에서 나온 압축 크기와 전송 시간이 하루 1회로 감당 가능한지 먼저 판단하십시오.
감당이 안 되면 `<DaysInterval>` 을 늘리거나 `EXCLUDE_LIST` 를 도입해야 합니다.

각 단계의 구체적인 명령은 [install.md](../scripts/install.md) 에 있습니다.
검증 시 무엇을 근거로 판정하는지는 [VERIFICATION.md](VERIFICATION.md) 를 참고하십시오.

---

## 수신 기기 쪽 전제

보내는 쪽이 성공해도 **받는 쪽에서 Taildrop 수신이 준비되어 있지 않으면 파일은 도착하지
않습니다.** 기기 종류별로 다릅니다.

- **모바일 / 데스크톱 앱** — Taildrop 수신 허용이 켜져 있어야 하고, 앱에 따라 수신 알림을
  수락해야 저장됩니다
- **CLI 전용 기기** — `tailscale file get <저장경로>` 를 실행해야 실제 파일로 떨어집니다.
  상시 수신하려면 이 명령을 반복 실행하는 상주 프로세스나 스케줄이 필요합니다
  (대기 옵션은 버전에 따라 다르므로 `tailscale file get --help` 로 확인하십시오)

이 저장소의 스크립트는 **보내는 쪽만** 담당합니다. 받는 쪽의 저장·정리는 범위 밖입니다.
