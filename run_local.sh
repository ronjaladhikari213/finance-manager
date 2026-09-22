#!/usr/bin/env bash
# Start the app on your own computer/Wi-Fi.  Usage: ./run_local.sh
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "  On this computer:  http://localhost:5000"
[ -n "$IP" ] && echo "  On your phone (same Wi-Fi):  http://$IP:5000"
echo ""
python3 app.py
