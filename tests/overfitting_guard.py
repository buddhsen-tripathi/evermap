"""Fail when the skill uses EverBench booking-fixture vocabulary (BENCHMARK 8.4).

Usage: python tests/overfitting_guard.py [path ...]   (default: skills/evermap)

Terms match whole words, case-insensitively; letters and digits are word characters, so
"room_id" matches "room" but "showroom" does not. Generic service names of the fixture
(identity, notifications, reporting, worker) are left out: the skill needs those words.
"""

import os
import re
import sys

DENYLIST = [
    # domain terms
    "booking", "bookings", "booked", "hotel", "hotels", "room", "rooms", "guest", "guests",
    "reservation", "reservations", "refund", "refunds", "refunded", "checkout", "check-in",
    "check-out", "check_in", "check_out", "nightly", "seasonal", "payment", "payments",
    "receipt", "receipts", "revenue", "promo", "fx",
    # service and component names
    "catalog", "legacy-import", "legacy_import", "pglite",
    # planted identifiers
    "v1QuoteHandler", "cancelBooking", "cancelReservation", "daily_revenue", "net_eur",
    "total_amount", "late-checkout-fee",
]
PATTERN = re.compile(r"(?<![A-Za-z0-9])(%s)(?![A-Za-z0-9])" % "|".join(re.escape(t) for t in DENYLIST), re.I)
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(os.path.dirname(HERE), "skills", "evermap")


def scan(root):
    """[(relative path, line number, term)] for every denylisted word under root."""
    hits = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
        for name in sorted(names):
            full = os.path.join(folder, name)
            with open(full, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    for match in PATTERN.finditer(line):
                        hits.append((os.path.relpath(full, root).replace(os.sep, "/"), number, match.group(1)))
    return hits


def main(argv):
    hits = []
    for root in argv or [DEFAULT]:
        hits += [(os.path.join(root, path), number, term) for path, number, term in scan(root)]
    for path, number, term in hits:
        print("%s:%d: fixture term %r" % (path, number, term))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
