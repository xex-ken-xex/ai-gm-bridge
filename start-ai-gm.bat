@echo off
chcp 65001 > nul
title AI GM Server
rem "FVTT" is the WSL distribution name shown by: wsl -l -v
wsl -d FVTT -- bash -lic "~/foundrydata/Data/modules/ai-gm-bridge/start_server.sh"
pause
