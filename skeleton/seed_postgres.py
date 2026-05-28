"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
Safe to re-run: all inserts use ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import bcrypt
import psycopg2
from psycopg2.extras import execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_national_rail_stations(cur):
    """Seed national rail stations and their lines."""
    data = load("national_rail_stations.json")

    # Insert stations
    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_national_rail"],
            s["is_interchange_metro"],
            s.get("interchange_metro_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "national_rail_stations",
                    ["station_id", "name", "is_interchange_national_rail",
                     "is_interchange_metro", "interchange_metro_station_id"], rows)
    print(f"  national_rail_stations: {n} rows")

    # Insert station lines
    line_rows = []
    for s in data:
        for line in s["lines"]:
            line_rows.append((s["station_id"], line))
    n = insert_many(cur, "national_rail_station_lines", ["station_id", "line"], line_rows)
    print(f"  national_rail_station_lines: {n} rows")


def seed_metro_stations(cur):
    """Seed metro stations and their lines."""
    data = load("metro_stations.json")

    # Insert stations
    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_metro"],
            s["is_interchange_national_rail"],
            s.get("interchange_national_rail_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "metro_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "is_interchange_national_rail",
                     "interchange_national_rail_station_id"], rows)
    print(f"  metro_stations: {n} rows")

    # Insert station lines
    line_rows = []
    for s in data:
        for line in s["lines"]:
            line_rows.append((s["station_id"], line))
    n = insert_many(cur, "metro_station_lines", ["station_id", "line"], line_rows)
    print(f"  metro_station_lines: {n} rows")


