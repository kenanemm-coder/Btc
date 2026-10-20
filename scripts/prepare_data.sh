#!/usr/bin/env bash
# 'data' dalını ./data klasörüne hazırlar ve GITHUB_OUTPUT'a init=... yazar.
#   init=--init : yeni sanal portföy kurulabilir (state hiç var olmamış)
#   init=       : mevcut state kullanılacak
# State bir zamanlar vardıysa ama şimdi yoksa, ya da GitHub'a erişilemezse DURUR
# (portföy yanlışlıkla sıfırlanmasın diye).
# Kullanım: REMOTE=<git url> bash scripts/prepare_data.sh
set -euo pipefail
OUT="${GITHUB_OUTPUT:-/dev/stdout}"
: "${REMOTE:?REMOTE tanımlı olmalı}"

set +e
git ls-remote --exit-code --heads "$REMOTE" data >/dev/null 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
  rm -rf data
  git clone --quiet --branch data "$REMOTE" data
  if [ -f data/paper_state.json ]; then
    init=""
  elif [ -z "$(git -C data log --oneline -- paper_state.json | head -n 1)" ]; then
    echo "data dalı var ama state hiç oluşturulmamış: ilk kurulum yapılacak"
    init="--init"
  else
    echo "::error::paper_state.json geçmişte vardı ama şimdi yok; portföy sıfırlanmasın diye durduruldu"
    exit 1
  fi
elif [ "$rc" -eq 2 ]; then
  echo "data dalı yok: ilk kurulum yapılacak"
  rm -rf data
  mkdir data
  git -C data init -q -b data
  git -C data remote add origin "$REMOTE"
  init="--init"
else
  echo "::error::GitHub'a erişilemedi (kod $rc); state sıfırlanmasın diye durduruldu"
  exit 1
fi

git -C data config user.name "paper-bot"
git -C data config user.email "paper-bot@users.noreply.github.com"
printf '*.lock\n*.tmp\n' > data/.gitignore
echo "init=$init" >> "$OUT"
