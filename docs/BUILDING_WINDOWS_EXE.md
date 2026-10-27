# Building a Windows Executable

This produces `dist\BYTT_Sonar_Viewer\BYTT_Sonar_Viewer.exe` — a standalone
Windows application that requires no Python installation on the target machine.

---

## Prerequisites (build machine only)

- **Windows 10 or 11**, 64-bit
- **Python 3.11 or 3.12** installed from [python.org](https://python.org)
  - During install tick **"Add Python to PATH"**
  - Python 3.13+ *may* work but is untested with all dependencies

---

## Quickstart (recommended)

1. Open **Command Prompt** (not PowerShell) in the project root folder.
2. Run:
   ```bat
   build_windows.bat
   ```
3. Wait — first run downloads ~400 MB of dependencies. Subsequent runs are fast.
4. When done, the finished folder is at:
   ```
   dist\BYTT_Sonar_Viewer\
   ```

---

## Manual steps (if you prefer)

```bat
:: 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 2. Install dependencies
pip install "PySide6>=6.6" "pyqtgraph>=0.13" "numpy>=1.26" "opencv-python>=4.9"
pip install pyinstaller
pip install -e .

:: 3. Build
pyinstaller bytt.spec --noconfirm
```

---

## Distributing to a Windows user

Zip the entire `dist\BYTT_Sonar_Viewer\` folder and send it.  
The user unzips it and double-clicks `BYTT_Sonar_Viewer.exe`. Nothing else needed.

> **Do not move the `.exe` out of the folder** — it depends on the DLLs and
> plugin folders alongside it. The whole folder must stay together.

---

## One-file vs one-folder

`bytt.spec` defaults to **one-folder** mode (the `dist\BYTT_Sonar_Viewer\` folder).

To get a **single `.exe`** instead (easier to share but ~3 s slower to start
because it unpacks to `%TEMP%` each time), edit `bytt.spec` and swap the
`EXE + COLLECT` block for the commented-out one-file `EXE` block at the bottom.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Reinstall Python and tick "Add to PATH", or use the full path `C:\Python312\python.exe` |
| `PySide6` import error at runtime | Delete `dist\` and `build\`, then rebuild — stale cached builds sometimes miss Qt plugins |
| Blank/black window on launch | Run from Command Prompt (`BYTT_Sonar_Viewer.exe` in the dist folder) to see the error output, then report it |
| Antivirus flags the exe | PyInstaller-built executables are commonly flagged as false positives. Add an exclusion or use a code-signing certificate |
| Missing `bsf_viewer_config.json` | The file is bundled automatically by `bytt.spec`. If missing after build, check the `datas=` section in `bytt.spec` |
