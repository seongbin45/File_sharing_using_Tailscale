# 배포

> 먼저 읽으십시오. 이 콘솔은 **다른 컴퓨터의 셸을 그대로 열어 줍니다.**
> 공개 주소에 올리는 것은 설정 문제가 아니라 결정입니다.

---

## 올리기 전에 — 무엇을 넘기는 것인가

Render 같은 PaaS 에 올리면 세 가지가 제3자 인프라로 넘어갑니다.

| 넘어가는 것 | 결과 |
|---|---|
| **tailnet 인증 키** | 그 컨테이너가 당신의 tailnet 노드가 됩니다 |
| **양쪽 PC 의 SSH 비밀번호** | 환경변수로 저장됩니다 |
| **공개 URL** | 비밀번호 하나가 집 PC 두 대의 셸을 지킵니다 |

이 프로젝트가 애초에 Tailscale 을 고른 이유가 "파일이 남의 서버를 지나가지 않게"였습니다.
관리 콘솔을 남의 서버에 올리면 **그 판단을 뒤집는 것**입니다. 그래도 필요하다면(외출 중에
휴대폰으로 봐야 한다든가) 아래 절차가 있고, 코드도 그에 맞게 고쳐 뒀습니다.

### 그전에 — 더 나은 선택지 두 개

**1. Tailscale Funnel** — tailnet 안에 그대로 두고 HTTPS 만 밖으로 냅니다.
SSH 비밀번호도 tailnet 키도 남의 서버에 두지 않습니다.

```cmd
python -m app.main --ssh --host 127.0.0.1 --port 8765
tailscale funnel 8765
```

**2. tailnet 주소에 바인딩** — 밖에서 볼 필요가 없다면 이게 정답입니다.
휴대폰에도 Tailscale 을 깔면 그대로 보입니다.

```cmd
python -m app.main --ssh --host 100.101.7.4
```

둘 다 안 되는 상황이라면 계속 읽으십시오.

---

## 코드가 강제하는 것

```
$ python -m app.main --host 0.0.0.0

거부: 0.0.0.0 에 바인딩하려면 TSCONSOLE_PASSWORD 가 필요합니다.
```

**루프백이 아닌 주소에 바인딩하면서 비밀번호가 없으면 프로세스가 뜨지 않습니다.**
설득해서 넘어갈 수 있는 옵션이 아닙니다. 로그인 없이 뜬 콘솔은 되돌릴 수 없고,
안 뜨는 콘솔은 로그를 보면 됩니다.

로그인이 가리는 범위:

| 경로 | 인증 |
|---|---|
| `/healthz` | 없음 (플랫폼이 살아있는지 확인용. 호스트 정보 노출 안 함) |
| `/login` | 없음 |
| 그 외 전부 — `/`, `/static/*`, `/api/*` | 필요 |
| **`WS /api/hosts/{id}/terminal`** | **필요** |

WebSocket 은 따로 검사합니다. `@app.middleware("http")` 는 WebSocket 스코프를 보지
못하므로, 그 사실을 모르면 **화면은 잠기고 셸만 열려 있는** 상태가 됩니다.
`tests/authtest.py` 가 실제로 붙어서 확인합니다.

---

## Render 배포

### 1. Tailscale 인증 키

<https://login.tailscale.com/admin/settings/keys> 에서 **Reusable** 키를 만듭니다.
**태그를 붙이십시오** (`tag:ts-control` 등). 나중에 이 노드만 골라서 취소할 수 있습니다.

ACL 에서 이 태그가 **양쪽 PC 의 22번 포트에만** 닿게 좁히는 것을 권합니다.

```jsonc
// tailnet ACL
{
  "tagOwners": { "tag:ts-control": ["autogroup:admin"] },
  "acls": [
    {
      "action": "accept",
      "src": ["tag:ts-control"],
      "dst": ["desktop-nb8bfur:22", "wisenesco-23031302:22"]
    }
  ]
}
```

이걸 해 두면 컨테이너가 털려도 **tailnet 전체가 아니라 그 두 포트만** 노출됩니다.

### 2. Blueprint 로 생성

Render → **New → Blueprint** → 이 저장소 선택. `webadmin/render.yaml` 을 읽습니다.
`Dockerfile` 은 `tailscaled` 를 **userspace 모드**로 띄웁니다 — 컨테이너에는
`/dev/net/tun` 이 없어 일반 노드가 될 수 없기 때문입니다.

### 3. 환경변수

대시보드 → Environment 에서 넣습니다. `render.yaml` 에 `sync: false` 로 표시된 것들입니다.

| 이름 | 값 | 설명 |
|---|---|---|
| `TS_AUTHKEY` | `tskey-auth-...` | 1단계의 재사용 키 |
| `TSCONSOLE_PASSWORD` | 충분히 긴 임의 문자열 | **콘솔 로그인.** 이게 유일한 방벽입니다 |
| `TSCONSOLE_SECRET` | (자동 생성됨) | 쿠키 서명. 없으면 재배포마다 로그아웃 |
| `TSCONSOLE_HOSTS_JSON` | 아래 참조 | 호스트 목록 |
| `TSCONSOLE_PASSWORD_SENDER` | 보내는 PC 의 SSH 비밀번호 | |
| `TSCONSOLE_PASSWORD_RECEIVER` | 받는 PC 의 SSH 비밀번호 | |

