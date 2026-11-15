#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python start.py
else
  exec python3 start.py
fi
