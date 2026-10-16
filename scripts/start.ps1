# Starts the demo shop (port 8100) and the dashboard/API (port 8000) in two windows.
# Usage (from the repo root, with the venv active):  .\scripts\start.ps1
$py = (Get-Command python).Source
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$py' -m demo_shop.app"
Start-Sleep 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$py' -m uvicorn app.main:app --port 8000"
Start-Sleep 4
Start-Process "http://127.0.0.1:8000"
