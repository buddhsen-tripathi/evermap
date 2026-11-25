"""Recipient collects a parcel with a pickup code."""

MAX_DAYS = 7


def is_expired(parcel, today):
    return (today - parcel.stored_on).days > MAX_DAYS


def collect_parcel(request, db):
    parcel = db.one("SELECT * FROM parcels WHERE id = ?", request.id)
    if parcel.pickup_code != request.code:
        raise PermissionError("wrong code")
    if is_expired(parcel, request.today):
        raise ValueError("expired")
    db.run("UPDATE parcels SET status = 'collected' WHERE id = ? AND status = 'stored'", parcel.id)
    db.run("UPDATE lockers SET free = true WHERE id = ?", parcel.locker_id)
