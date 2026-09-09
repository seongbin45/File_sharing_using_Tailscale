# 제한된 키 — 콘솔이 셸을 못 열게 만들기

관리 콘솔의 `admin` 은 기본적으로 **양쪽 PC 에서 임의 코드 실행**입니다. 콘솔 서버가
털리면 두 대가 같이 털립니다.

이 문서의 설정을 끝내면 그 관계가 끊어집니다.

> 콘솔이 평소에 쓰는 키로는 **셸을 열 수 없습니다.**
> 상태 조회·로그·즉시 실행만 됩니다. 그게 전부입니다.

통제권이 콘솔이 아니라 **대상 기기 쪽에** 박히기 때문에, **콘솔을 신뢰하지 않아도**
됩니다. 이 프로젝트에서 접근 통제로 할 수 있는 일 중 값어치가 가장 큰 한 수입니다.

---

## `command=` 만으로는 아무 의미가 없습니다

SSH 의 강제 명령은 이렇게 씁니다.

```
command="...",no-pty ssh-ed25519 AAAA... 
```

클라이언트가 무엇을 요청하든 sshd 는 `command=` 를 대신 실행하고, 원래 요청은
`SSH_ORIGINAL_COMMAND` 에 넣어 줍니다.

**그런데 콘솔은 원래 임의 PowerShell 을 base64 로 보내고 있었습니다.** 강제 명령이
"받은 base64 를 실행"이면 제한이 장식입니다. 그래서 프로토콜을 바꿨습니다.

| | 전 | 후 |
|---|---|---|
| 콘솔이 보내는 것 | 임의 PowerShell (base64) | **동사 하나** |
| 코드가 있는 곳 | 콘솔 | **대상 기기 (`ts_guard.ps1`)** |
| 인자 | 자유 문자열 | **코드를 실을 수 있는 인자가 없음** |

허용되는 전부:

```
whoami
status
log <1-500>
task run | enable | disable
```

`log` 는 경로가 아니라 **범위 제한된 정수**를 받습니다. `task` 는 **작업 이름을 받지
않습니다** — 이름은 `ts_guard.ps1` 안에 있습니다. 이름을 지정할 수 있으면 그 기기의
아무 예약 작업이나 실행시킬 수 있으니까요.

---

## 설치

양쪽 PC 에서 각각 합니다. **비관리자 SSH 세션으로 가능합니다.**

### 1. 가드 배치

```cmd
git -C C:\Scripts\src pull
copy /y C:\Scripts\src\scripts\ts_guard.ps1 C:\Scripts\
```

상단 `CONFIG` 블록의 작업 이름이 이 기기와 맞는지 확인하십시오.

```cmd
powershell -NoProfile -Command "Select-String -Path C:\Scripts\ts_guard.ps1 -Pattern '^\$Guard'"
```

### 2. 가드가 실제로 동작하는지 먼저 확인

키를 묶기 전에 확인합니다. **묶고 나면 고치기 어렵습니다.**

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ts_guard.ps1 -Command status
```

base64 한 덩어리가 나와야 합니다. 사람이 읽으려면:

```cmd
powershell -NoProfile -Command "$b=(& powershell -NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ts_guard.ps1 -Command status); [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b))"
```

거부도 확인하십시오. `denied: true` 가 나와야 합니다.

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ts_guard.ps1 -Command "status; calc"
```

### 3. 콘솔 쪽에서 키 만들기

콘솔이 도는 기기에서:

```cmd
ssh-keygen -t ed25519 -f C:\Scripts\keys\ts_console_restricted -C "ts-console restricted" -N ""
type C:\Scripts\keys\ts_console_restricted.pub
```

`-N ""` 는 암호 없음입니다. 무인 실행이라 어차피 어딘가에 암호를 둬야 하고, 그럴 바엔
**키 파일 자체의 권한**으로 지키는 편이 낫습니다. 암호를 걸겠다면
`TSCONSOLE_KEYPASS_<ID>` 에 넣으십시오.

### 4. 대상 기기의 authorized_keys 에 등록

`%USERPROFILE%\.ssh\authorized_keys` 에 **한 줄로** 넣습니다. 3단계에서 출력된 공개키
전체를 `ssh-ed25519 AAAA...` 자리에 붙이십시오.

```
command="powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\Scripts\ts_guard.ps1",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc ssh-ed25519 AAAA...여기에공개키... ts-console restricted
```

각 옵션이 하는 일:

| 옵션 | 막는 것 |
|---|---|
| `command="..."` | 요청한 명령 대신 가드를 실행 |
| `no-pty` | 대화형 터미널 |
| `no-port-forwarding` | 이 기기를 발판 삼아 내부망으로 들어가는 것 |
| `no-agent-forwarding` | 콘솔의 다른 키를 빌려 쓰는 것 |
| `no-X11-forwarding`, `no-user-rc` | 나머지 우회 경로 |

