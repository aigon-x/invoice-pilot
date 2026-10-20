import json

from invoicepilot.ai import AiExtractor


TEXT = """Client: ACME GmbH billing@acme.example
Currency: EUR
2 x Konsultacja @ 450.00 EUR
3 x Wsparcie @ 630.00 EUR
"""


def test_rule_based_fallback_is_labelled():
    result = AiExtractor(base_url="", api_key="").extract(TEXT)
    assert result.mode == "RULE-BASED"
    assert result.currency == "EUR"
    assert len(result.items) == 2


def test_grammar_round_trip_parses():
    result = AiExtractor(base_url="", api_key="").extract(TEXT)
    assert "Client: ACME GmbH" in result.to_grammar()


def test_llm_mode_used_when_configured(monkeypatch):
    extractor = AiExtractor(base_url="http://llm.local", api_key="k")

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            payload = {"choices": [{"message": {"content": json.dumps({
                "buyer": "ACME GmbH", "email": "billing@acme.example", "currency": "EUR",
                "items": [{"qty": 2, "description": "Konsultacja", "unit_price": 450.0}],
                "notes": None})}}]}
            return json.dumps(payload).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    result = extractor.extract(TEXT)
    assert result.mode == "LLM"
    assert result.items[0]["description"] == "Konsultacja"


def test_llm_failure_falls_back_to_rules(monkeypatch):
    extractor = AiExtractor(base_url="http://llm.local", api_key="k")

    def boom(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert extractor.extract(TEXT).mode == "RULE-BASED"
