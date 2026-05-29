"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination
    in the correct order, with available seat counts for the travel date.

    We join schedule_stops twice (once for origin, once for destination)
    and ensure origin stop_order < destination stop_order so we only
    return services going in the right direction.
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            orig.travel_time_from_origin_min  AS origin_time,
            dest.travel_time_from_origin_min  AS destination_time,
            dest.stop_order - orig.stop_order AS stops_travelled,
            -- Count total seats minus already-booked seats for this date
            (
                SELECT COUNT(*) FROM national_rail_seat_layouts sl
                WHERE sl.schedule_id = s.schedule_id
            ) -
            (
                SELECT COUNT(*) FROM national_rail_bookings b
                WHERE b.schedule_id       = s.schedule_id
                  AND b.travel_date       = %s::date
                  AND b.status           != 'cancelled'
            ) AS available_seats
        FROM national_rail_schedules s
        -- Join stops for origin station
        JOIN national_rail_schedule_stops orig
            ON orig.schedule_id = s.schedule_id
           AND orig.station_id  = %s
           AND orig.is_passing_stop = FALSE
        -- Join stops for destination station
        JOIN national_rail_schedule_stops dest
            ON dest.schedule_id = s.schedule_id
           AND dest.station_id  = %s
           AND dest.is_passing_stop = FALSE
        -- Ensure origin comes before destination in stop order
        WHERE orig.stop_order < dest.stop_order
        ORDER BY s.first_train_time
    """
    date_val = travel_date if travel_date else datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (date_val, origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.
    total = base_fare + (per_stop_rate × stops_travelled)
    """
    sql = """
        SELECT fare_class, base_fare_usd, per_stop_rate_usd
        FROM national_rail_fare_classes
        WHERE schedule_id = %s AND fare_class = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class))
            row = cur.fetchone()
            if not row:
                return None
            base = float(row["base_fare_usd"])
            rate = float(row["per_stop_rate_usd"])
            return {
                "fare_class":        fare_class,
                "base_fare_usd":     base,
                "per_stop_rate_usd": rate,
                "total_fare_usd":    round(base + rate * stops_travelled, 2),
            }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination
    in the correct order (origin stop_order < destination stop_order).
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            orig.stop_order                   AS origin_stop_order,
            dest.stop_order                   AS destination_stop_order,
            dest.stop_order - orig.stop_order AS stops_travelled
        FROM metro_schedules s
        JOIN metro_schedule_stops orig
            ON orig.schedule_id = s.schedule_id
           AND orig.station_id  = %s
        JOIN metro_schedule_stops dest
            ON dest.schedule_id = s.schedule_id
           AND dest.station_id  = %s
        WHERE orig.stop_order < dest.stop_order
        ORDER BY s.line, s.direction
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.
    total = base_fare + (per_stop_rate × stops_travelled)
    """
    sql = """
        SELECT base_fare_usd, per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            if not row:
                return None
            base = float(row["base_fare_usd"])
            rate = float(row["per_stop_rate_usd"])
            return {
                "base_fare_usd":     base,
                "per_stop_rate_usd": rate,
                "total_fare_usd":    round(base + rate * stops_travelled, 2),
            }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return seats that are in the requested fare class and not yet booked
    for this schedule and date.

    We select all seats for the schedule/fare_class, then exclude any
    seat_id that already appears in national_rail_bookings for that date.
    """
    sql = """
        SELECT sl.seat_id, sl.coach, sl.fare_class
        FROM national_rail_seat_layouts sl
        WHERE sl.schedule_id = %s
          AND sl.fare_class  = %s
          -- Exclude seats already booked for this date
          AND sl.seat_id NOT IN (
              SELECT b.seat_id
              FROM national_rail_bookings b
              WHERE b.schedule_id  = %s
                AND b.travel_date  = %s::date
                AND b.status      != 'cancelled'
                AND b.seat_id IS NOT NULL
          )
        ORDER BY sl.coach, sl.seat_id
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, schedule_id, travel_date))
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select seats that are as close together as possible."""
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]
    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)
    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]
    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email, or None if not found."""
    sql = """
        SELECT
            user_id::text,
            legacy_user_id,
            full_name,
            email,
            phone,
            date_of_birth::text,
            secret_question,
            registered_at::text,
            is_active
        FROM users
        WHERE email = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history.
    Always returns both keys even if lists are empty.
    """
    # Get user_id from email first
    user = query_user_profile(user_email)
    if not user:
        return {"national_rail": [], "metro": []}

    user_uuid = user["user_id"]

    # National rail bookings
    nr_sql = """
        SELECT
            b.booking_id::text,
            b.legacy_booking_id AS booking_id_legacy,
            b.travel_date::text,
            b.departure_time::text,
            b.ticket_type,
            b.fare_class,
            b.coach,
            b.seat_id,
            b.stops_travelled,
            b.amount_usd,
            b.status,
            b.booked_at::text,
            orig.name AS origin_name,
            dest.name AS destination_name,
            s.line,
            s.service_type
        FROM national_rail_bookings b
        JOIN national_rail_stations orig ON orig.station_id = b.origin_station_id
        JOIN national_rail_stations dest ON dest.station_id = b.destination_station_id
        JOIN national_rail_schedules s   ON s.schedule_id  = b.schedule_id
        WHERE b.user_id = %s::uuid
        ORDER BY b.travel_date DESC
    """

    # Metro trips
    mt_sql = """
        SELECT
            t.trip_id::text,
            t.legacy_trip_id,
            t.travel_date::text,
            t.ticket_type,
            t.stops_travelled,
            t.amount_usd,
            t.status,
            t.travelled_at::text,
            orig.name AS origin_name,
            dest.name AS destination_name,
            s.line
        FROM metro_trips t
        JOIN metro_stations orig ON orig.station_id = t.origin_station_id
        JOIN metro_stations dest ON dest.station_id = t.destination_station_id
        JOIN metro_schedules s   ON s.schedule_id  = t.schedule_id
        WHERE t.user_id = %s::uuid
        ORDER BY t.travel_date DESC
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(nr_sql, (user_uuid,))
            national_rail = [dict(row) for row in cur.fetchall()]
            cur.execute(mt_sql, (user_uuid,))
            metro = [dict(row) for row in cur.fetchall()]

    return {"national_rail": national_rail, "metro": metro}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment record for a booking (national rail or metro).
    Accepts legacy booking IDs (BK001) or generated IDs (BK-XXXXXX).
    """
    sql = """
        SELECT
            p.payment_id::text,
            p.legacy_payment_id,
            p.amount_usd,
            p.payment_method,
            p.status,
            p.paid_at::text,
            p.national_rail_booking_id::text,
            p.metro_trip_id::text
        FROM payments p
        LEFT JOIN national_rail_bookings b
            ON b.booking_id = p.national_rail_booking_id
        LEFT JOIN metro_trips t
            ON t.trip_id = p.metro_trip_id
        WHERE b.legacy_booking_id = %s
           OR t.legacy_trip_id    = %s
           OR b.booking_id::text  = %s
           OR t.trip_id::text     = %s
        LIMIT 1
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id, booking_id, booking_id, booking_id))
            row = cur.fetchone()
            return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking atomically.
    Both the booking and payment records are inserted in a single transaction —
    if either fails, both are rolled back (all-or-nothing).
    Returns (True, booking_dict) on success, (False, error_message) on failure.
    """
    # Use manual connection so we control commit/rollback
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Look up user UUID from legacy_user_id or email
            cur.execute("""
                SELECT user_id, full_name FROM users
                WHERE legacy_user_id = %s OR email = %s
            """, (user_id, user_id))
            user = cur.fetchone()
            if not user:
                return False, f"User '{user_id}' not found."
            user_uuid = user["user_id"]

            # Check seat is not already taken
            cur.execute("""
                SELECT 1 FROM national_rail_bookings
                WHERE schedule_id  = %s
                  AND travel_date  = %s::date
                  AND seat_id      = %s
                  AND status      != 'cancelled'
            """, (schedule_id, travel_date, seat_id))
            if cur.fetchone():
                return False, f"Seat {seat_id} is already booked for {travel_date}."

            # Calculate stops travelled and fare
            cur.execute("""
                SELECT
                    dest.stop_order - orig.stop_order AS stops_travelled,
                    orig.travel_time_from_origin_min  AS origin_time
                FROM national_rail_schedule_stops orig
                JOIN national_rail_schedule_stops dest
                    ON dest.schedule_id = orig.schedule_id
                   AND dest.station_id  = %s
                WHERE orig.schedule_id = %s
                  AND orig.station_id  = %s
            """, (destination_station_id, schedule_id, origin_station_id))
            route = cur.fetchone()
            if not route:
                return False, "Route not found for given stations."

            stops = route["stops_travelled"]
            fare = query_national_rail_fare(schedule_id, fare_class, stops)
            if not fare:
                return False, f"Fare class '{fare_class}' not found."
            amount = fare["total_fare_usd"]

            # Get departure time from schedule
            cur.execute("""
                SELECT first_train_time::text FROM national_rail_schedules
                WHERE schedule_id = %s
            """, (schedule_id,))
            sched = cur.fetchone()
            departure_time = sched["first_train_time"] if sched else "00:00"

            # Get coach for the seat
            cur.execute("""
                SELECT coach FROM national_rail_seat_layouts
                WHERE schedule_id = %s AND seat_id = %s
            """, (schedule_id, seat_id))
            seat_row = cur.fetchone()
            coach = seat_row["coach"] if seat_row else None

            # Insert booking
            new_booking_id = _gen_booking_id()
            cur.execute("""
                INSERT INTO national_rail_bookings (
                    legacy_booking_id, user_id, schedule_id,
                    origin_station_id, destination_station_id,
                    travel_date, departure_time, ticket_type, fare_class,
                    coach, seat_id, stops_travelled, amount_usd, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed')
                RETURNING booking_id::text, legacy_booking_id
            """, (
                new_booking_id, user_uuid, schedule_id,
                origin_station_id, destination_station_id,
                travel_date, departure_time, ticket_type, fare_class,
                coach, seat_id, stops, amount,
            ))
            booking = cur.fetchone()

            # Insert payment in the same transaction (atomic)
            new_payment_id = _gen_payment_id()
            cur.execute("""
                INSERT INTO payments (
                    legacy_payment_id, national_rail_booking_id,
                    amount_usd, payment_method, status
                ) VALUES (%s, %s, %s, 'credit_card', 'completed')
            """, (new_payment_id, booking["booking_id"]))

            # Commit both booking and payment together
            conn.commit()

            return True, {
                "booking_id":    booking["legacy_booking_id"],
                "user_id":       user_id,
                "schedule_id":   schedule_id,
                "seat_id":       seat_id,
                "travel_date":   travel_date,
                "fare_class":    fare_class,
                "amount_usd":    amount,
                "status":        "confirmed",
            }

    except Exception as e:
        conn.rollback()
        return False, f"Booking failed: {str(e)}"
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a booking and calculate refund based on time before travel.
    Refund policy:
      - More than 7 days before: 100% refund
      - 3-7 days before:         75% refund
      - 1-2 days before:         50% refund
      - Less than 1 day:         0% refund
    Returns (True, result_dict) or (False, error_message).
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Find booking by legacy_booking_id or UUID
            cur.execute("""
                SELECT
                    b.booking_id::text,
                    b.legacy_booking_id,
                    b.user_id::text,
                    b.travel_date,
                    b.amount_usd,
                    b.status,
                    u.legacy_user_id
                FROM national_rail_bookings b
                JOIN users u ON u.user_id = b.user_id
                WHERE b.legacy_booking_id = %s
                   OR b.booking_id::text  = %s
            """, (booking_id, booking_id))
            booking = cur.fetchone()

            if not booking:
                return False, f"Booking '{booking_id}' not found."
            if booking["status"] == "cancelled":
                return False, f"Booking '{booking_id}' is already cancelled."

            # Verify user owns this booking
            if (booking["legacy_user_id"] != user_id and
                    booking["user_id"] != user_id):
                return False, "You can only cancel your own bookings."

            # Calculate refund based on days until travel
            days_until = (booking["travel_date"] - datetime.now(timezone.utc).date()).days
            amount = float(booking["amount_usd"])

            if days_until >= 7:
                refund_pct = 1.0
                policy_note = "100% refund — cancelled more than 7 days before travel"
            elif days_until >= 3:
                refund_pct = 0.75
                policy_note = "75% refund — cancelled 3-7 days before travel"
            elif days_until >= 1:
                refund_pct = 0.5
                policy_note = "50% refund — cancelled 1-2 days before travel"
            else:
                refund_pct = 0.0
                policy_note = "No refund — cancelled less than 1 day before travel"

            refund = round(amount * refund_pct, 2)

            # Update booking status
            cur.execute("""
                UPDATE national_rail_bookings
                SET status = 'cancelled'
                WHERE booking_id = %s::uuid
            """, (booking["booking_id"],))

            # Update payment status if refund applies
            if refund > 0:
                cur.execute("""
                    UPDATE payments SET status = 'refunded'
                    WHERE national_rail_booking_id = %s::uuid
                """, (booking["booking_id"],))

            conn.commit()

            return True, {
                "booking_id":       booking["legacy_booking_id"] or booking_id,
                "status":           "cancelled",
                "refund_amount_usd": refund,
                "policy_note":      policy_note,
            }

    except Exception as e:
        conn.rollback()
        return False, f"Cancellation failed: {str(e)}"
    finally:
        conn.close()


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user with bcrypt-hashed password.
    Returns (True, user_id) on success or (False, error_message) on failure.
    """
    # Hash password with bcrypt before storing
    password_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    sql = """
        INSERT INTO users (
            full_name, email, password_hash,
            date_of_birth, secret_question, secret_answer, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        RETURNING user_id::text
    """
    full_name = f"{first_name} {surname}"
    dob = f"{year_of_birth}-01-01"  # approximate date of birth from year

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    full_name, email, password_hash,
                    dob, secret_question, secret_answer.lower(),
                ))
                user_id = cur.fetchone()[0]
                return True, user_id
    except psycopg2.errors.UniqueViolation:
        return False, f"Email '{email}' is already registered."
    except Exception as e:
        return False, str(e)


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials using bcrypt.
    Returns user dict on success, None on failure.
    """
    sql = """
        SELECT
            user_id::text,
            legacy_user_id,
            full_name,
            email,
            password_hash,
            phone,
            date_of_birth::text,
            is_active
        FROM users
        WHERE email = %s AND is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row:
                return None
            # Verify password against stored bcrypt hash
            if not bcrypt.checkpw(password.encode("utf-8"),
                                   row["password_hash"].encode("utf-8")):
                return None
            user = dict(row)
            del user["password_hash"]  # never return the hash
            # Split full_name into first_name and surname for the agent
            parts = user["full_name"].split(" ", 1)
            user["first_name"] = parts[0]
            user["surname"]    = parts[1] if len(parts) > 1 else ""
            return user


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    sql = "SELECT secret_question FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Return True if answer matches stored secret answer.
    Case-insensitive comparison — secret_answer stored as lowercase.
    """
    sql = "SELECT secret_answer FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row:
                return False
            return row[0] == answer.lower()


def update_password(email: str, new_password: str) -> bool:
    """
    Update user password with a new bcrypt hash.
    Returns True if the row was updated.
    """
    # Hash the new password before storing
    new_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    sql = """
        UPDATE users SET password_hash = %s
        WHERE email = %s
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (new_hash, email))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """Find the most relevant policy documents for a given query embedding."""
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """Insert a policy document with its embedding into the database."""
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]

