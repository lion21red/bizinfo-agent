@echo off
chcp 65001 >nul
cd /d C:\bizinfo-agent

echo 지원사업 매칭 도우미를 시작합니다...
py -3 -m streamlit run app.py

pause
