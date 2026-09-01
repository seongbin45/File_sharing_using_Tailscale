# 웹 관리 콘솔 (FastAPI)

`ts_console.ps1` 은 한 대의 PC 에 SSH 로 들어가야 볼 수 있습니다. 이 콘솔은 **양쪽 PC 를
한 화면에서** 보고, 각각의 **터미널을 브라우저에서 직접** 조작합니다.

```
브라우저 ──HTTP/WS──▶ FastAPI ──SSH(paramiko)──▶ 보내는 PC  (desktop-nb8bfur)
                              └───SSH──────────▶ 받는 PC    (wisenesco-23031302)
                                     │
                                 Tailscale 위
```

---

## Tailscale 은 어떻게 관여하는가

**FastAPI 가 Tailscale 을 중계하지 않습니다. 중계할 것이 없습니다.**

Tailscale 은 프록시가 아니라 네트워크입니다. 이 서버가 돌아가는 기기를 tailnet 에 넣으면
그 순간부터 `desktop-nb8bfur` 같은 MagicDNS 이름이 그냥 해석되고, 평범한 SSH 연결이
그대로 됩니다. 애플리케이션 쪽에 Tailscale 용 코드나 설정은 한 줄도 없습니다.

```cmd
tailscale status          :: 이 기기가 tailnet 에 있는지
ping desktop-nb8bfur      :: 이름이 100.x.y.z 로 풀리는지
```

이름이 안 풀리면 MagicDNS 가 꺼져 있는 것이니 `hosts.json` 의 `address` 에 `100.x.y.z`
주소를 직접 넣으면 됩니다.

**tailnet 에 없는 기기는 이 방식으로 닿을 수 없고, 코드로 우회할 수도 없습니다.**
그 기기를 tailnet 에 넣는 것이 유일한 해법입니다. 클라우드에 중계 서버를 두는 방법도
있지만, 그러면 SSH 자격 증명이 제3자 인프라를 지나가게 됩니다 —
이 프로젝트가 Tailscale 을 고른 이유가 정확히 그것을 피하기 위해서입니다.

### 어디에 띄우는가

tailnet 안이라면 어디든 됩니다. 실용적인 선택지:

| 위치 | 장점 | 단점 |
|---|---|---|
| **받는 PC** | 늘 켜져 있음, 스냅샷 저장소와 같은 기기 | 그 PC 가 죽으면 콘솔도 죽음 |
| 노트북 | 설치가 제일 간단 | 노트북이 꺼지면 못 봄 |
| 항상 켜진 소형 서버 | 양쪽과 독립적 | 기기 하나 더 |

---

## 설치

관리자 권한은 필요 없습니다.

```cmd
python -m venv C:\Scripts\webadmin-venv
C:\Scripts\webadmin-venv\Scripts\python -m pip install -r C:\Scripts\src\webadmin\requirements.txt
```

파이썬이 없으면 [docs/SETUP_TOOLS.md](../docs/SETUP_TOOLS.md) 와 같은 방식으로 터미널만으로
설치할 수 있습니다.

---

## 호스트 등록

```cmd
copy C:\Scripts\src\webadmin\hosts.example.json C:\Scripts\src\webadmin\hosts.json
```

`hosts.json` 을 열어 `address`, `username` 을 실제 값으로 고칩니다.
**이 파일은 `.gitignore` 에 들어 있어 커밋되지 않습니다.**

비밀번호는 두 가지 방법이 있습니다.

| 방법 | 언제 |
|---|---|
| `password` 를 `null` 로 두고 **화면에서 입력** | 기본. 서버 메모리에만 남고 재시작하면 사라짐 |
| `hosts.json` 에 직접 기입 | 무인 재시작이 필요할 때. **평문으로 디스크에 남습니다** |

---

## 실행

```cmd
:: 화면만 먼저 보기 (실제 기기에 접속하지 않음)
C:\Scripts\webadmin-venv\Scripts\python -m app.main

:: 실제 기기에 접속
C:\Scripts\webadmin-venv\Scripts\python -m app.main --ssh
```

`webadmin` 디렉터리에서 실행해야 합니다. 브라우저로 <http://127.0.0.1:8765> 를 엽니다.

### 다른 기기에서 보려면

기본값은 **루프백(127.0.0.1)** 이라 그 PC 에서만 열립니다. tailnet 의 다른 기기에서도
보려면 그 PC 의 **tailnet 주소**에 바인딩합니다.

```cmd
python -m app.main --ssh --host 100.101.102.103
```

그러면 tailnet 안에서만 닿고 바깥에서는 보이지 않습니다.

