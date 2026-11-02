@echo off
rem Runs the game catalog bot, writing its log to bot.log.
rem If the bot stops (crash, or no internet yet right after the PC starts), it is started again after 30 seconds.
rem Started hidden at login by "Game catalog bot.vbs" in the Windows Startup folder.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
:loop
echo [%date% %time%] Starting the bot >> bot.log
".venv\Scripts\python.exe" bot.py >> bot.log 2>&1
echo [%date% %time%] The bot stopped, starting it again in 30 seconds >> bot.log
rem (timeout doesn't work without a window, so wait by pinging this PC 31 times, about 30 seconds)
ping -n 31 127.0.0.1 > nul
goto loop
