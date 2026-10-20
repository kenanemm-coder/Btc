#!/usr/bin/env bash
# ./data içindeki değişiklikleri 'data' dalına commit + push eder.
# paper_state.json yoksa HİÇBİR ŞEY commit edilmez (state'siz bir data dalı oluşmasın).
set -euo pipefail
cd data
if [ ! -f paper_state.json ]; then
  echo "State dosyası yok: commit edilmedi."
  exit 0
fi
git add -A
if git diff --cached --quiet; then
  echo "State değişmedi."
  exit 0
fi
git commit -q -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  if git push -q origin HEAD:data; then
    exit 0
  fi
  git pull -q --rebase origin data || true
  sleep "${PUSH_RETRY_SLEEP:-3}"
done
echo "::error::State push edilemedi"
exit 1
