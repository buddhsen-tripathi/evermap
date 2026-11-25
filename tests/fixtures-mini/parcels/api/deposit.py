"""Courier deposits a parcel into a free locker."""


def deposit_parcel(request, db):
    if "courier" not in request.roles:
        raise PermissionError("couriers only")
    with db.transaction() as tx:
        locker = tx.one("SELECT id FROM lockers WHERE free FOR UPDATE SKIP LOCKED")
        tx.run("UPDATE lockers SET free = false WHERE id = ?", locker)
        tx.run("INSERT INTO parcels (id, locker_id, status) VALUES (?, ?, 'stored')", request.id, locker)
        tx.run("INSERT INTO outbox (event) VALUES ('parcel.stored')")
    return locker
