@echo off
rem Run vidroute from this skill without needing python on PATH (cmd.exe and PowerShell):
rem   "<skill folder>\scripts\vidroute.cmd" <command> [options] --json
rem Tries the Python recorded by `vidroute setup`, then py -3 / python (3.10+; the Microsoft Store
rem placeholder fails the check), then uv (also where its installers put it before PATH is refreshed)
rem and runs vidroute.py next to this file. Without any of them it prints a JSON envelope with error
rem needs_install (exit code 2).
setlocal EnableExtensions DisableDelayedExpansion
set "VR_HERE=%~dp0"
set "VR_SCRIPT=%~dp0vidroute.py"
set "VR_CHECK=import sys; sys.exit(sys.version_info < (3, 10))"
set "VR_PY="
if exist "%VR_HERE%..\.vidroute-skill.cfg" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%VR_HERE%..\.vidroute-skill.cfg") do (
    if "%%A"=="python" set "VR_PY=%%B"
  )
)
if defined VR_PY if exist "%VR_PY%" goto run_recorded
where py >nul 2>nul && py -3 -c "%VR_CHECK%" >nul 2>nul && goto run_py
where python >nul 2>nul && python -c "%VR_CHECK%" >nul 2>nul && goto run_python
if not defined VIDROUTE_HOME set "VIDROUTE_HOME=%USERPROFILE%\.vidroute"
set "VR_UV="
for /f "delims=" %%U in ('where uv 2^>nul') do if not defined VR_UV set "VR_UV=%%U"
if not defined VR_UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "VR_UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined VR_UV if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "VR_UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if not defined VR_UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "VR_UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined VR_UV if exist "%VIDROUTE_HOME%\runtimes\uv\bin\uv.exe" set "VR_UV=%VIDROUTE_HOME%\runtimes\uv\bin\uv.exe"
if defined VR_UV goto run_uv
where winget >nul 2>nul && goto need_uv_winget
echo {"ok": false, "schema": "vidroute/1", "data": {}, "warnings": [], "error": {"code": "needs_install", "message": "vidroute needs Python 3.10+ or uv, and neither was found; install uv, then run this command again (no restart needed).", "hint": "powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 ^| iex\""}}
exit /b 2

:need_uv_winget
echo {"ok": false, "schema": "vidroute/1", "data": {}, "warnings": [], "error": {"code": "needs_install", "message": "vidroute needs Python 3.10+ or uv, and neither was found; install uv, then run this command again (no restart needed).", "hint": "winget install -e --id astral-sh.uv"}}
exit /b 2

:run_recorded
"%VR_PY%" "%VR_SCRIPT%" %*
exit /b

:run_py
py -3 "%VR_SCRIPT%" %*
exit /b

:run_python
python "%VR_SCRIPT%" %*
exit /b

:run_uv
rem uv downloads Python 3.12 into vidroute's own runtimes folder, where the model workers find it too.
set "UV_PYTHON_INSTALL_DIR=%VIDROUTE_HOME%\runtimes\python"
set "UV_PYTHON_INSTALL_REGISTRY=0"
"%VR_UV%" run --no-project --python 3.12 "%VR_SCRIPT%" %*
exit /b
