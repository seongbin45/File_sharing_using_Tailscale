# 검증 기록

> 시스템 구조와 용어는 [README](../README.md) 를 참고하십시오.

2026-08-30, 실제 기기에서 수행한 검증의 전문입니다. 각 항목은 **무엇을 어떻게 확인했고
실제 출력이 무엇이었는지**를 남깁니다. 이후 코드를 고칠 때 회귀 테스트의 기준으로 쓰십시오.

## 검증 환경

| 항목 | 값 |
|---|---|
| 송신 PC | `DESKTOP-NB8BFUR`, 계정 `dicia` |
| OS | Windows, 한국어 표시 언어, 도메인 미가입(WORKGROUP) |
| 접근 경로 | OpenSSH 서버 → cmd, **비관리자 권한** |
| 대상 | `C:\Users\DiCiA\PycharmProjects` |
| 수신 기기 | `wisenesco-23031302` (1순위), `laptop-7gmpubqc`, `desktop-dvj3pqk`, `desktop-0g92n63` |
| 테스트 프로젝트 | `A1-1_Project`(zip 63,530 B), `A1-2_Project`(zip 343,628,118 B) |

---

## 1. 압축 · 전송 기본 동작

**결과: 통과**

```
[16:34:43.94] target folder: A1-2_Project (previous: A1-1_Project)
[16:34:43.94] creating archive: C:\TempBackup\A1-2_Project_2026_08_30_16_34.zip
[16:34:50.91] archive ready, 343628118 bytes
[16:35:37.93] sent A1-2_Project_2026_08_30_16_34.zip -> wisenesco-23031302
[16:35:37.97] sent and removed local archive
[16:35:37.98] === run end (exit 0) ===
```

- 압축 328MB 에 약 7초, 전송에 약 47초
- 파일명 타임스탬프 `_2026_08_30_16_34` 정상
- 전송 후 로컬 zip 삭제 확인
- **수신 기기에서 파일 도착을 육안 확인함**

---

## 2. 폴더 순환

**결과: 통과**

```
[16:26:06.44] target folder: A1-1_Project (previous: )
[16:34:43.94] target folder: A1-2_Project (previous: A1-1_Project)
```

- 최초 실행(상태 파일 없음) → 첫 폴더 선택
- 두 번째 실행 → 다음 폴더로 이동, `previous` 에 직전 폴더 기록
- 상태 파일 삭제 시 첫 폴더로 복귀

---

## 3. 전송 롤백

**결과: 통과.** 이 검증으로 **`tailscale file cp` 가 실패 시 0 이 아닌 종료 코드를 반환한다**는
전제가 확인되었습니다. 롤백 로직 전체가 이 전제에 의존합니다.

`TARGETS` 첫 항목을 존재하지 않는 이름으로 바꿔 실행:

```
error looking up IP of "nosuchdevice-test": lookup nosuchdevice-test: no such host
[16:39:08.05] failed  A1-1_Project_2026_08_30_16_39.zip -> nosuchdevice-test
[16:39:08.33] sent A1-1_Project_2026_08_30_16_39.zip -> laptop-7gmpubqc
[16:39:08.34] === run end (exit 0) ===
```

- 1순위 실패 → 2순위 승계
- `tailscale` 자체 오류 메시지도 로그에 함께 기록됨
- 2순위 기기에서도 도착 확인

---

## 4. pending 보관 · 재전송 · 보존

**결과: 통과**

`TARGETS` 4개 전부를 가짜 이름으로 바꿔 실행:

```
[16:42:28.07] failed  A1-1_Project_2026_08_30_16_42.zip -> nodev1
[16:42:30.82] failed  A1-1_Project_2026_08_30_16_42.zip -> nodev2
[16:42:33.58] failed  A1-1_Project_2026_08_30_16_42.zip -> nodev3
[16:42:36.33] failed  A1-1_Project_2026_08_30_16_42.zip -> nodev4
[16:42:36.34] all targets failed - archive moved to pending, will retry next run
[16:42:36.61] === run end (exit 2) ===
```

