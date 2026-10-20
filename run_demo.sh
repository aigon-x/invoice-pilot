#!/usr/bin/env bash
# Reproducible offline demo. No network, no credentials, resets demo-state/.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-/usr/bin/python3}"
rm -rf demo-state

cat > demo-order.txt <<'EOF'
Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h
EOF

echo "== tests =="
"$PY" -m pytest -q

echo "== create (offline) =="
"$PY" -m invoicepilot --offline --store demo-state invoice create --file demo-order.txt

ID="$("$PY" -c "import json;print(list(json.load(open('demo-state/invoices.json')).values())[0]['id'])")"

echo "== status =="
"$PY" -m invoicepilot --offline --store demo-state invoice status "$ID"

echo "== reminder plan (dry-run, D+7) =="
"$PY" -m invoicepilot --offline --store demo-state remind --dry-run --as-of 2026-10-27

echo "== reminder execution (D+7) =="
"$PY" -m invoicepilot --offline --store demo-state remind --execute --as-of 2026-10-27

rm -f demo-order.txt /tmp/invoicepilot-demo-order.txt