# ── VECTOR / RAG QUERIES ──────────────────────────────────────────────

def search_policy(query: str, top_k: int = 3) -> list[dict]:
    """
    Search policy documents by semantic meaning (RAG).
    
    Converts user's natural language question to a vector,
    then finds most similar policy documents using cosine distance.
    
    Args:
        query: User's natural language question (e.g., "Can I get a refund?")
        top_k: Number of top results to return (default 3)
        
    Returns:
        List of relevant policy documents with similarity scores
        
    Example:
        >>> search_policy("Can I get a refund for a delayed train?")
        [
            {
                "title": "Delay Compensation Policy",
                "category": "refund",
                "content": "RF005: 30-59 minutes delay entitles to 50% refund...",
                "similarity": 0.89
            },
            {
                "title": "Booking Rules",
                "category": "booking",
                "content": "...",
                "similarity": 0.45
            }
        ]
    """
    from skeleton.llm_provider import embed_text
    
    # Step 1: Embed the user's question using the active LLM
    query_vector = embed_text(query)
    
    # Step 2: Build SQL for vector similarity search
    sql = \"\"\"\n        SELECT\n            title,\n            category,\n            content,\n            1 - (embedding <=> %s::vector) AS similarity\n        FROM policy_documents\n        WHERE 1 - (embedding <=> %s::vector) > %s\n        ORDER BY embedding <=> %s::vector\n        LIMIT %s\n    \"\"\"\n    \n    # Convert embedding list to vector string for PostgreSQL\n    vec_str = \"[\" + \",\".join(str(x) for x in query_vector) + \"]\"\n    \n    with _connect() as conn:\n        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))\n            return [dict(row) for row in cur.fetchall()]\n```
