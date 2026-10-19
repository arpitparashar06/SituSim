#!/usr/bin/env bash
# Sets up a virtual environment the first time, then starts SituSim.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: creating .venv and installing packages..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python main.py
