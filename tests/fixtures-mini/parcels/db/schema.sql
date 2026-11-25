CREATE TABLE lockers (
  id integer PRIMARY KEY,
  free boolean NOT NULL DEFAULT true
);

CREATE TABLE parcels (
  id text PRIMARY KEY,
  locker_id integer REFERENCES lockers (id),
  pickup_code text,
  recipient text,
  status text NOT NULL
);

CREATE TABLE outbox (
  id integer PRIMARY KEY,
  event text NOT NULL
);
