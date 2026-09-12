# TsBackup.exe 빌드

`.exe` 는 **Windows 에서** 만들어야 합니다. PyInstaller 산출물은 OS 별이라
Linux 에서 돌리면 Linux ELF 가 나옵니다 — 릴리스에 필요한 `.exe` 가 아닙니다.

두 가지 길이 있습니다.

- **자동(권장)** — `vX.Y.Z` 태그를 푸시하면 GitHub Actions 의 Windows 러너가
  빌드해 같은 태그의 릴리스에 `TsBackup.exe` 를 첨부합니다. 아래 "릴리스" 참고.
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

---

## 릴리스 (자동)

`.github/workflows/desktop-release.yml` 이 태그 푸시에 반응합니다.

```cmd
git tag v0.1.0
git push origin v0.1.0
```

러너가 하는 일:

1. `windows-latest` 체크아웃, Python 설치
2. `desktop/requirements.txt` 설치
3. `desktop/` 에서 `pyinstaller build/tsbackup.spec`
4. `desktop/dist/TsBackup.exe` 를 그 태그의 GitHub 릴리스에 업로드

태그 이름이 곧 버전입니다. `desktop/tsbackup/__init__.py` 의 `__version__` 과
맞춰 두면 릴리스 노트에서 헷갈리지 않습니다.

릴리스를 지우고 같은 태그를 다시 밀면 워크플로가 다시 돌아 자산을 덮어씁니다.
