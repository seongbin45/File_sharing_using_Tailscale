# 다른 PC / 다른 대상으로 이식하기

이 백업 시스템은 `PycharmProjects` 전용이 아닙니다. **"여러 폴더가 들어 있는 부모 디렉터리"**
라면 무엇이든 대상이 될 수 있습니다. 사진 폴더, 문서 폴더, 다른 워크스페이스 모두 동일하게
동작합니다.

이식할 때 손대야 하는 값을 전부 아래에 모았습니다. **여기 없는 값은 고칠 필요가 없습니다.**

---

## 전제 조건

| 항목 | 확인 명령 | 비고 |
|---|---|---|
| `tar` (bsdtar) | `tar --version` | Windows 10 1803 이상 기본 포함 |
| `wscript` | `where wscript` | 창 숨김 래퍼 실행에 필요 |
| `tailscale` | `tailscale status` | PATH 에 잡혀 있어야 함 |
| PowerShell | `powershell -NoProfile -Command "$PSVersionTable.PSVersion"` | 5.1 이상. 타임스탬프·보존 정리에 사용 |

관리자 권한은 필요 없습니다. 일반 사용자 권한의 SSH(cmd) 세션만으로 전부 설치됩니다.

---

## 1. `scripts/ts_backup.bat`

상단 `CONFIG` 블록만 고치면 됩니다. 그 아래 로직은 건드릴 필요가 없습니다.

| 변수 | 예시 값 | 설명 |
|---|---|---|
| `BASE_DIR` | `C:\Users\DiCiA\PycharmProjects` | **필수 변경.** 순환 대상 폴더들의 부모 디렉터리 |
| `STATE_FILE` | `C:\Users\DiCiA\backup_state.txt` | **필수 변경.** 순환 커서. 쓰기 권한이 있는 곳이면 어디든 무방 |
| `WORK_DIR` | `C:\TempBackup` | 압축 파일 생성 위치. 대상 폴더 중 가장 큰 것보다 여유 공간이 있어야 함 |
| `TARGETS` | `wisenesco-23031302 laptop-7gmpubqc ...` | **필수 변경.** 공백 구분, 순서가 곧 우선순위. `tailscale status` 의 이름과 정확히 일치해야 함 |
| `PENDING_KEEP_DAYS` | `3` | 전송 실패분 보관 기한 |
| `PENDING_KEEP_PER_PROJECT` | `1` | 프로젝트당 실패분 최대 개수 |
| `LOG_MAX_MB` | `5` | 로그 회전 기준 |
| `TAR_EXCLUDES` | (비어 있음) | 압축 제외 패턴. 아래 참고 |
| `DRY_RUN` | `0` | `1` 이면 압축만 하고 전송하지 않음. 최초 테스트용 |

### 제외 패턴을 쓰려면

`bsdtar` 패턴이며 `*/이름/*` 형태입니다.

```bat
set "TAR_EXCLUDES=--exclude=*/venv/* --exclude=*/.venv/* --exclude=*/__pycache__/* --exclude=*/node_modules/*"
```

`.git` 과 `.env` 를 제외하면 백업의 의미가 사라지므로 넣지 마십시오.

---

## 2. `scripts/ts_backup_task.xml`

| 위치 | 값 | 설명 |
|---|---|---|
| `<UserId>` (2곳) | `__USERID__` | 설치 시 치환. 직접 편집하지 말 것 — [install.md](../scripts/install.md) 4단계 참조 |
| `<Arguments>` | `"C:\Scripts\ts_backup_hidden.vbs"` | **스크립트를 다른 폴더에 둘 경우 변경** |
| `<Description>` | 대상 경로가 문장에 박혀 있음 | 선택. 작업 스케줄러 목록에 표시됨 |
| `<URI>` | `\TailscaleProjectBackup` | 작업 이름을 바꾸려면 `schtasks /tn` 값과 함께 변경 |
| `<StartBoundary>` | `2026-01-01T00:00:00` | 과거 시각이면 무엇이든 무방. 여기에 매시간 반복이 얹힘 |
| `<Interval>` | `PT1H` | 실행 주기. `PT30M`, `PT6H` 등 ISO 8601 기간 |

### 트리거를 바꾸려면

- **매시간 주기 변경** → `CalendarTrigger` 의 `<Interval>`
- **부팅 시 실행 제거** → `LogonTrigger` 블록 삭제
- **절전 해제 감지 제거** → `EventTrigger` 블록 삭제

`ONSTART`(시스템 시작 시)는 쓰지 않습니다. 비관리자 세션에서 등록이 거부되기 쉽고,
사용자 컨텍스트 작업은 어차피 로그온 전에 실행되지 않아 `LogonTrigger` 와 실질적으로 같습니다.

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
1. 전제 조건 확인            (tar / wscript / tailscale / powershell)
2. 파일 3개 배치             (bat, vbs, xml 을 같은 폴더에)
3. ts_backup.bat CONFIG 수정 (최소 BASE_DIR, STATE_FILE, TARGETS)
4. DRY_RUN=1 로 압축만 테스트
5. DRY_RUN=0 으로 전송 테스트 → 수신 기기 도착 확인
6. 롤백 테스트               (1순위를 가짜 이름으로)
7. XML 치환 후 schtasks 등록
8. schtasks /run 으로 즉시 실행 → LastTaskResult 확인
```

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