> **`--host 0.0.0.0` 을 쓰지 마십시오.** 이 콘솔에는 자체 로그인이 없습니다. 남의
> 컴퓨터에서 명령을 실행하는 도구이므로, 공인 IP 가 붙은 인터페이스에 열면 그대로
> 무인증 원격 실행 창구가 됩니다.

---

## 화면

왼쪽은 **`tailscale status` 가 보고하는 기기 목록**입니다. `hosts.json` 에 등록된 기기는
역할 태그(보내는 쪽 / 받는 쪽)와 `SSH` 태그가 붙고, 등록되지 않은 tailnet 기기도 그대로
보입니다 — 클릭하면 터미널이 아니라 연결 설정으로 갑니다.

| 탭 | 내용 |
|---|---|
| **개요** | 양쪽을 한 화면에. 스케줄러 상태, 압축 대상, 전송 대상, pending, 스냅샷, 저장소 크기, 최근 로그 |
| **터미널** | 해당 PC 의 셸. 입력이 그대로 전달됩니다. 아래 `빠른 명령` 은 자주 쓰는 것을 한 번에 |
| **연결 설정** | 주소·계정·포트·역할·연결 경로. `연결 테스트` 로 저장 전에 확인 |

개요 탭은 **선택한 기기가 아니라 백업 시스템 전체**를 보여줍니다. 사이드바 선택은 터미널과
연결 설정에 적용됩니다.

`즉시 실행`, `일시 중지 / 재개` 는 `ts_console.ps1` 과 같은 동작입니다.
**지우거나 초기화하는 버튼은 없습니다** — 되돌릴 수 없는 것은 터미널에서 직접 치는 편이
낫습니다.

### 연결 경로

| 경로 | 어떻게 붙는가 | 상태 |
|---|---|---|
| **Tailscale IP + OpenSSH** | `100.x` 주소 22번 포트 직결, 비밀번호 인증 | 기본값 |
| **Tailscale SSH** | `tailscaled` 가 tailnet 신원으로 인가. 비밀번호를 아예 보내지 않음 | 대상에 `tailscale up --ssh` 필요. **실기 검증 전** |
| **점프 호스트 경유** | 접속 가능한 다른 SSH 서버를 거침 (표준 ProxyJump) | tailnet 이 닿지 않는 기기용 |

**"릴레이"는 점프 호스트로 구현했습니다.** 공개 클라우드 중계 서비스를 쓰면 SSH 자격
증명이 제3자 인프라를 지나갑니다 — 이 프로젝트가 Tailscale 을 고른 이유가 그것을 피하려던
것이라, 같은 문제를 관리 콘솔에서 되살릴 이유가 없습니다. ProxyJump 는 같은 목적을
달성하면서 자격 증명이 본인 소유 기기 사이에만 머뭅니다.

---

## 구조

```
webadmin/
  app/
    main.py          FastAPI 라우팅 · WebSocket 터미널
    config.py        hosts.json 로딩, 비밀번호 보관(메모리)
    backends.py      Backend 인터페이스 + MockBackend
    sshbackend.py    paramiko 구현
    devices.py       tailscale status 파싱 · 등록 호스트와 병합
    static/          화면 (빌드 도구 없음, 그냥 파일 3개)
  tests/selftest.py  테스트 러너 없이 도는 자체 점검
  hosts.example.json
  requirements.txt
```

### 자체 점검

```cmd
cd C:\Scripts\src\webadmin
python -m tests.selftest
```

pytest 같은 것을 설치하지 않고 그대로 돕니다. SSH 서버 없이도 스레드↔이벤트 루프 브리지,
PowerShell 과의 base64/JSON 규약, 기기 병합, `hosts.json` 왕복, 실패 경로 전부를 덮습니다.

**MockBackend 를 남겨 둔 이유**가 있습니다. 화면을 고칠 때마다 실제 기기에 붙을 필요가
없고, tailnet 이 없는 곳에서도 화면이 돌아갑니다. `--ssh` 없이 실행하면 MOCK 이고,
화면 우측 상단 배지와 터미널 배너가 그 사실을 계속 알려 줍니다.

### 터미널 프로토콜

WebSocket **프레임 종류**가 곧 의미입니다.

| 프레임 | 뜻 |
|---|---|
| 바이너리 | 터미널 바이트 (양방향) |
| 텍스트 | JSON 제어 메시지 (`ready` / `closed` / `error` / `resize`) |

페이로드 내용으로 구분하지 않습니다. 터미널은 정의상 임의의 바이트를 뱉으므로
"`{` 로 시작하면 제어 메시지" 같은 규칙은 그런 출력을 내는 명령이 나오는 순간 깨집니다.
