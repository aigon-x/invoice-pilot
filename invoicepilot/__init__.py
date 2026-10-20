"""InvoicePilot — turn an unstructured order note into a PayPal invoice.

Public surface kept small on purpose; the CLI (`python -m invoicepilot`) and
the tests are the only consumers.
"""

from .models import Buyer, Invoice, LineItem
from .parser import parse_order
from .paypal import MODE_LIVE, MODE_OFFLINE, PayPalClient
from .reminders import ReminderAgent, plan_reminders

__all__ = [
    "Buyer",
    "Invoice",
    "LineItem",
    "MODE_LIVE",
    "MODE_OFFLINE",
    "PayPalClient",
    "ReminderAgent",
    "parse_order",
    "plan_reminders",
]

__version__ = "1.0.0"
