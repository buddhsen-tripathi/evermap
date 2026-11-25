def test_one_parcel_per_locker(db):
    assert db.count_parcels_sharing_a_locker() == 0
