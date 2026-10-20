"""Error types with stable exit codes so the CLI never guesses."""

from __future__ import annotations


class InvoicePilotError(Exception):
    """Base class. `exit_code` is part of the CLI contract."""

    exit_code = 1

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OrderParseError(InvoicePilotError):
    """The order text could not be turned into a valid invoice."""

    exit_code = 2

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [])
        if self.problems:
            message = message + "\n" + "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(message)


class ConfigurationError(InvoicePilotError):
    """Live mode requested but credentials/configuration are missing."""

    exit_code = 3


class PayPalAPIError(InvoicePilotError):
    """Non-2xx answer from PayPal after retries were exhausted."""

    exit_code = 4

    def __init__(self, message: str, status: int = 0, issue: str = "") -> None:
        self.status = status
        self.issue = issue
        super().__init__(message)


class TransportError(InvoicePilotError):
    """Network-level failure (DNS, TLS, timeout)."""

    exit_code = 5


class InvoiceNotFound(InvoicePilotError):
    """Requested invoice id is not in the local store."""

    exit_code = 6


class ReminderError(InvoicePilotError):
    """Reminder could not be planned or executed."""

    exit_code = 7
