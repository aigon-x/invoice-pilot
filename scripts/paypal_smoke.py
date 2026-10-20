#!/usr/bin/env python3
"""Live sandbox smoke: OAuth, then create a draft invoice.

Credentials come from the environment (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET) or from
/opt/PROD/products/monai/.env when run as root. Values are never printed.
Exit codes: 0 ok, 2 missing credentials, 3 OAuth rejected, 4 Invoicing not permitted.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")


def credentials() -> tuple[str, str]:
    cid, sec = os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    path = "/opt/PROD/products/monai/.env"
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.startswith("PAYPAL_CLIENT_ID="):
                cid = line.split("=", 1)[1].strip()
            if line.startswith("PAYPAL_CLIENT_SECRET="):
                sec = line.split("=", 1)[1].strip()
    return cid or "", sec or ""


def main() -> int:
    cid, sec = credentials()
    if not (cid and sec):
        print("missing PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET")
        return 2
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(
        f"{BASE}/v1/oauth2/token", data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.loads(resp.read().decode())["access_token"]
    except urllib.error.HTTPError as exc:
        print("oauth rejected:", exc.code)
        return 3
    print("oauth: 200")

    invoice = {
        "detail": {"currency_code": "EUR", "invoice_number": "MONAI-SMOKE",
                   "invoice_date": "2026-10-20"},
        "primary_recipients": [{"billing_info": {"email_address": "sb-buyer@example.com"}}],
        "items": [{"name": "Agent build", "quantity": "1",
                   "unit_amount": {"currency_code": "EUR", "value": "100.00"}}],
    }
    req = urllib.request.Request(
        f"{BASE}/v2/invoicing/invoices", data=json.dumps(invoice).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "PayPal-Request-Id": str(uuid.uuid4())}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            created = json.loads(resp.read().decode())
        print("invoice created:", resp.status, "id:", created.get("id"), "status:", created.get("status"))
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        print("invoice rejected:", exc.code, body)
        return 4 if exc.code == 403 else 1


if __name__ == "__main__":
    sys.exit(main())
