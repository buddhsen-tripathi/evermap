"""HTTP routes for the parcel locker service."""

from api import deposit, collect


def register(app):
    app.post("/parcels", deposit.deposit_parcel)
    app.post("/parcels/<id>/collect", collect.collect_parcel)


def legacy_status(request):
    return {"status": "gone"}
