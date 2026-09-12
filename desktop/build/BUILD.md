# TsBackup 빌드

두 단계입니다: PyInstaller 로 `TsBackup.exe` 를 만들고, Inno Setup 으로 그걸
`TsBackup-Setup.exe` 설치 파일로 감쌉니다(`build/tsbackup.iss`). 릴리스에
올라가는 건 후자뿐입니다 — 둘 다 **Windows 에서** 만들어야 합니다. PyInstaller
산출물은 OS 별이라 Linux 에서 돌리면 Linux ELF 가 나오고, Inno Setup 은 애초에
Windows 전용 도구입니다.

두 가지 길이 있습니다.

- **자동(권장)** — `vX.Y.Z` 태그를 푸시하면 GitHub Actions 의 Windows 러너가
  둘 다 빌드해 같은 태그의 릴리스에 `TsBackup-Setup.exe` 를 첨부합니다. 아래
  "릴리스" 참고.
- **로컬** — 손에 Windows 가 있을 때. 아래 "로컬 빌드".

---

## 로컬 빌드 (Windows)

Python 3.11 이상. `cmd` 또는 PowerShell 에서:

```cmd
cd desktop
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pyinstaller build\tsbackup.spec
```

결과물은 `desktop\dist\TsBackup.exe` (단일 파일). 스펙은 `desktop\` 에서 실행하는
것을 전제로 `pathex` 를 `os.getcwd()` 로 잡습니다 — 반드시 `desktop\` 안에서
`pyinstaller build\tsbackup.spec` 를 부르십시오.

빌드가 끝나면 그 자리에서 확인:

```cmd
dist\TsBackup.exe --config     :: 설정 파일 경로가 찍히면 임포트·번들 정상
dist\TsBackup.exe --check-gui  :: GUI 쪽 임포트만 검사 (창은 안 뜸) - CI 도 이걸로 확인
dist\TsBackup.exe              :: GUI 가 뜨는지
```

---

## 설치 파일(인스톨러) 빌드

[Inno Setup](https://jrsoftware.org/isinfo.php) 이 로컬에 필요합니다(GitHub
Actions 러너에는 이미 깔려 있지만, 로컬 PC 에는 따로 설치해야 합니다). 설치
후 `iscc` 가 PATH 에 없으면 전체 경로로 부르십시오
(보통 `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`).

위의 PyInstaller 빌드로 `dist\TsBackup.exe` 를 먼저 만든 다음:

```cmd
iscc /DMyAppVersion=0.1.1 build\tsbackup.iss
```

결과물은 `desktop\dist\TsBackup-Setup.exe`. `MyAppVersion` 을 안 주면
`tsbackup.iss` 의 기본값 `0.0.0` 이 들어갑니다 — 로컬 확인용으로는 무해하지만
실제 릴리스에 쓸 거라면 버전을 맞춰 주십시오.

빌드가 끝나면 CI 와 같은 방식으로 설치까지 확인:

```cmd
dist\TsBackup-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER
%LOCALAPPDATA%\Programs\TsBackup\TsBackup.exe --config
%LOCALAPPDATA%\Programs\TsBackup\TsBackup.exe --check-gui
```

(`/CURRENTUSER` 는 관리자 권한 없이 도는 조용한 설치입니다 — 대화형으로
"나만 설치할지 / 모든 사용자로 설치할지" 화면을 직접 보려면 그냥
`dist\TsBackup-Setup.exe` 를 인자 없이 실행하십시오.)

`tsbackup.iss` 의 `AppId` 는 한 번 정해서 고정한 GUID입니다 — 절대 바꾸지
마십시오. 바꾸면 다음 버전이 Windows 입장에서 "다른 앱"이 되어 제자리 업그레이드
대신 중복 설치·중복 제거 항목이 생깁니다.

---

## 자주 나는 실패

- **`ModuleNotFoundError: No module named '_lzma'`(또는 brotli/zstandard)** —
  py7zr 의 코덱 백엔드를 PyInstaller 훅이 놓친 경우. `build\tsbackup.spec` 의
  `hiddenimports` 에 그 이름을 추가하고 다시 빌드하십시오. `_lzma`, `bz2`,
  `zlib` 은 이미 넣어 두었습니다.
- **`Failed to execute script` / 검은 창이 잠깐 떴다 사라짐** — 스펙은
  `console=False` 라 창이 없어야 정상입니다. 오류를 보려면 스펙에서 임시로
  `console=True` 로 바꿔 콘솔에 트레이스백을 띄운 뒤, 고치고 되돌리십시오.
- **UPX 관련 경고/실패** — 스펙은 `upx=True` 입니다. 러너/PC 에 UPX 가 없으면
  PyInstaller 가 압축을 건너뛰고 경고만 남깁니다(빌드는 됨). 문제가 되면
  스펙에서 `upx=False`.
- **아이콘 파일을 못 찾음** — 없습니다. 아이콘은 `app/icons.py` 가 코드로
  그리므로 번들할 `.ico/.png` 가 없고, `--onefile` 임시 폴더 경로 문제도
  생기지 않습니다.
- **바이너리가 큼(수십 MB)** — 정상입니다. PySide6(Qt) 를 통째로 담습니다.
  스펙의 `excludes` 가 안 쓰는 Qt 모듈을 덜어냅니다. 무언가 임포트에 실패하면
  해당 항목을 `excludes` 에서 빼십시오.
- **`'iscc' 은(는) 내부 또는 외부 명령이 아닙니다`** — Inno Setup 은 설치해도
  기본적으로 PATH 에 안 잡힙니다. 전체 경로로 부르십시오:
  `"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\tsbackup.iss`.
- **설치 후 스모크 테스트에서 `installed exe not found`** — `/CURRENTUSER` 조용
  설치가 실패했거나 경로가 다른 경우입니다. 인자 없이 `dist\TsBackup-Setup.exe`
  를 대화형으로 띄워 실제로 어디에 설치되는지 먼저 확인하십시오.

---

## 릴리스 (자동)

`.github/workflows/desktop-release.yml` 이 태그 푸시에 반응합니다.

```cmd
git tag v0.1.0
git push origin v0.1.0
```

러너가 하는 일:

1. `windows-latest` 체크아웃, Python 설치
2. `desktop/requirements.txt` 설치, 순수 코어 셀프테스트
3. `desktop/` 에서 `pyinstaller build/tsbackup.spec` → `TsBackup.exe`
4. `TsBackup.exe --config`/`--check-gui` 로 그 바이너리 자체를 확인
5. 태그에서 버전 뽑기(없으면 `0.0.0`) → `iscc /DMyAppVersion=... build\tsbackup.iss`
   → `TsBackup-Setup.exe`
6. 그 설치 파일을 조용히 설치(`/CURRENTUSER`)한 뒤 **설치된** 경로에서
   다시 `--config`/`--check-gui` — 설치 파일이 컴파일만 된 게 아니라 실제로
   설치되고 도는지까지 확인
7. `desktop/dist/TsBackup-Setup.exe` 를 그 태그의 GitHub 릴리스에 업로드

태그 이름이 곧 버전입니다. `desktop/tsbackup/__init__.py` 의 `__version__` 과
맞춰 두면 릴리스 노트에서 헷갈리지 않습니다.

릴리스를 지우고 같은 태그를 다시 밀면 워크플로가 다시 돌아 자산을 덮어씁니다.
