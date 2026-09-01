@echo off
setlocal
cd /d "%~dp0"

echo ==^> Starting QIX sidecar on :8788 (new window)
start "QlikGen QIX" cmd /k "cd /d %~dp0qix-node && node index.js"

echo ==^> Starting backend on :8787 (new window)
start "QlikGen Backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8787 --reload"

echo ==^> Starting UI on :5173 (new window)
start "QlikGen UI" cmd /k "cd /d %~dp0ui && npm run dev"

echo ==^> Waiting 4s for services to boot, then opening browser...
timeout /t 4 /nobreak >nul
start "" http://localhost:5173

echo.
echo QlikGen is running in three windows. Close each window to stop that service.
