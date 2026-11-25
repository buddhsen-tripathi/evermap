"""Sends a message to the recipient for each stored parcel."""


def on_parcel_stored(event, mailer, db):
    parcel = db.one("SELECT * FROM parcels WHERE id = ?", event.parcel_id)
    mailer.send(parcel.recipient, "Your parcel is ready")
