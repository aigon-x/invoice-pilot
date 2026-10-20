"""Tiny JSON state store. Atomic writes, no database, no lock files."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable


class Store:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.invoices_path = self.root / "invoices.json"
        self.decisions_path = self.root / "decisions.jsonl"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.invoices_path.exists():
            return {}
        with self.invoices_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{self.invoices_path} does not contain an object")
        return data

    def get(self, invoice_id: str) -> dict[str, Any] | None:
        return self.load().get(invoice_id)

    def upsert(self, record: dict[str, Any]) -> None:
        invoice_id = record.get("id") or record.get("invoice_id")
        if not invoice_id:
            raise ValueError("record needs an id")
        self.ensure()
        data = self.load()
        data[str(invoice_id)] = record
        self._write_atomic(self.invoices_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def append_decision(self, entry: dict[str, Any]) -> None:
        self.ensure()
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def decisions(self) -> list[dict[str, Any]]:
        if not self.decisions_path.exists():
            return []
        entries = []
        with self.decisions_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise


def invoice_record(invoice, today: date, *, sent_on: str | None = None) -> dict[str, Any]:
    """Project a domain Invoice into the flat record persisted in state."""
    record = invoice.summary()
    record.update(
        {
            "status": invoice.status or "DRAFT",
            "buyer_email": invoice.buyer.email or "",
            "issue_date": invoice.issue_date.isoformat(),
            "sent_on": sent_on or "",
            "reminder_days_done": [],
            "escalated": False,
            "offline": False,
            "updated_at": today.isoformat(),
        }
    )
    return record


def merge_unique(existing: Iterable[str], new: Iterable[str]) -> list[str]:
    return sorted(set(existing) | set(new))
