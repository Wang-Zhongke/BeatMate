#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print '首次使用请先在项目目录运行：'
  print 'python3 -m venv .venv'
  print '.venv/bin/python -m pip install -r requirements.txt'
  exit 1
fi
exec .venv/bin/python -m beatmate.audio_start "$@"
