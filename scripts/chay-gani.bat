@echo off
rem GANI Video - mo 1 cham: bat may chu (serve) va daemon neu chua chay, roi mo giao dien
rem trong cua so rieng (khong thanh dia chi). Dong hai cua so nho "GANI - ..." tren thanh
rem tac vu de tat het. Cau hinh (khoa API, thu muc footage...) nam trong file .env cua du an.
rem Dat GANI_NO_BROWSER=1 de chi bat may chu, khong mo cua so (dung khi thu).
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONPATH=src

if not exist "frontend\dist\index.html" (
  echo Chua co giao dien da build. Chay mot lan:  cd frontend ^&^& npm ci ^&^& npm run build:real
  pause
  exit /b 1
)

rem May chu giao dien + API: bo qua neu cong 8765 da co nguoi lang nghe.
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul
if errorlevel 1 (
  start "GANI - may chu (serve)" /min cmd /k python -m gani_dung_video serve
)

rem Doi may chu tra loi (toi da khoang 30 giay).
set /a tries=0
:wait
curl -s -m 2 http://127.0.0.1:8765/api/health >nul 2>&1
if not errorlevel 1 goto up
set /a tries+=1
if %tries% geq 30 goto failed
ping -n 2 127.0.0.1 >nul
goto wait

:failed
echo May chu khong tra loi sau 30 giay. Xem cua so "GANI - may chu (serve)" de biet loi.
pause
exit /b 1

:up
rem Daemon: chi bat neu may chu bao chua co daemon nao.
curl -s -m 3 http://127.0.0.1:8765/api/health | findstr /R /C:"daemon_online.: true" >nul
if errorlevel 1 (
  start "GANI - daemon (lam viec)" /min cmd /k python -m gani_dung_video daemon
)

if defined GANI_NO_BROWSER exit /b 0

rem --start-maximized: cua so ung dung mac dinh chi rong ~1050 px va bi cat khung kiem tra ben phai.
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%EDGE%" (
  start "" "%EDGE%" --app=http://127.0.0.1:8765/ --start-maximized
) else if exist "%CHROME%" (
  start "" "%CHROME%" --app=http://127.0.0.1:8765/ --start-maximized
) else (
  start "" http://127.0.0.1:8765/
  exit /b 0
)

rem Edge hay bo qua --start-maximized khi da co phien chay san: phong to lai bang lenh he thong.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0phong-to-cua-so.ps1"
exit /b 0