def seed_metro_schedules(cur):
    """Seed metro schedules, stops, and operating days."""
    data = load("metro_schedules.json")

    # Insert schedules
    rows = [
        (
            s["schedule_id"],
            s["line"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["base_fare_usd"],
            s["per_stop_rate_usd"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "metro_schedules",
                    ["schedule_id", "line", "direction", "origin_station_id",
                     "destination_station_id", "first_train_time", "last_train_time",
                     "base_fare_usd", "per_stop_rate_usd", "frequency_min"], rows)
    print(f"  metro_schedules: {n} rows")

    # Insert stops (junction table — each stop gets its own row)
    stop_rows = []
    for s in data:
        for order, station_id in enumerate(s["stops_in_order"]):
            travel_time = s["travel_time_from_origin_min"].get(station_id, 0)
            stop_rows.append((s["schedule_id"], station_id, order + 1, travel_time))
    n = insert_many(cur, "metro_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"], stop_rows)
    print(f"  metro_schedule_stops: {n} rows")

    # Insert operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "metro_schedule_days", ["schedule_id", "day_of_week"], day_rows)
    print(f"  metro_schedule_days: {n} rows")


def seed_national_rail_schedules(cur):
    """Seed national rail schedules, fare classes, stops, and operating days."""
    data = load("national_rail_schedules.json")

    # Insert schedules
    rows = [
        (
            s["schedule_id"],
            s["line"],
            s["service_type"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "national_rail_schedules",
                    ["schedule_id", "line", "service_type", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time", "frequency_min"], rows)
    print(f"  national_rail_schedules: {n} rows")

    # Insert fare classes (standard and first class have different rates)
    fare_rows = []
    for s in data:
        for fare_class, fare_info in s["fare_classes"].items():
            fare_rows.append((
                s["schedule_id"],
                fare_class,
                fare_info["base_fare_usd"],
                fare_info["per_stop_rate_usd"],
            ))
    n = insert_many(cur, "national_rail_fare_classes",
                    ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"],
                    fare_rows)
    print(f"  national_rail_fare_classes: {n} rows")

    # Insert stops
    stop_rows = []
    for s in data:
        for order, station_id in enumerate(s["stops_in_order"]):
            travel_time = s["travel_time_from_origin_min"].get(station_id, 0)
            stop_rows.append((s["schedule_id"], station_id, order + 1, travel_time, False))
        # Express services have passing stops (not served but physically passed through)
        for station_id in s.get("passed_through_stations", []):
            stop_rows.append((s["schedule_id"], station_id, 0, 0, True))
    n = insert_many(cur, "national_rail_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min", "is_passing_stop"], stop_rows)
    print(f"  national_rail_schedule_stops: {n} rows")

    # Insert operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "national_rail_schedule_days",
                    ["schedule_id", "day_of_week"], day_rows)
    print(f"  national_rail_schedule_days: {n} rows")


def seed_seat_layouts(cur):
    """Seed national rail seat layouts."""
    data = load("national_rail_seat_layouts.json")

    rows = []
    for layout in data:
        for coach in layout["coaches"]:
            for seat in coach["seats"]:
                rows.append((
                    seat["seat_id"],
                    layout["schedule_id"],
                    coach["coach"],
                    coach["fare_class"],
                ))
    n = insert_many(cur, "national_rail_seat_layouts",
                    ["seat_id", "schedule_id", "coach", "fare_class"], rows)
    print(f"  national_rail_seat_layouts: {n} rows")


def seed_users(cur):
    """Seed users with bcrypt-hashed passwords.
    
    bcrypt automatically generates a unique salt per user, so two users
    with the same password will have different hashes — this defeats
    rainbow-table attacks.
    """
    data = load("registered_users.json")

    rows = []
    for u in data:
        # Hash the password with bcrypt before storing — never store plain text
        password_hash = bcrypt.hashpw(
            u["password"].encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        rows.append((
            u["user_id"],       # used as legacy_user_id for seeding compatibility
            u["full_name"],
            u["email"],
            password_hash,
            u.get("phone"),
            u.get("date_of_birth"),
            u.get("secret_question"),
            u.get("secret_answer", "").lower() if u.get("secret_answer") else None,
            u["registered_at"],
            u["is_active"],
        ))

    n = insert_many(cur, "users",
                    ["legacy_user_id", "full_name", "email", "password_hash",
                     "phone", "date_of_birth", "secret_question", "secret_answer",
                     "registered_at", "is_active"], rows)
    print(f"  users: {n} rows")


def seed_national_rail_bookings(cur):
    """Seed national rail bookings, looking up UUID user_id from legacy_user_id."""
    data = load("bookings.json")

    rows = []
    for b in data:
        rows.append((
            b["booking_id"],    # legacy_booking_id e.g. BK001
            b["user_id"],       # legacy_user_id e.g. RU01
            b["schedule_id"],
            b["origin_station_id"],
            b["destination_station_id"],
            b["travel_date"],
            b["departure_time"],
            b["ticket_type"],
            b["fare_class"],
            b.get("coach"),
            b.get("seat_id"),
            b["stops_travelled"],
            b["amount_usd"],
            b["status"],
            b["booked_at"],
            b.get("travelled_at"),
        ))

    # Use a subquery to convert legacy_user_id to UUID
    sql = """
        INSERT INTO national_rail_bookings (
            legacy_booking_id, user_id, schedule_id,
            origin_station_id, destination_station_id,
            travel_date, departure_time, ticket_type, fare_class,
            coach, seat_id, stops_travelled, amount_usd, status,
            booked_at, travelled_at
        )
        SELECT
            %s,
            (SELECT user_id FROM users WHERE legacy_user_id = %s),
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ON CONFLICT DO NOTHING
    """
    count = 0
    for row in rows:
        cur.execute(sql, row)
        count += cur.rowcount
    print(f"  national_rail_bookings: {count} rows")


def seed_metro_travels(cur):
    """Seed metro trip history."""
    data = load("metro_travel_history.json")

    rows = []
    for t in data:
        rows.append((
            t["trip_id"],       # legacy_trip_id
            t["user_id"],       # legacy_user_id
            t["schedule_id"],
            t["origin_station_id"],
            t["destination_station_id"],
            t["travel_date"],
            t["ticket_type"],
            t.get("stops_travelled"),
            t["amount_usd"],
            t["status"],
            t.get("travelled_at"),
        ))

    sql = """
        INSERT INTO metro_trips (
            legacy_trip_id, user_id, schedule_id,
            origin_station_id, destination_station_id,
            travel_date, ticket_type, stops_travelled,
            amount_usd, status, travelled_at
        )
        SELECT
            %s,
            (SELECT user_id FROM users WHERE legacy_user_id = %s),
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        ON CONFLICT DO NOTHING
    """
    count = 0
    for row in rows:
        cur.execute(sql, row)
        count += cur.rowcount
    print(f"  metro_trips: {count} rows")


def seed_payments(cur):
    """Seed payments, linking to national_rail_bookings or metro_trips."""
    data = load("payments.json")

    count = 0
    for p in data:
        booking_id = p["booking_id"]

        # Determine if this payment is for a national rail booking or metro trip
        # BK prefix = national rail, MT prefix = metro
        if booking_id.startswith("BK"):
            sql = """
                INSERT INTO payments (
                    legacy_payment_id, national_rail_booking_id,
                    amount_usd, payment_method, status, paid_at
                )
                SELECT %s,
                    (SELECT booking_id FROM national_rail_bookings
                     WHERE legacy_booking_id = %s),
                    %s, %s, %s, %s
                ON CONFLICT DO NOTHING
            """
        else:
            sql = """
                INSERT INTO payments (
                    legacy_payment_id, metro_trip_id,
                    amount_usd, payment_method, status, paid_at
                )
                SELECT %s,
                    (SELECT trip_id FROM metro_trips
                     WHERE legacy_trip_id = %s),
                    %s, %s, %s, %s
                ON CONFLICT DO NOTHING
            """

        cur.execute(sql, (
            p["payment_id"],
            booking_id,
            p["amount_usd"],
            p["method"],
            p["status"],
            p["paid_at"],
        ))
        count += cur.rowcount

    print(f"  payments: {count} rows")


def seed_feedback(cur):
    """Seed passenger feedback linked to bookings or metro trips."""
    data = load("feedback.json")

    count = 0
    for f in data:
        booking_id = f["booking_id"]

        if booking_id.startswith("BK"):
            sql = """
                INSERT INTO feedback (
                    legacy_feedback_id, user_id,
                    national_rail_booking_id, rating, comment, submitted_at
                )
                SELECT %s,
                    (SELECT user_id FROM users WHERE legacy_user_id = %s),
                    (SELECT booking_id FROM national_rail_bookings
                     WHERE legacy_booking_id = %s),
                    %s, %s, %s
                ON CONFLICT DO NOTHING
            """
        else:
            sql = """
                INSERT INTO feedback (
                    legacy_feedback_id, user_id,
                    metro_trip_id, rating, comment, submitted_at
                )
                SELECT %s,
                    (SELECT user_id FROM users WHERE legacy_user_id = %s),
                    (SELECT trip_id FROM metro_trips
                     WHERE legacy_trip_id = %s),
                    %s, %s, %s
                ON CONFLICT DO NOTHING
            """

        cur.execute(sql, (
            f["feedback_id"],
            f["user_id"],
            booking_id,
            f["rating"],
            f.get("comment"),
            f["submitted_at"],
        ))
        count += cur.rowcount

    print(f"  feedback: {count} rows")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        # national_rail_stations must come before metro_stations
        # because metro_stations references it
        seed_national_rail_stations(cur)
        seed_metro_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()