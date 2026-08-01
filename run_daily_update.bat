@echo off
chcp 65001 >nul
cd /d C:\bizinfo-agent

echo ==================================== >> logs\update.log
echo Auto update started >> logs\update.log
echo ==================================== >> logs\update.log

echo [1/2] Collecting announcements (collector.py) >> logs\update.log
py collector.py >> logs\update.log 2>&1

echo [2/2] Parsing new announcements (parser.py) >> logs\update.log
py parser.py >> logs\update.log 2>&1

echo Auto update finished >> logs\update.log
echo. >> logs\update.log
