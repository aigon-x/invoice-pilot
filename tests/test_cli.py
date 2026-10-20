import json
from datetime import date, timedelta

import pytest

from invoicepilot.cli import main

ORDER = """Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "INVOICEPILOT_OFFLINE"):
        monkeypatch.delenv(name, raising=False)


def create(tmp_path, capsys, extra=None):
    args = ["--offline", "--store", str(tmp_path / "state"), "invoice", "create", "--text", ORDER]
    code = main(args + list(extra or []))
    return code, capsys.readouterr()


def only_record(tmp_path):
    data = json.loads((tmp_path / "state" / "invoices.json").read_text())
    return list(data.values())[0]


def test_offline_create_prints_mode_id_and_total(tmp_path, capsys):
    code, captured = create(tmp_path, capsys)
    assert code == 0
    assert "MODE: OFFLINE" in captured.out
    assert "ZERO wywo" in captured.out
    assert "total=2790.00" in captured.out
    assert "id=INV2-" in captured.out
    record = only_record(tmp_path)
    assert record["status"] == "SENT"
    assert record["offline"] is True


def test_global_flags_work_after_subcommand(tmp_path, capsys):
    code = main(
        [
            "invoice",
            "create",
            "--offline",
            "--store",
            str(tmp_path / "state"),
            "--text",
            ORDER,
        ]
    )
    assert code == 0
    assert "MODE: OFFLINE" in capsys.readouterr().out


def test_parse_error_returns_exit_code_two_and_hint(tmp_path, capsys):
    code = main(["--offline", "--store", str(tmp_path / "state"), "invoice", "create", "--text", "cokolwiek"])
    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR[parse]" in captured.err
    assert "nabywcy" in captured.err
    assert "Currency" in captured.err


def test_live_mode_without_keys_is_a_readable_error(tmp_path, capsys):
    code = main(["--store", str(tmp_path / "state"), "invoice", "create", "--text", ORDER])
    captured = capsys.readouterr()
    assert code == 3
    assert "ERROR[config]" in captured.err
    assert "PAYPAL_CLIENT_ID" in captured.err
    assert "--offline" in captured.err


def test_status_unknown_invoice_returns_six(tmp_path, capsys):
    code = main(["--offline", "--store", str(tmp_path / "state"), "invoice", "status", "INV2-NOPE"])
    captured = capsys.readouterr()
    assert code == 6
    assert "ERROR[InvoiceNotFound]" in captured.err


def test_status_after_create_shows_stored_invoice(tmp_path, capsys):
    create(tmp_path, capsys)
    invoice_id = only_record(tmp_path)["id"]
    capsys.readouterr()
    code = main(["--offline", "--store", str(tmp_path / "state"), "invoice", "status", invoice_id])
    out = capsys.readouterr().out
    assert code == 0
    assert invoice_id in out
    assert "2790.00" in out
    assert "status: SENT" in out


def test_remind_defaults_to_dry_run_and_does_not_change_state(tmp_path, capsys):
    create(tmp_path, capsys)
    before = only_record(tmp_path)
    capsys.readouterr()
    code = main(["--offline", "--store", str(tmp_path / "state"), "remind", "--as-of", "2026-12-01"])
    out = capsys.readouterr().out
    after = only_record(tmp_path)
    assert code == 0
    assert "dry_run=True" in out
    assert after["sent_on"] == before["sent_on"]
    assert after["reminder_days_done"] == []
    decisions = (tmp_path / "state" / "decisions.jsonl").read_text().strip().splitlines()
    assert decisions and all(json.loads(line)["dry_run"] for line in decisions)


def test_remind_execute_updates_invoice_and_log(tmp_path, capsys):
    create(tmp_path, capsys)
    capsys.readouterr()
    as_of = (date.today() + timedelta(days=7)).isoformat()
    code = main(
        ["--offline", "--store", str(tmp_path / "state"), "remind", "--execute", "--as-of", as_of]
    )
    out = capsys.readouterr().out
    record = only_record(tmp_path)
    assert code == 0
    assert "executed=True" in out
    assert record["reminders_sent"] == 1
    assert record["reminder_days_done"] == ["7"]
