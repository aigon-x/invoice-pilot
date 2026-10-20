import pytest
from fastapi.testclient import TestClient

from web.server import app

ORDER = """Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Implementation workshop @ 450.00
- 12h Development @ 120.00 EUR/h
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICEPILOT_WEB_STATE", str(tmp_path / "web"))
    monkeypatch.delenv("PAYPAL_AI_BASE_URL", raising=False)
    monkeypatch.delenv("PAYPAL_AI_API_KEY", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def _create(client, text=ORDER):
    response = client.post("/api/invoice", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_index_serves_the_interface(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Create invoice" in response.text
    assert "Simulate payment" in response.text
    assert 'aria-live="polite"' in response.text


def test_health_reports_offline_mode(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "offline"
    assert body["invoices"] == 0


def test_invoice_creation_returns_sent_invoice(client):
    body = _create(client)
    assert body["status"] == "SENT"
    assert body["ai_mode"] == "RULE-BASED"
    assert body["currency"] == "EUR"
    assert body["total"] == "2790.00"
    assert body["number"].startswith("INV-")
    assert len(body["items"]) == 2
    assert body["items"][0]["description"] == "Implementation workshop"
    assert body["items"][1]["quantity"] == "12"

    health = client.get("/api/health").json()
    assert health["invoices"] == 1


def test_webhook_moves_invoice_to_paid(client):
    created = _create(client)
    response = client.post("/api/webhook", json={"invoice_id": created["id"]},
                             headers={"X-Simulate-Payment": "enabled"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PAID"
    assert body["verified"] is True
    assert body["event_type"] == "INVOICING.INVOICE.PAID"
    assert body["id"] == created["id"]


def test_webhook_without_id_targets_latest_invoice(client):
    _create(client)
    second = _create(client, ORDER.replace("Acme", "Beta"))
    response = client.post("/api/webhook", json={}, headers={"X-Simulate-Payment": "enabled"})
    assert response.status_code == 200
    assert response.json()["id"] == second["id"]
    assert response.json()["status"] == "PAID"


def test_bad_order_text_returns_400(client):
    response = client.post("/api/invoice", json={"text": "please just invoice them"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "order text could not be parsed"
    assert any("nabywcy" in problem or "buyer" in problem for problem in detail["problems"])
    assert client.get("/api/health").json()["invoices"] == 0


def test_webhook_rejects_unknown_invoice(client):
    response = client.post("/api/webhook", json={"invoice_id": "INV2-NOPE"},
                             headers={"X-Simulate-Payment": "enabled"})
    assert response.status_code == 404


def test_webhook_requires_explicit_simulation_header(client):
    created = _create(client)
    response = client.post("/api/webhook", json={"invoice_id": created["id"]})
    assert response.status_code == 403


def test_webhook_replay_is_rejected(client):
    created = _create(client)
    payload = {"invoice_id": created["id"], "transmission_id": "t-1"}
    first = client.post("/api/webhook", json=payload, headers={"X-Simulate-Payment": "enabled"})
    second = client.post("/api/webhook", json=payload, headers={"X-Simulate-Payment": "enabled"})
    assert first.status_code == 200
    assert second.status_code == 409
