#!/bin/sh
# STORY 13.3: after 2026-10-24 22:00 KST every public/operator path must be refused (410).
U=${1:-https://frontier-market.vercel.app}
for p in / /api/demo/status /api/demo/bootstrap /static/app.js /api/records/works; do
  printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$U$p")" "$p"
done
printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' -d '{"token":"x"}' "$U/api/auth/login")" "POST /api/auth/login"
