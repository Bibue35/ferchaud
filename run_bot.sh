#!/bin/bash
# Watchdog: auto-restarts the bot if it dies
cd /Users/louistenant/PycharmProject/quant_bot

while true; do
    echo "[WATCHDOG] Starting bot at $(date)" >> /tmp/watchdog.log
    /Users/louistenant/opt/miniconda3/bin/python3 main.py >> /tmp/quantbot.log 2>&1
    EXIT_CODE=$?
    echo "[WATCHDOG] Bot exited with code $EXIT_CODE at $(date). Restarting in 5s..." >> /tmp/watchdog.log
    sleep 5
done