`TSCONSOLE_PASSWORD` 는 직접 짓지 마십시오.

```cmd
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

**`TSCONSOLE_HOSTS_JSON`** — 한 줄로 넣습니다. **비밀번호는 넣지 마십시오.**
`TSCONSOLE_PASSWORD_<ID>` 쪽에서 따로 읽습니다.

```json
{"hosts":[{"id":"sender","label":"보내는 PC","role":"sender","address":"desktop-nb8bfur","username":"dicia","task":"TailscaleProjectBackup","scripts_dir":"C:\\Scripts","work_dir":"C:\\TempBackup","path":"direct"},{"id":"receiver","label":"받는 PC","role":"receiver","address":"wisenesco-23031302","username":"emergency","task":"TailscaleProjectReceive","scripts_dir":"C:\\Scripts","work_dir":"C:\\TempReceive","path":"direct"}]}
```

호스트 `id` 가 비밀번호 변수 이름을 정합니다: `sender` → `TSCONSOLE_PASSWORD_SENDER`.
영숫자가 아닌 문자는 `_` 가 되고 전부 대문자가 됩니다 (`100-9-9-9` →
`TSCONSOLE_PASSWORD_100_9_9_9`).

### 4. 나머지 (render.yaml 에 이미 있음)

| 이름 | 값 | 왜 |
|---|---|---|
| `TSCONSOLE_BACKEND` | `ssh` | MOCK 이 아닌 실제 접속 |
| `TSCONSOLE_BIND` | `0.0.0.0` | 컨테이너 안이므로 필요 |
| `TSCONSOLE_SOCKS5` | `localhost:1055` | **userspace tailscaled 에는 인터페이스가 없습니다** |
| `PORT` | (Render 가 주입) | 고르는 값이 아닙니다 |

`TSCONSOLE_SOCKS5` 가 핵심입니다. userspace 모드에서는 tailnet 이 네트워크가 아니라
**SOCKS5 프록시**로 노출되므로, paramiko 가 그리로 다이얼해야 합니다. 이름 해석도
프록시가 합니다(`rdns=True`) — MagicDNS 이름은 tailnet 안에서만 의미가 있어서
컨테이너가 먼저 풀려고 하면 전부 실패합니다.

### 5. 확인

```bash
curl https://ts-control-xxxx.onrender.com/healthz
# {"ok":true,"backend":"ssh"}

curl -i https://ts-control-xxxx.onrender.com/api/devices
# HTTP/2 401   <- 로그인 전에는 반드시 401 이어야 합니다
```

브라우저로 열면 로그인 화면이 먼저 나옵니다. 로그인 후 사이드바에 tailnet 기기가
`ts-control` 자신을 포함해 나오면 성공입니다.

---

## 배포 전 점검

```cmd
cd webadmin
python -m tests.selftest
pip install -r tests/requirements-dev.txt
python -m tests.authtest
python -m tests.accesstest
```

`authtest` 는 **실제로 서버를 띄우고 WebSocket 에 붙어 봅니다.** 로그인 없이 터미널이
열리면 여기서 잡힙니다. `accesstest` 는 같은 방식으로 역할별 경계를 확인합니다.
배포할 때마다 둘 다 돌리십시오.

여러 사람이 쓴다면 공유 비밀번호 대신 **tailnet 신원 + 역할 표**를 쓰십시오
([ACCESS.md](ACCESS.md)). 사람별로 구분되고, 회수는 Tailscale admin 콘솔에서
기기를 지우는 것으로 끝나며, 감사 로그가 남습니다.

배포 후 밖에서 확인할 것, 제한된 키·호스트 키·감사 로그 점검까지 포함한 전체 목록은
[VERIFICATION.md 의 검증 절차](../docs/VERIFICATION.md#검증-절차--고친-뒤-무엇을-돌릴-것인가)
에 한곳으로 모아 뒀습니다.

---

## 운영 중 알아 둘 것

**Free/Starter 플랜은 유휴 시 컨테이너를 재웁니다.** 깨어나는 데 수십 초가 걸리고,
그동안 tailnet 노드도 내려가 있습니다. 상시 감시용으로는 맞지 않습니다.

**디스크가 없으면 tailscaled 상태가 매번 날아갑니다.** `render.yaml` 이 1GB 디스크를
`/var/lib/tailscale` 에 붙이는 이유입니다. 없으면 배포마다 admin 콘솔에 죽은 노드가
하나씩 쌓입니다.

**연결 설정 탭의 저장 버튼은 여기서 동작하지 않습니다.** 설정이 환경변수에서 왔으면
파일로 저장해도 다음 재시작에 덮어써지므로, 저장 대신 그 사실을 알려 줍니다.
Render 에서 호스트를 바꾸려면 `TSCONSOLE_HOSTS_JSON` 을 고치고 재배포하십시오.

**의심되면 키부터 끊으십시오.** 콘솔 비밀번호를 바꾸는 것보다
<https://login.tailscale.com/admin/machines> 에서 `ts-control` 노드를 지우는 쪽이
확실합니다. tailnet 에서 빠지면 아무 데도 닿지 못합니다.
