# 터미널만으로 필수 도구 설치하기

> 시스템 전체는 [README](../README.md) 를 보십시오.

이 백업은 두 대의 PC 에서 각각 도구를 필요로 합니다. 화면 없이 SSH 터미널만으로 설치하는
절차입니다.

| 도구 | 보내는 PC | 받는 PC | 쓰임 |
|---|---|---|---|
| **7-Zip** | 필수 | 필수 | 압축 / 압축 해제 |
| **git** | 선택 | **필수** | 저장소에서 스크립트를 받아옴 / 받는 쪽 관리 저장소 |
| **Python 3** | 선택 | 선택 | `webadmin/` 콘솔을 돌리거나 `desktop/` 앱을 소스에서 직접 실행·개발할 때만 |

`tar` 와 `wscript`, PowerShell 은 Windows 에 기본 포함이라 설치할 것이 없습니다.

**Python 이 필요 없는 경우:** `scripts/` 의 백업 배치/PowerShell 파이프라인(이 문서의 원래
대상)은 Python 을 전혀 쓰지 않습니다 — [README](../README.md#왜-이런-구조인가) 참고. 빌드된
`Naru-Setup.exe` 를 그냥 설치해 쓰는 것도 마찬가지입니다 — PyInstaller 로 파이썬 런타임이
이미 exe 안에 들어 있습니다. Python 은 오직 `webadmin/`(관리 콘솔) 을 돌리거나 `desktop/` 앱을
소스에서 고쳐가며 실행할 때만 필요합니다.

---

## 먼저 — 이미 있는지 확인

설치 전에 확인하십시오. 이미 있는 경우가 많습니다.

```cmd
dir "C:\Program Files\7-Zip\7z.exe"
git --version
python --version
```

`7z.exe` 가 나오면 7-Zip 은 끝났습니다. 스크립트는 **PATH 가 아니라 이 전체 경로로** 직접
호출하므로 PATH 등록은 필요 없습니다.

**`python --version` 이 버전 대신 Microsoft Store 를 열거나 아무것도 안 하면** Python 이
없는 것이 맞습니다 — Windows 10/11 은 설치 안 된 `python.exe` 이름을 스토어로 연결하는
"앱 실행 별칭"을 기본으로 깔아 두기 때문에, 이 동작 자체가 "없다"는 신호입니다.

---

## 방법 A: winget (가장 간단, 권장)

Windows 10 1809 이상 / Windows 11 에는 `winget` 이 기본 포함돼 있습니다.

```cmd
winget --version
```

버전이 나오면 이것으로 끝입니다.

```cmd
winget install --id 7zip.7zip -e --accept-source-agreements --accept-package-agreements
winget install --id Git.Git  -e --accept-source-agreements --accept-package-agreements
winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
```

Python 이 필요할 때만 세 번째 줄도 실행하십시오. `Python.Python.3.12` 의 끝자리
버전(`3.12`)은 계속 바뀝니다 — 먼저 `winget search Python.Python.3` 로 현재 나오는 정확한
id 를 확인한 뒤 그 id 를 쓰는 편이 안전합니다.

`-e` 는 이름이 정확히 일치하는 패키지만 설치한다는 뜻입니다. 없으면 비슷한 이름의 다른
패키지가 걸릴 수 있습니다.

**관리자 권한이 필요할 수 있습니다.** 세 패키지 모두 기본적으로 시스템 범위로 설치되므로,
비관리자 SSH 세션에서는 UAC 승격에 실패할 수 있습니다. 그 경우 방법 B 로 가십시오. winget 의
Python 패키지는 `PrependPath` 를 기본으로 켜 두므로 별도 설정 없이도 PATH 에 등록됩니다.

설치 후 새 세션에서 확인합니다(현재 세션의 PATH 에는 반영되지 않습니다).

```cmd
dir "C:\Program Files\7-Zip\7z.exe"
git --version
python --version
python -m pip --version
```

---

## 방법 B: 직접 내려받아 무인 설치

winget 이 없거나 실패할 때입니다.

### B-1. 셸을 먼저 확인하십시오

**이 구간에서 가장 흔한 실패 원인입니다.** cmd 와 PowerShell 은 쓸 수 있는 명령이 다릅니다.

| 하려는 것 | cmd | PowerShell |
|---|---|---|
| 파일 다운로드 | `curl -L -o <파일> <URL>` | `curl.exe -L -o <파일> <URL>` 또는 `Invoke-WebRequest` |
| 명령 위치 찾기 | `where <이름>` | `where.exe <이름>` 또는 `Get-Command` |
| 파일 복사(덮어쓰기) | `copy /y <원본> <대상>` | `Copy-Item <원본> <대상> -Force` |

cmd 명령의 `/y`, `/s` 같은 스위치는 PowerShell 별칭에 통하지 않습니다. `copy`, `where`,
`curl`, `ls`, `cat`, `rm` 은 전부 PowerShell 에서 다른 명령의 별칭입니다.

- `Invoke-WebRequest` 는 **PowerShell 전용**입니다. cmd 에서 실행하면
  `'Invoke-WebRequest'은(는) 내부 또는 외부 명령... 아닙니다` 가 납니다.
- PowerShell 에서 `curl` 은 **`Invoke-WebRequest` 의 별칭**입니다. `-L -o` 같은 옵션이
  통하지 않으므로 `curl.exe` 라고 확장자까지 적어야 진짜 curl 이 실행됩니다.
- 같은 이유로 PowerShell 의 `where` 는 `Where-Object` 입니다. `where 7z` 가 아무것도
  출력하지 않는 것은 7-Zip 이 없어서가 아닙니다.

지금 어느 셸인지 확실하지 않으면:

```
echo %COMSPEC%
```

cmd 면 경로가 나오고, PowerShell 이면 `%COMSPEC%` 가 그대로 출력됩니다.

### B-2. 실제 설치 파일 주소를 찾아냅니다

**`https://7-zip.org` 이나 `https://www.7-zip.org` 은 다운로드 주소가 아닙니다.**
웹페이지 주소입니다. 그대로 내려받으면 7 KB 남짓한 HTML 이 `.exe` 라는 이름으로 저장되고,
실행하려는 순간 이런 오류가 납니다.

```
Start-Process : 파일 또는 디렉터리가 손상되었기 때문에 읽을 수 없습니다
```

파일이 손상된 것이 아니라 **애초에 프로그램이 아닌 것**을 받은 것입니다.

실제 파일은 `https://www.7-zip.org/a/7z<버전>-x64.exe` 형태이고 버전은 계속 바뀝니다.
버전을 문서에 박아두면 다음 릴리스에서 또 깨지므로, 현재 파일명을 받아옵니다.

**PowerShell:**

```powershell
$page = Invoke-WebRequest -Uri 'https://www.7-zip.org/download.html' -UseBasicParsing
$file = ($page.Links.href | Where-Object { $_ -match '^a/7z\d+-x64\.exe$' } | Select-Object -First 1)
$url  = "https://www.7-zip.org/$file"
$url
```

**cmd:**

```cmd
curl -s https://www.7-zip.org/download.html | findstr /i "x64.exe"
```

출력에서 `a/7z2500-x64.exe` 같은 항목을 골라 앞에 `https://www.7-zip.org/` 를 붙이면 됩니다.

`-UseBasicParsing` 은 PowerShell 5.1 에서 붙이십시오. 없으면 Internet Explorer 엔진을
쓰려다 초기화되지 않은 프로필에서 실패합니다.

### B-3. 내려받고, 받은 것이 맞는지 확인합니다

```powershell
$ProgressPreference = 'SilentlyContinue'   # 5.1 에서 다운로드가 훨씬 빨라집니다
Invoke-WebRequest -Uri $url -OutFile 'C:\Scripts\7z-installer.exe' -UseBasicParsing
```

```cmd
curl -L -o C:\Scripts\7z-installer.exe https://www.7-zip.org/a/7z<버전>-x64.exe
```

**받자마자 검증하십시오.** 이 한 단계가 앞의 "손상되었습니다" 오류를 미리 잡습니다.

```powershell
$f = Get-Item 'C:\Scripts\7z-installer.exe'
"{0:N0} bytes" -f $f.Length
$head = [System.IO.File]::ReadAllBytes($f.FullName)[0..1]
if ($head[0] -eq 0x4D -and $head[1] -eq 0x5A) { 'OK  : 실행 파일(MZ) 맞음' }
else { 'FAIL: 실행 파일이 아님 - URL 을 다시 확인하십시오' }
```

- 크기가 **1.5 MB 안팎**이어야 합니다. 7 KB 정도면 HTML 을 받은 것입니다
- 첫 두 바이트가 `MZ`(0x4D 0x5A) 여야 Windows 실행 파일입니다

잘못 받았다면 지우고 다시 하십시오. 남아 있으면 계속 같은 오류가 반복됩니다.

```powershell
Remove-Item 'C:\Scripts\7z-installer.exe' -Force
```

### B-4. 무인 설치

7-Zip 설치본은 NSIS 기반이라 `/S` 로 창 없이 설치됩니다.

```powershell
Start-Process -FilePath 'C:\Scripts\7z-installer.exe' -ArgumentList '/S' -Wait
```

**`C:\Program Files` 에 설치하려면 관리자 권한이 필요합니다.** 비관리자 SSH 세션에서는
UAC 창이 물리 화면에 뜨고 터미널에서는 응답할 수 없어 그대로 멈추거나 실패합니다.

권한이 없다면 쓰기 가능한 경로로 설치하십시오.

```powershell
Start-Process -FilePath 'C:\Scripts\7z-installer.exe' -ArgumentList '/S','/D=C:\Scripts\7-Zip' -Wait
```

`/D=` 는 **반드시 마지막 인자**여야 하고 **따옴표를 붙이면 안 됩니다.** NSIS 의 규칙입니다.
경로에 공백이 없는 곳을 고르십시오.

이 경우 스크립트 설정값을 함께 바꿔야 합니다(아래 참조).

### B-5. git (받는 PC 필수)

같은 방식입니다. Git for Windows 의 무인 설치 옵션은 Inno Setup 계열입니다.

```powershell
$api = Invoke-WebRequest -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -UseBasicParsing
$url = (($api.Content | ConvertFrom-Json).assets | Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' }).browser_download_url
$url
Invoke-WebRequest -Uri $url -OutFile 'C:\Scripts\git-installer.exe' -UseBasicParsing
Start-Process -FilePath 'C:\Scripts\git-installer.exe' -ArgumentList '/VERYSILENT','/NORESTART' -Wait
```

여기서도 다운로드 직후 크기와 `MZ` 헤더를 확인하십시오. git 설치본은 60 MB 안팎입니다.

git 은 설치 시 PATH 에 등록되므로, 설치 후 **새 세션**에서 `git --version` 이 나와야 합니다.
현재 세션에는 반영되지 않습니다.

### B-6. Python (webadmin / desktop 소스 실행 시에만)

python.org 의 공식 배포판입니다. 파일명의 버전 번호가 계속 바뀌므로 다운로드 페이지에서
현재 값을 받아옵니다 — 7-Zip 과 같은 방식입니다.

```powershell
$page = Invoke-WebRequest -Uri 'https://www.python.org/downloads/windows/' -UseBasicParsing
$file = ($page.Links.href | Where-Object { $_ -match 'python-3\.\d+\.\d+-amd64\.exe$' } | Select-Object -First 1)
$file
Invoke-WebRequest -Uri $file -OutFile 'C:\Scripts\python-installer.exe' -UseBasicParsing
```

받은 뒤 크기(25MB 안팎)와 `MZ` 헤더를 다른 도구와 같은 방식으로 확인하십시오.

```powershell
Start-Process -FilePath 'C:\Scripts\python-installer.exe' `
  -ArgumentList 'InstallAllUsers=0','PrependPath=1','Include_test=0','InstallLauncherAllUsers=0','/quiet' -Wait
```

`InstallAllUsers=0` 은 사용자 범위 설치라 관리자 권한이 없어도 됩니다(7-Zip/git 의 방법 B
와 같은 이유). `PrependPath=1` 을 빼먹으면 설치는 되지만 **새 세션에서도** `python`/`pip` 가
안 잡힙니다 — 이 설치본은 7-Zip 과 달리 PATH 등록이 선택 사항이라 꼭 켜야 합니다.

관리자 권한으로 전체 사용자에게 설치하려면 `InstallAllUsers=1` 로 바꾸고 관리자 세션에서
실행하십시오.

설치 후 **새 세션**에서 확인합니다.

```cmd
python --version
python -m pip --version
```

`python` 대신 Microsoft Store 가 열리면, 설치는 됐지만 Windows 의 "앱 실행 별칭"이 아직
`python.exe` 를 가로채고 있는 것입니다 — 설정 > 앱 > 고급 앱 설정 > 앱 실행 별칭에서
`python.exe`/`python3.exe` 항목을 끄면 해결됩니다. `PrependPath=1` 로 설치했다면 보통
겪지 않는 문제입니다.

---

## 설치 확인

```cmd
dir "C:\Program Files\7-Zip\7z.exe"
"C:\Program Files\7-Zip\7z.exe" i
git --version
python --version
```

두 번째 줄이 7-Zip 버전과 지원 포맷을 출력하면 정상입니다.

기기 이름까지 같이 찍어두면 어느 PC 를 확인한 것인지 헷갈리지 않습니다.
이 시스템은 PC 두 대를 오가며 작업하므로 실제로 헷갈립니다.

```powershell
"$env:COMPUTERNAME : 7z     = " + (Test-Path 'C:\Program Files\7-Zip\7z.exe')
"$env:COMPUTERNAME : git    = " + (Get-Command git -ErrorAction SilentlyContinue).Source
"$env:COMPUTERNAME : python = " + (Get-Command python -ErrorAction SilentlyContinue).Source
```

---

## 저장소 받기 + 파이썬 패키지 설치 (webadmin / desktop 소스 실행 시)

`scripts/` 배치 파이프라인이나 빌드된 `Naru-Setup.exe` 만 쓴다면 이 절은 건너뛰어도
됩니다. `webadmin/` 콘솔을 띄우거나 `desktop/` 앱을 소스에서 직접 실행·개발할 때만
필요합니다. git 과 Python 이 위에서 이미 확인됐다는 전제입니다.

```cmd
mkdir C:\Scripts 2>nul
git clone https://github.com/seongbin45/File_sharing_using_Tailscale.git C:\Scripts\src
```

두 컴포넌트 중 필요한 쪽만 골라 venv 를 만들고 그 안에 패키지를 설치합니다 — 시스템
전역 Python 에 바로 설치하지 않는 이유는 아래에 있습니다.

```cmd
:: webadmin 콘솔
python -m venv C:\Scripts\webadmin-venv
C:\Scripts\webadmin-venv\Scripts\python -m pip install -r C:\Scripts\src\webadmin\requirements.txt

:: 또는 desktop 앱을 소스에서
python -m venv C:\Scripts\src\desktop\.venv
C:\Scripts\src\desktop\.venv\Scripts\python -m pip install -r C:\Scripts\src\desktop\requirements.txt
```

**venv 를 쓰는 이유:** `pip install -r requirements.txt` 를 venv 없이 바로 돌리면
비관리자 세션에서는 전역 site-packages 에 쓰기 권한이 없어 실패하거나(관리자 세션이면
성공하지만 시스템 Python 환경을 오염시킴), 이미 다른 용도로 쓰던 전역 패키지 버전과
충돌할 수 있습니다. venv 는 이 프로젝트만의 격리된 패키지 집합을 만들어 두 문제를 모두
피합니다.

각 README 의 나머지 설치·실행 단계는 [webadmin/README.md](../webadmin/README.md),
[desktop/README.md](../desktop/README.md) 를 보십시오.

---

## 설치 경로를 바꿨다면

기본 위치(`C:\Program Files\7-Zip\7z.exe`)가 아닌 곳에 설치했다면 **양쪽 스크립트의
설정값을 고쳐야 합니다.** 스크립트는 PATH 를 쓰지 않고 이 값의 전체 경로로 직접 호출합니다.

| 파일 | 값 |
|---|---|
| `scripts/ts_backup.bat` | `set "SEVENZIP=C:\Program Files\7-Zip\7z.exe"` |
| `scripts/receiver/ts_receive.ps1` | `$SevenZip = 'C:\Program Files\7-Zip\7z.exe'` |

PATH 에 의존하지 않는 이유는 두 가지입니다.

1. **7-Zip 설치본은 자기를 PATH 에 등록하지 않습니다.** `where.exe 7z` 가 아무것도 못 찾는
   것이 정상입니다
2. **작업 스케줄러는 맨 환경에서 실행됩니다.** 어떤 셸에서 `7z` 가 잘 실행되더라도(conda
   환경 등) 스케줄러가 돌릴 때는 그 PATH 가 없습니다

---

## 함정 모음

| 증상 | 원인 |
|---|---|
| `'Invoke-WebRequest'은(는) 내부 또는 외부 명령... 아닙니다` | cmd 에서 PowerShell 명령을 실행함 |
| `curl -L -o` 가 PowerShell 에서 이상하게 동작 | `curl` 이 `Invoke-WebRequest` 별칭. `curl.exe` 로 |
| `where 7z` 가 아무것도 출력 안 함 | PowerShell 에서 `where` 는 `Where-Object`. `where.exe` 로. 그리고 7-Zip 은 어차피 PATH 에 없음 |
| `copy /y` 가 `'...' 인수를 허용하는 위치 매개 변수를 찾을 수 없습니다` | PowerShell 에서 `copy` 는 `Copy-Item`. `-Force` 를 쓰거나 cmd 에서 실행 |
| `git pull` 이 Already up to date 인데 파일이 없음 | clone 이 다른 브랜치에 있음. `git -C <경로> branch --show-current` 로 확인하고 `checkout main` |
| `파일 또는 디렉터리가 손상되었기 때문에 읽을 수 없습니다` | 웹페이지 HTML 을 `.exe` 로 저장함. URL 이 잘못됨 |
| 다시 받아도 같은 오류 반복 | 손상된 파일이 남아 있음. 먼저 지울 것 |
| 설치가 멈추거나 조용히 실패 | `Program Files` 쓰기에 관리자 권한 필요. UAC 가 물리 화면에 떠 있음 |
| 설치했는데 `git --version` 이 안 됨 | PATH 변경은 새 세션부터 적용됨 |
| `/D=` 를 따옴표로 감쌌더니 무시됨 | NSIS 규칙상 `/D=` 는 마지막 인자이고 따옴표 불가 |
| `python` 이 버전 대신 Microsoft Store 를 엶 | 설치 안 된 상태의 "앱 실행 별칭"이 아직 남음. `PrependPath=1` 로 재설치하거나 설정 > 앱 실행 별칭에서 끄기 |
| 설치했는데 새 세션에서도 `python`/`pip` 를 못 찾음 | Python 설치본은 `PrependPath=1` 을 직접 안 넣으면 PATH 등록을 안 함(7-Zip/git 과 다름) |
| `pip install -r requirements.txt` 가 권한 오류로 실패 | venv 없이 전역 Python 에 설치하려 함. `python -m venv` 로 격리된 환경을 먼저 만들 것 |
