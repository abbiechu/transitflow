-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Relational Tables
-- ============================================================

-- ------------------------------------------------------------
-- National Rail Stations (defined first because metro_stations references it)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id          VARCHAR(10)  PRIMARY KEY,
    name                VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_metro         BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)
);

-- National rail station lines
CREATE TABLE IF NOT EXISTS national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(10) NOT NULL,
    PRIMARY KEY (station_id, line)
);

-- ------------------------------------------------------------
-- Metro Stations
-- Using natural station_id (e.g. MS01) as PK — stable and human-readable,
-- no need for a surrogate key.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metro_stations (
    station_id          VARCHAR(10)  PRIMARY KEY,
    name                VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10) REFERENCES national_rail_stations(station_id) ON DELETE SET NULL
);

-- Metro station lines (one station can be on multiple lines)
CREATE TABLE IF NOT EXISTS metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(10) NOT NULL,
    PRIMARY KEY (station_id, line)
);

-- ------------------------------------------------------------
-- Metro Schedules
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id         VARCHAR(20)  PRIMARY KEY,
    line                VARCHAR(10)  NOT NULL,
    direction           VARCHAR(20)  NOT NULL,
    origin_station_id   VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    first_train_time    TIME         NOT NULL,
    last_train_time     TIME         NOT NULL,
    base_fare_usd       NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd   NUMERIC(6,2) NOT NULL,
    frequency_min       INTEGER      NOT NULL
);

-- Metro schedule operating days
CREATE TABLE IF NOT EXISTS metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week VARCHAR(10) NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Metro schedule stops (junction table — 3NF: stop order depends on schedule+station)
CREATE TABLE IF NOT EXISTS metro_schedule_stops (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id   VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stop_order   INTEGER      NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (schedule_id, station_id)
);

-- ------------------------------------------------------------
-- National Rail Schedules
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id         VARCHAR(20)  PRIMARY KEY,
    line                VARCHAR(10)  NOT NULL,
    service_type        VARCHAR(20)  NOT NULL, -- 'normal' or 'express'
    direction           VARCHAR(20)  NOT NULL,
    origin_station_id   VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    first_train_time    TIME         NOT NULL,
    last_train_time     TIME         NOT NULL,
    frequency_min       INTEGER      NOT NULL
);

-- National rail fare classes (one schedule has multiple fare classes)
CREATE TABLE IF NOT EXISTS national_rail_fare_classes (
    schedule_id       VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    fare_class        VARCHAR(20)  NOT NULL, -- 'standard' or 'first'
    base_fare_usd     NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

-- National rail schedule operating days
CREATE TABLE IF NOT EXISTS national_rail_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week VARCHAR(10) NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- National rail schedule stops (junction table)
CREATE TABLE IF NOT EXISTS national_rail_schedule_stops (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id   VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    stop_order   INTEGER      NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL DEFAULT 0,
    is_passing_stop BOOLEAN   NOT NULL DEFAULT FALSE, -- TRUE for express passed-through stations
    PRIMARY KEY (schedule_id, station_id)
);

-- National rail seat layouts
CREATE TABLE IF NOT EXISTS national_rail_seat_layouts (
    seat_id     VARCHAR(10)  NOT NULL,
    schedule_id VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    coach       VARCHAR(5)   NOT NULL,
    fare_class  VARCHAR(20)  NOT NULL,
    PRIMARY KEY (seat_id, schedule_id)
);

-- ------------------------------------------------------------
-- Users
-- Password stored as bcrypt hash — bcrypt is chosen over MD5/SHA
-- because it has a built-in cost factor that makes brute-force
-- attacks computationally expensive, and salt is automatically
-- included per hash so two users with the same password get
-- different hashes (defeats rainbow-table attacks).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    -- UUID chosen over SERIAL: user_id is exposed in API responses
    -- and auth tokens; UUID prevents enumeration attacks.
    user_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_user_id  VARCHAR(10)  UNIQUE, -- e.g. RU01, for seeding compatibility
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL, -- bcrypt hash, never plain text
    phone           VARCHAR(20),
    date_of_birth   DATE,
    secret_question VARCHAR(255),
    secret_answer   VARCHAR(255), -- stored as lowercase for case-insensitive comparison
    registered_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Soft delete: is_active=FALSE instead of deleting rows,
    -- so booking history and audit trails are preserved.
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ------------------------------------------------------------
-- National Rail Bookings
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS national_rail_bookings (
    -- UUID chosen: booking_id is customer-facing (receipts, cancellations)
    -- so we avoid exposing sequential integers.
    booking_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_booking_id VARCHAR(10) UNIQUE, -- e.g. BK001, for seeding compatibility
    user_id         UUID         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    schedule_id     VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id   VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date     DATE         NOT NULL,
    departure_time  TIME         NOT NULL,
    ticket_type     VARCHAR(20)  NOT NULL, -- 'single' or 'return'
    fare_class      VARCHAR(20)  NOT NULL,
    coach           VARCHAR(5),
    seat_id         VARCHAR(10),
    stops_travelled INTEGER      NOT NULL,
    amount_usd      NUMERIC(8,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'confirmed', -- 'confirmed', 'completed', 'cancelled'
    booked_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    travelled_at    TIMESTAMPTZ
);

-- ------------------------------------------------------------
-- Metro Trips
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metro_trips (
    trip_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_trip_id  VARCHAR(10)  UNIQUE,
    user_id         UUID         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    schedule_id     VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id   VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date     DATE         NOT NULL,
    ticket_type     VARCHAR(20)  NOT NULL, -- 'single' or 'day_pass'
    stops_travelled INTEGER,
    amount_usd      NUMERIC(8,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'confirmed',
    travelled_at    TIMESTAMPTZ
);

-- ------------------------------------------------------------
-- Payments
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    payment_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_payment_id VARCHAR(10) UNIQUE,
    -- booking_id can refer to national_rail_bookings OR metro_trips
    -- We store both FKs and ensure exactly one is non-null via CHECK.
    national_rail_booking_id UUID REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    metro_trip_id            UUID REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    amount_usd      NUMERIC(8,2) NOT NULL,
    payment_method  VARCHAR(30)  NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'completed',
    paid_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT payment_one_booking CHECK (
        (national_rail_booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (national_rail_booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

-- ------------------------------------------------------------
-- Feedback
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_feedback_id VARCHAR(10) UNIQUE,
    user_id         UUID         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    national_rail_booking_id UUID REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    metro_trip_id            UUID REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    rating          INTEGER      NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT,
    submitted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_documents_embedding_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);