**`no-port-forwarding` 을 빠뜨리지 마십시오.** 셸을 막아도 포트 포워딩이 열려 있으면
그 기기를 통해 내부망 어디로든 터널을 팔 수 있습니다.

SSH 로 파일을 고치는 방법:

```cmd
powershell -NoProfile -Command "$k = Get-Content C:\temp\key.pub -Raw; $line = 'command=\"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\Scripts\ts_guard.ps1\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc ' + $k.Trim(); Add-Content -Path $env:USERPROFILE\.ssh\authorized_keys -Value $line -Encoding ASCII"
```

### 5. 밖에서 확인 — 여기가 진짜 검증입니다

콘솔 기기에서:

```bash
# 허용된 것: base64 가 돌아온다
ssh -i C:\Scripts\keys\ts_console_restricted dicia@desktop-nb8bfur status

# 셸: 열리면 안 된다
ssh -i C:\Scripts\keys\ts_console_restricted dicia@desktop-nb8bfur
# -> denied. "이 키로는 셸을 열 수 없습니다"

# 다른 명령: 무시되고 가드가 거부한다
ssh -i C:\Scripts\keys\ts_console_restricted dicia@desktop-nb8bfur "whoami && calc"
# -> denied

# 포트 포워딩: 거부되어야 한다
ssh -i C:\Scripts\keys\ts_console_restricted -L 9999:127.0.0.1:3389 dicia@desktop-nb8bfur status
# -> "port forwarding is disabled"
```

**두 번째와 네 번째가 통과해 버리면 설정이 안 걸린 것입니다.** 그 상태로 넘어가지
마십시오.

### 6. 콘솔에 알려 주기

`hosts.json`:

```json
{
  "id": "sender",
  "address": "desktop-nb8bfur",
  "username": "dicia",
  "key_file": "C:\\Scripts\\keys\\ts_console_restricted",
  "host_key": "SHA256:...",
  "restricted": true
}
```

또는 환경변수로:

```
TSCONSOLE_KEYFILE_SENDER=C:\Scripts\keys\ts_console_restricted
TSCONSOLE_HOSTKEY_SENDER=SHA256:...
```

`restricted: true` 는 **실제 상태와 맞춰야 합니다.** 걸지도 않고 참으로 두면 얻는 게
없고, 걸어 놓고 거짓으로 두면 모든 요청이 실패합니다.

이제 콘솔의 터미널 탭은 이 호스트에 대해 **셸을 열 수 없다고 표시하고 시도하지
않습니다.**

---

## 터미널이 정말 필요할 때

두 번째 키를 **제한 없이** 등록하되, 필요할 때만 두십시오.

```cmd
:: 필요할 때 추가
powershell -NoProfile -Command "Add-Content $env:USERPROFILE\.ssh\authorized_keys (Get-Content C:\temp\admin_key.pub -Raw).Trim()"

:: 끝나면 제거
powershell -NoProfile -Command "$p=\"$env:USERPROFILE\.ssh\authorized_keys\"; (Get-Content $p) | Where-Object { $_ -notmatch 'ts-console admin' } | Set-Content $p -Encoding ASCII"
```

**이것이 좋은 이유:** 셸 권한이 파일 한 줄이 되고, 그 줄은 **대상 기기에** 있습니다.
콘솔이 아니라 기기가 결정합니다. 회수도 그 줄을 지우는 것이라 아무에게도 요청할
필요가 없습니다.

`ts_console.ps1` 은 그대로 두십시오. 웹 콘솔에 셸이 없어도 SSH 로 직접 들어가는 길이
남아 있어야 합니다.

---

## 남는 위험

정직하게: 이걸 해도 **다 막히는 것은 아닙니다.**

| 여전히 가능 | 왜 |
|---|---|
| `task run` 남발 | 백업을 반복 실행시켜 대역폭·디스크를 소모 |
| 상태·로그 열람 | 경로와 프로젝트 개수 등이 드러남 |
| 무제한 키가 남아 있으면 전부 | 5단계를 반드시 확인하십시오 |

`task run` 은 원래 하루 한 번 도는 작업이고 `IgnoreNew` 가 중복을 막으므로 피해가
제한적입니다. 그래도 **"셸이 열린다"와는 완전히 다른 손실**입니다.

---

## 점검

```cmd
cd webadmin
python -m tests.selftest
```

동사 문법(주입 시도 19가지 거부), 제한된 호스트에 PowerShell 이 전송되지 않는지,
`shell()` 이 거부되는지, 작업 이름이 전송되지 않는지를 확인합니다.