원복 후 재실행:

```
[16:43:22.86] === run start ===
[16:43:23.17] sent A1-1_Project_2026_08_30_16_42.zip -> wisenesco-23031302
[16:43:23.17] pending flushed: A1-1_Project_2026_08_30_16_42.zip
[16:43:23.37] target folder: A1-1_Project (previous: )
[16:43:23.37] creating archive: C:\TempBackup\A1-1_Project_2026_08_30_16_43.zip
```

- 4개 전부 순차 시도 후 pending 이동, 종료 코드 `2`
- 다음 실행에서 **새 압축보다 먼저** pending 재전송
- 성공 후 pending 폴더 비워짐 (`dir` 로 0개 파일 확인)

---

## 5. 작업 스케줄러 등록 · 실행

**결과: 통과**

```
LastRunTime        : 2026-08-30 오후 4:54:53
LastTaskResult     : 0
NextRunTime        : 2026-08-30 오후 5:00:00
NumberOfMissedRuns : 0

MultipleInstances  : IgnoreNew
StartWhenAvailable : True
ExecutionTimeLimit : PT2H
```

- XML 임포트 성공, 트리거 3종(`CalendarTrigger` PT1H / `LogonTrigger` / `EventTrigger`) 등록
- `EventTrigger` 의 `Power-Troubleshooter ... EventID=1` 구독 확인
- 스케줄러가 VBS 를 통해 실행한 회차가 로그에 정상 기록되고 `exit 0`

---

## 미검증 항목

| 항목 | 이유 |
|---|---|
| **창 숨김 동작** | SSH 로는 물리 화면을 볼 수 없음. 정시 실행 때 본체 모니터 확인 필요 |
| **절전 해제 트리거** | 실제로 절전 → 복귀시켜 로그에 실행 기록이 남는지 확인 필요 |
| **로그온 트리거** | 재부팅 후 확인 필요 |
| **놓친 실행 보충** | PC 를 꺼 둔 뒤 켜서 `NumberOfMissedRuns` 와 보충 실행 확인 필요 |
| **로그 회전** | 로그가 5MB 를 넘어야 발동. 실사용 중 자연 도달 |
| **pending 3일 경과 삭제** | `LastWriteTime` 을 과거로 조작해야 확인 가능 |

---

## 검증 과정에서 발견해 수정한 결함

### 배포 전 코드 리뷰에서 잡은 것

| 결함 | 증상 |
|---|---|
| `if <조건> A & B` | `B` 가 조건과 무관하게 항상 실행 → **롤백 2~4순위가 완전히 죽음** |
| `echo %VAR% > file` | 값 뒤 공백 기록 → 비교 영구 실패 → **매번 첫 폴더만 백업** |
| `tar -C <경로> *` | `*` 가 현재 디렉터리 기준으로 해석될 위험 |
| robocopy 종료 코드 | 성공 시에도 1~7 반환. `equ 0` 판정은 오판 (robocopy 제거로 해소) |

### 실제 실행 중 드러난 것

| 커밋 | 결함 | 증상 |
|---|---|---|
| `ea5bd94` | 로그 메시지의 `>` 가 리디렉션으로 승격 | `sent x.zip -> host` 한 줄이 로그가 아니라 `host` 라는 이름의 파일로 빠져나감. **어느 기기가 받았는지 알 수 없게 됨** |
| `37d6ed0` | `%USERDOMAIN%` 이 `WORKGROUP` 반환 | `schtasks` 등록 시 `계정 이름과 보안 식별자 사이에 매핑이 이루어지지 않았습니다` |
| `8e86d69` | `findstr /i "Last"` | 한글 Windows 에서 `마지막 결과` 로 출력되어 매칭 실패. **잘못된 값을 못 잡고 지나침** |

증상별 대처는 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 를 참고하십시오.
