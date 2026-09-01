@echo off
setlocal
cd /d "%~dp0.."

echo ==^> Backend: creating venv + installing Python deps
py -3 -m venv backend\.venv
if errorlevel 1 goto :error
call backend\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
if errorlevel 1 goto :error
call deactivate

echo ==^> QIX sidecar: installing Node deps
pushd qix-node
call npm install
if errorlevel 1 (popd ^& goto :error)
popd

echo ==^> UI: installing Node deps
pushd ui
call npm install
if errorlevel 1 (popd ^& goto :error)
popd

echo.
echo ==^> Done. Run start.cmd to launch.
exit /b 0

:error
echo *** Setup failed.
exit /b 1
