# TransitFlow Database Design Document

**Author:** irischue1218-code  
**Date:** 2026-05-29  
**Branch:** feature/design-document  
**Project:** TransitFlow — Intelligent Rail Assistant  

---

## Table of Contents

1. [ER Diagram & Relational Schema](#1-er-diagram--relational-schema)
2. [PostgreSQL Database Design Rationale](#2-postgresql-database-design-rationale)
3. [Neo4j Graph Design & Routing Logic](#3-neo4j-graph-design--routing-logic)
4. [pgvector & RAG Implementation Strategy](#4-pgvector--rag-implementation-strategy)
5. [AI Tool Integration & Function Routing](#5-ai-tool-integration--function-routing)
6. [Reflection & Key Design Decisions](#6-reflection--key-design-decisions)

---

## 1. ER Diagram & Relational Schema

### 1.1 Conceptual ER Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRANSITFLOW ENTITY MODEL                     │
└─────────────────────────────────────────────────────────────────┘

                              USERS
                            ┌──────┐
                            │  PK: │ user_id (UUID)
                            │      │ email (UNIQUE)
                            │      │ password_hash
                            │      │ full_name
                            │      │ registered_at
                            └──────┘
                               ▲ │
                    ┌───────────┘ └──────────────────┐
                    │ 1                              │ 1
                    │                                │
            ┌───────┴──────────┐            ┌─────────────────────┐
            │                  │            │   NATIONAL_RAIL     │
            │            ┌──────────────┐  │    BOOKINGS         │
            │            │  PAYMENTS    │  ├─────────────────────┤
            │            ├──────────────┤  │  PK: booking_id     │
            │            │  PK: pay_id  │  │  FK: user_id        │
            │            │  FK: user_id │  │  FK: schedule_id    │
            │            │  amount_usd  │  │  travel_date        │
            │            │  status      │  │  fare_class         │
            │            │  paid_at     │  │  seat_number        │
            │            └──────────────┘  │  amount_usd         │
            │                              │  status             │
    ┌───────┴──────────┐                   │  booked_at          │
    │                  │                   └─────────────────────┘
    │            ┌──────────────┐               │
    │            │ METRO_TRIPS  │               │
    │            ├──────────────┤          ┌────┴──────────────┐
    │            │  PK: trip_id │          │                   │
    │            │  FK: user_id │    ┌───────────────┐   ┌──────────────┐
    │            │  travel_date │    │ SCHEDULES    │   │ STATIONS    │
    │            │  amount_usd  │    └───────────────┘   └──────────────┘
    │            └──────────────┘
    │
    └─→ (Soft delete: is_active = FALSE)


METRO_STATIONS                          NATIONAL_RAIL_STATIONS
├── station_id (PK)                     ├── station_id (PK)
├── name                                ├── name
├── is_interchange_metro                ├── is_interchange_national_rail
├── is_interchange_national_rail        ├── is_interchange_metro
└── interchange_national_rail_id (FK)   └── interchange_metro_id (FK)

METRO_SCHEDULES                         NATIONAL_RAIL_SCHEDULES
├── schedule_id (PK)                    ├── schedule_id (PK)
├── line                                ├── line
├── direction                           ├── service_type
├── origin_station_id (FK)              ├── origin_station_id (FK)
├── destination_station_id (FK)         ├── destination_station_id (FK)
├── frequency_min                       ├── frequency_min
└── stops (normalized)                  └── stops (normalized)

METRO_SCHEDULE_STOPS                    NATIONAL_RAIL_SCHEDULE_STOPS
├── schedule_id (FK/PK)                 ├── schedule_id (FK/PK)
├── station_id (FK/PK)                  ├── station_id (FK/PK)
├── stop_order                          ├── stop_order
└── travel_time_from_origin_min         ├── travel_time_from_origin_min
                                        └── is_passing_stop

NATIONAL_RAIL_SEAT_LAYOUTS              POLICY_DOCUMENTS
├── seat_id (PK)                        ├── id (PK)
├── schedule_id (FK/PK)                 ├── title
├── coach                               ├── category
└── fare_class                          ├── content
                                        ├── embedding (vector 768)
                                        └── source_file
```

### 1.2 Key Tables Overview

**USERS**
- Stores user profiles with bcrypt-hashed passwords
- `legacy_user_id` (e.g., RU01) maintained for seeding compatibility
- `is_active` implements soft delete (preserve booking history)

**NATIONAL_RAIL_STATIONS & METRO_STATIONS**
- Separate tables because they have different properties
- Metro has zones; national rail has regions
- `interchange_*_id` FKs capture network crossing points

**METRO_SCHEDULES vs NATIONAL_RAIL_SCHEDULES**
- Separate because operating patterns differ
- Metro: frequency-based (trains every N min)
- National rail: timetable-based with express/standard variants

**SCHEDULE_STOPS (Metro & National Rail)**
- Normalized junction table (3NF)
- Stop order uniquely identifies each stop within a schedule
- Avoids repeating schedule info for each stop

**NATIONAL_RAIL_SEAT_LAYOUTS**
- Maps seat positions to fare classes
- Used for availability checking and booking

**NATIONAL_RAIL_BOOKINGS**
- Represents confirmed seat reservations (can be cancelled)
- Has payment relationship (polymorphic via payments table)
- Status: confirmed, completed, cancelled

**METRO_TRIPS**
- Records actual metro journeys (pay-as-you-go)
- No seat assignment (turnstile-based entry)
- Different business logic from national rail bookings

**PAYMENTS**
- Unified table for both metro and national rail
- Polymorphic: `national_rail_booking_id` XOR `metro_trip_id` (never both)
- CHECK constraint enforces exactly one FK is non-null

**POLICY_DOCUMENTS**
- Stores refund policies, ticket types, booking rules, travel policies
- `embedding` is a 768-dimensional vector (Ollama) or 3072 (Gemini)
- Used for RAG semantic search (not keyword-based)

---

## 2. PostgreSQL Database Design Rationale

### 2.1 Why PostgreSQL for This Project

| Query Type | Why PostgreSQL | Example |
|------------|---|---|
| **Exact lookups by ID** | Indexes on PK/FK are lightning-fast | Find booking BK-XXXXX by UUID |
| **Date/time filtering** | Native DATE/TIME types with indexing | "What trains run on 2026-06-01?" |
| **Complex JOINs** | Query optimizer handles multi-table joins | Booking + User + Station + Schedule in one query |
| **Seat availability** | COUNT + GROUP BY + NOT IN subqueries | "How many seats left in coach A?" |
| **ACID transactions** | Prevents overbooking via locks | Book a seat atomically: check → reserve → debit |
| **Semantic search** | pgvector extension adds vector similarity | Find policies by meaning (not keywords) |

### 2.2 Design Decisions & Trade-offs

#### Why Two Station Tables (Not One Unified Table)

**❌ Option 1: One unified `stations` table**
```sql
CREATE TABLE stations (
  station_id VARCHAR(10),
  type VARCHAR(10),  -- 'metro' or 'rail'
  name VARCHAR(100),
  zone INTEGER,      -- NULL for national rail
  region VARCHAR(50) -- NULL for metro
);
```
Problems:
- Nullable columns violate good schema design
- Queries must always check `type` to disambiguate
- Schema doesn't self-document (type is implicit)
- Can't enforce zone only on metro stations

**✅ Option 2: Separate tables (chosen)**
```sql
CREATE TABLE metro_stations (
  station_id VARCHAR(10) PRIMARY KEY,
  name VARCHAR(100),
  zone INTEGER NOT NULL,
  ...
);

CREATE TABLE national_rail_stations (
  station_id VARCHAR(10) PRIMARY KEY,
  name VARCHAR(100),
  region VARCHAR(50),
  ...
);
```
Benefits:
- No null columns (cleaner)
- Type is explicit (MS01 always metro, NR01 always rail)
- Each table has domain-specific properties
- Foreign key constraints can be more specific
- Interchange relationship is explicit: `metro_stations.interchange_national_rail_id -> national_rail_stations`

**Trade-off:**
- Pro: Clarity and correctness
- Con: Some code duplication (both tables have similar structure)
- Verdict: **Clarity wins** — this is an educational project, not production code

---

#### Why Separate SCHEDULE & SCHEDULE_STOPS (Not Denormalized)

**❌ Denormalized approach:**
```sql
CREATE TABLE metro_schedules_denorm (
  schedule_id VARCHAR(20),
  line VARCHAR(10),
  -- All stops flattened into one row:
  stop1_station_id VARCHAR(10),
  stop1_arrival_time TIME,
  stop2_station_id VARCHAR(10),
  stop2_arrival_time TIME,
  ...
  stop10_station_id VARCHAR(10),
  stop10_arrival_time TIME
);
```
Problems:
- Unused columns (if a schedule has 5 stops, 5 columns are NULL)
- Repeats schedule info (line, direction) for every stop
- Hard to add more stops (schema change needed)
- Queries are complex (many OR conditions)

**✅ Normalized approach (chosen):**
```sql
CREATE TABLE metro_schedules (
  schedule_id VARCHAR(20) PRIMARY KEY,
  line VARCHAR(10),
  direction VARCHAR(20),
  ...
);

CREATE TABLE metro_schedule_stops (
  schedule_id VARCHAR(20) REFERENCES metro_schedules,
  station_id VARCHAR(10) REFERENCES metro_stations,
  stop_order INTEGER,
  PRIMARY KEY (schedule_id, station_id)
);
```
Benefits:
- One row per stop (no wasted columns)
- Schedule info stored once (single source of truth)
- Adding stops doesn't require schema changes
- Queries are cleaner: `WHERE schedule_id = ? AND station_id = ?`
- Indexes are more effective

**Trade-off:**
- Pro: Storage efficiency, query clarity, flexibility
- Con: Requires JOIN to get full schedule
- Verdict: **Normalization wins** — faster queries, clearer structure

---

#### Why Separate METRO_TRIPS & NATIONAL_RAIL_BOOKINGS (Not One Table)

**Business logic is fundamentally different:**

| Aspect | Metro | National Rail |
|--------|-------|---------------|
| **Business model** | Pay-as-you-go (turnstile) | Pre-booking required |
| **Seats** | No assigned seats | Assigned seats |
| **Cancellation** | N/A (already travelled) | Can cancel before travel → refund |
| **Payment timing** | After travel | Before/at travel |
| **Data fields** | trip_id, travel_date, amount | booking_id, seat_id, coach, status |

**❌ Combined table:**
```sql
CREATE TABLE all_trips (
  trip_id UUID PRIMARY KEY,
  user_id UUID,
  seat_id VARCHAR(10),     -- NULL for metro
  coach VARCHAR(5),         -- NULL for metro
  cancellation_date DATE,   -- NULL for metro
  refund_amount DECIMAL,    -- NULL for metro
  ...
);
```
Problems:
- Many nullable columns
- Constraints can't be enforced (seat_id should be required for rail, forbidden for metro)
- Business logic has to check type everywhere
- Can't prevent invalid states (e.g., cancelling a metro trip)

**✅ Separate tables (chosen):**
```sql
CREATE TABLE national_rail_bookings (
  booking_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  seat_id VARCHAR(10) NOT NULL,
  coach VARCHAR(5) NOT NULL,
  status VARCHAR(20) NOT NULL,
  ...
);

CREATE TABLE metro_trips (
  trip_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  -- No seat fields
  status VARCHAR(20) NOT NULL,
  ...
);
```
Benefits:
- No unnecessary nulls
- Constraints enforce valid states
- Queries can be specific to each type
- Code doesn't need conditional logic

**Trade-off:**
- Pro: Correctness, clarity, constraint enforcement
- Con: Similar structure (some duplication)
- Verdict: **Separation wins** — prevents invalid states

---

### 2.3 Critical Indexes

```sql
-- User lookups (auth, session management)
CREATE INDEX idx_users_email ON users(email);

-- Booking history (most common query)
CREATE INDEX idx_bookings_user_id ON national_rail_bookings(user_id);

-- Availability checking (high-traffic query)
CREATE INDEX idx_bookings_travel_date ON national_rail_bookings(travel_date);

-- Metro trip history
CREATE INDEX idx_metro_trips_user_id ON metro_trips(user_id);

-- Payment lookups
CREATE INDEX idx_payments_user_id ON payments(user_id);

-- Policy search by category (for RAG filtering)
CREATE INDEX idx_policy_docs_category ON policy_documents(category);

-- Vector similarity search (pgvector HNSW index)
CREATE INDEX idx_policy_docs_embedding ON policy_documents 
  USING HNSW (embedding vector_cosine_ops);
```

**Why these indexes?**
- `users.email`: Authentication is on the critical path
- `bookings.user_id`: "Show my bookings" is the most frequent query
- `bookings.travel_date`: Seat availability check must be fast
- `policy_documents_embedding`: Vector search must use HNSW for efficiency

---

### 2.4 Seeding Dependency Order

```
1. national_rail_stations    ← no dependencies
2. metro_stations            ← FK to national_rail_stations (interchange)
3. metro_schedules           ← FK to metro_stations
4. metro_schedule_stops      ← FK to metro_schedules + metro_stations
5. national_rail_schedules   ← FK to national_rail_stations
6. national_rail_fare_classes← FK to national_rail_schedules
7. national_rail_schedule_stops ← FK to national_rail_schedules + stations
8. national_rail_seat_layouts ← FK to national_rail_schedules
9. users                     ← no dependencies
10. national_rail_bookings   ← FK to users, schedules, stations
11. metro_trips              ← FK to users, schedules, stations
12. payments                 ← FK to users, bookings/trips
13. feedback                 ← FK to users, bookings/trips (optional)
14. policy_documents         ← no dependencies (seeded separately by seed_vectors.py)
```

**Why this order?**
- Parent tables before child tables (prevent FK violations)
- Independent tables first (users, stations)
- Complex tables last (payments depends on both booking types)

---

## 3. Neo4j Graph Design & Routing Logic

### 3.1 Graph Model Overview

**Why a graph database for routing?**

SQL routing queries are nightmarishly complex:

```sql
-- Find shortest path A → B in SQL (RECURSIVE CTE)
WITH RECURSIVE paths AS (
  -- Base case: direct connections
  SELECT from_station, to_station, travel_time, 1 AS hops, 
         ARRAY[from_station, to_station] AS path
  FROM metro_links
  
  UNION ALL
  
  -- Recursive case: extend paths
  SELECT p.from_station, ml.to_station, 
         p.travel_time + ml.travel_time,
         p.hops + 1,
         p.path || ml.to_station
  FROM paths p
  JOIN metro_links ml ON p.to_station = ml.from_station
  WHERE p.hops < 10 AND NOT ml.to_station = ANY(p.path)  -- avoid cycles
)
SELECT * FROM paths 
WHERE from_station = 'MS01' AND to_station = 'MS09'
ORDER BY travel_time LIMIT 1;
```

In Neo4j, the same query is three lines:

```cypher
MATCH path = shortestPath(
  (start:MetroStation {station_id: 'MS01'})
  -[:METRO_LINK*..10]->
  (end:MetroStation {station_id: 'MS09'})
)
RETURN path;
```

### 3.2 Graph Schema

**Node Types:**

```cypher
-- Metro Station Node
node_label: MetroStation
properties: {
  station_id: "MS01",      -- primary key (human-readable)
  name: "Central Square",
  zone: 1,
  latitude: 51.5074,
  longitude: -0.1278,
  lines: ["M1", "M3"]      -- array of line identifiers
}

-- National Rail Station Node
node_label: NationalRailStation
properties: {
  station_id: "NR01",
  name: "Central Station",
  region: "London",
  operator: "UK National Rail",
  latitude: 51.5050,
  longitude: -0.1200
}
```

**Relationship Types:**

```cypher
-- Metro Link (directed, unidirectional per direction)
type: METRO_LINK
from: MetroStation
to: MetroStation
properties: {
  line: "M1",              -- which metro line
  travel_time_min: 5,      -- time between stations
  distance_km: 2.1
}

-- National Rail Link
type: RAIL_LINK
from: NationalRailStation
to: NationalRailStation
properties: {
  line: "NR1",
  travel_time_min: 45,
  distance_km: 28.5,
  express: false          -- TRUE if express service skips intermediate stops
}

-- Interchange Link (bidirectional)
type: INTERCHANGE_TO
from: MetroStation or NationalRailStation
to: NationalRailStation or MetroStation
properties: {
  transfer_time_min: 5,
  walking_distance_m: 400
}
```

### 3.3 Routing Query Examples

**Query 1: Fastest Metro Route**

```cypher
MATCH path = shortestPath(
  (start:MetroStation {station_id: $from_id})
  -[:METRO_LINK*..10]->
  (end:MetroStation {station_id: $to_id})
)
WITH path, 
     reduce(time = 0, rel IN relationships(path) | time + rel.travel_time_min) AS total_time
RETURN {
  found: true,
  stations: [node IN nodes(path) | {id: node.station_id, name: node.name}],
  total_time_min: total_time,
  legs: [rel IN relationships(path) | {line: rel.line, time: rel.travel_time_min}]
};
```

**Query 2: Cross-Network Interchange Path**

```cypher
MATCH 
  -- Metro segment
  metro_path = shortestPath(
    (start:MetroStation {station_id: $from_id})
    -[:METRO_LINK*..5]->
    (metro_station:MetroStation)
  ),
  -- Transfer to national rail
  (metro_station)-[interchange:INTERCHANGE_TO]->
    (rail_station:NationalRailStation),
  -- Rail segment
  rail_path = shortestPath(
    (rail_station)
    -[:RAIL_LINK*..5]->
    (end:NationalRailStation {station_id: $to_id})
  )
WITH metro_path, rail_path, interchange,
     reduce(t=0, r IN relationships(metro_path) | t + r.travel_time_min) AS metro_time,
     reduce(t=0, r IN relationships(rail_path) | t + r.travel_time_min) AS rail_time
RETURN {
  found: true,
  metro_leg: [n IN nodes(metro_path) | {id: n.station_id, name: n.name}],
  interchange_point: {
    from: metro_station.name,
    to: rail_station.name,
    transfer_time_min: interchange.transfer_time_min
  },
  rail_leg: [n IN nodes(rail_path) | {id: n.station_id, name: n.name}],
  total_time_min: metro_time + interchange.transfer_time_min + rail_time
};
```

**Query 3: Alternative Routes (Avoiding a Station)**

```cypher
MATCH path = shortestPath(
  (start:MetroStation {station_id: $from_id})
  -[:METRO_LINK*..10]->
  (end:MetroStation {station_id: $to_id})
  WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
)
WITH path,
     reduce(time = 0, rel IN relationships(path) | time + rel.travel_time_min) AS total_time
RETURN {
  stations: [node IN nodes(path) | {id: node.station_id, name: node.name}],
  total_time_min: total_time
}
ORDER BY total_time
LIMIT 3;
```

### 3.4 Why Graph > SQL for Routing

| Aspect | SQL | Neo4j |
|--------|-----|-------|
| **Shortest path query length** | 30+ lines (recursive CTE) | 3 lines |
| **Performance** | O(n²) at worst (Cartesian joins) | O(n log n) (graph traversal) |
| **Avoiding a station** | Rewrite entire query | Add WHERE clause |
| **Finding all paths** | Multiple UNIONs | Simple relationship traversal |
| **Visualization** | Need custom code | Native graph UI |
| **Intuitive?** | No (recursive logic is hard) | Yes (matches real-world networks) |

---

## 4. pgvector & RAG Implementation Strategy

### 4.1 What is RAG?

**RAG = Retrieval-Augmented Generation**

The problem it solves:
```
User: "What's your refund policy for delays?"

❌ Without RAG (naive LLM):
  LLM generates answer from training data
  → Potentially outdated
  → Could be confidently wrong
  → No source of truth

✅ With RAG (LLM + database):
  1. RETRIEVE: Find relevant policies from database
  2. AUGMENT: Pass those policies to LLM as context
  3. GENERATE: LLM composes answer based on actual policies
  → Guaranteed accurate
  → Source of truth is your database
```

### 4.2 How Vector Search Works

**Step 1: Embedding (Converting Text to Numbers)**

```
Text: "Passengers experiencing delays of 30-59 minutes..."
         ↓
    [Embedding Model]
         ↓
Vector: [0.234, -0.891, 0.567, ..., 0.123]  (768 dimensions)
         └─ Each dimension captures some semantic meaning
         └─ Similar texts → similar vectors
         └─ Different texts → different vectors
```

**Step 2: Similarity Search (Finding Close Vectors)**

```
User question: "Can I get a refund for a 45-minute delay?"
         ↓
    [Same Embedding Model]
         ↓
Query vector: [0.245, -0.885, 0.572, ..., 0.118]
         ↓
    [Cosine Distance]
         ↓
Policy 1: 0.89 similarity ← DELAY COMPENSATION (HIGH MATCH)
Policy 2: 0.45 similarity ← LUGGAGE POLICY
Policy 3: 0.12 similarity ← ACCESSIBILITY
         ↓
    [Top K results]
         ↓
Return top 3 policies with highest similarity
```

### 4.3 pgvector Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE policy_documents (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,              -- "Delay Compensation Policy"
    category VARCHAR(50) NOT NULL,            -- "refund", "ticket", "travel"
    content TEXT NOT NULL,                    -- Full policy text
    embedding vector(768),                    -- Ollama: 768 dims
                                              -- Gemini: 3072 dims
    source_file VARCHAR(200),                 -- "refund_policy.json"
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast similarity search
-- (Hierarchical Navigable Small World)
CREATE INDEX idx_policy_embedding ON policy_documents
    USING HNSW (embedding vector_cosine_ops);
```

### 4.4 Seeding Vectors (skeleton/seed_vectors.py)

```python
def seed_vectors():
    """
    Load policy JSONs, embed them, insert into pgvector
    """
    from llm_provider import embed_text  # Uses Ollama or Gemini
    
    for policy_file in ["refund_policy.json", "ticket_types.json", ...]:
        data = load_json(f"train-mock-data/{policy_file}")
        
        for entry in data:
            title = entry["title"]
            content = entry["content"]
            
            # Convert text to vector (768 or 3072 dimensions)
            embedding = embed_text(content)
            
            # Insert into database
            cursor.execute("""
                INSERT INTO policy_documents (title, category, content, embedding, source_file)
                VALUES (%s, %s, %s, %s::vector, %s)
            """, (title, policy_file.replace(".json", ""), content, 
                   f"[{','.join(map(str, embedding))}]", policy_file))
    
    db.commit()
```

### 4.5 Query-Time Search

```python
def search_policy(query: str) -> list[dict]:
    """
    User asks a policy question → find relevant documents
    """
    # Step 1: Embed the user's question
    query_vector = embed_text(query)
    
    # Step 2: Search database for similar vectors
    sql = """
        SELECT 
            id,
            title,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > 0.5  -- Threshold
        ORDER BY embedding <=> %s::vector  -- Cosine distance
        LIMIT 3
    """
    
    vec_str = f"[{','.join(map(str, query_vector))}]"
    cur.execute(sql, (vec_str, vec_str, vec_str))
    
    return [{
        "title": row[1],
        "content": row[2],
        "similarity": row[3]
    } for row in cur.fetchall()]
```

### 4.6 Example: Delay Compensation RAG

```
User types: "I was delayed 45 minutes. Can I get compensation?"
                          ↓
                    [Embed question]
                          ↓
         query_vector = [0.234, -0.567, ...]
                          ↓
         [Search policy_documents by similarity]
                          ↓
         Similarity scores:
         - Policy RF005: 0.91 ← DELAY COMPENSATION (MATCH!)
         - Policy T001:  0.34 ← TICKET TYPES
         - Policy B002:  0.18 ← BOOKING RULES
                          ↓
         [Return top 3]
                          ↓
Context for LLM:
"""Policy RF005: Passengers experiencing delays of 30–59 minutes 
are entitled to a 50% refund. Delays of 60+ minutes entitle 
passengers to 100% refund or rebooking."""
                          ↓
         [LLM composes answer]
                          ↓
Final answer: "Based on our Delay Compensation Policy, a 45-minute 
delay entitles you to a 50% refund of your fare."
```

### 4.7 Why pgvector Over Full-Text Search

```sql
-- Full-text search (keyword-based) ❌
SELECT * FROM policies 
WHERE content LIKE '%compensation%' OR content LIKE '%refund%'
-- Problem: Misses if policy says "money back" instead of "refund"

-- pgvector (semantic search) ✅
SELECT * FROM policies 
ORDER BY embedding <=> query_embedding
LIMIT 3
-- Handles: "money back", "compensation", "reimbursement" as synonyms
```

---

## 5. AI Tool Integration & Function Routing

### 5.1 Complete Tool Registry

All these tools are defined in `skeleton/agent.py`:

| Tool Name | Database | Purpose | Example Trigger |
|-----------|----------|---------|------------------|
| `check_national_rail_availability` | PostgreSQL | List trains between stations | "Are there trains from NR01 to NR05 today?" |
| `query_metro_schedules` | PostgreSQL | Find metro routes and fares | "What metro lines go from MS01 to MS09?" |
| `query_available_seats` | PostgreSQL | Check seat availability | "How many first-class seats are left?" |
| `query_user_bookings` | PostgreSQL | Get booking history (auth) | "Show my bookings" |
| `execute_booking` | PostgreSQL | Create new booking (auth) | "Book me a standard ticket..." |
| `execute_cancellation` | PostgreSQL | Cancel booking + calculate refund (auth) | "Cancel booking BK-XXXXX" |
| `search_policy` | PostgreSQL (pgvector) | RAG search for policies | "What's your refund policy?" |
| `query_shortest_route` | Neo4j | Fastest path (same network) | "Fastest metro route?" |
| `query_interchange_path` | Neo4j | Cross-network path | "How do I get from metro to national rail?" |
| `query_alternative_routes` | Neo4j | Routes avoiding a station | "Any routes avoiding NR03?" |

### 5.2 Tool Definition Format

Each tool has a schema that the LLM reads:

```python
TOOLS = [
    {
        "name": "check_national_rail_availability",
        "description": (
            "Check which national rail trains run between two stations on a specific date. "
            "Returns list of available schedules with times, seat counts, and fares. "
            "Use when user asks about train availability or timetables."
        ),
        "parameters": {
            "origin_id": {
                "type": "string",
                "description": "Origin station ID (e.g., 'NR01' for Central Station)"
            },
            "destination_id": {
                "type": "string",
                "description": "Destination station ID (e.g., 'NR05' for Stonehaven)"
            },
            "travel_date": {
                "type": "string",
                "description": "Travel date in YYYY-MM-DD format (e.g., '2026-06-01')"
            }
        },
        "required": ["origin_id", "destination_id"],  # travel_date is optional (defaults to today)
    },
    # ... more tools ...
]
```

### 5.3 Tool Routing: How LLM Decides Which Tool to Call

**Ollama (Local Model)**
```python
def ollama_tool_call(question: str):
    system_prompt = """
    You are a transit assistant. You have these tools:
    
    Train availability: check_national_rail_availability(origin, destination, date?)
    → Use when user asks about specific train services
    
    Metro routes: query_metro_schedules(origin, destination)
    → Use when user asks about metro options
    
    Refund policy: search_policy(query)
    → Use when user asks about policies, compensation, refunds
    
    My bookings: query_user_bookings()
    → Use when user asks "show my bookings" or "my trips"
    
    Route finding: query_shortest_route(from, to)
    → Use when user asks for fastest path
    """
    # Ollama is less reliable at tool calling, so we add explicit hints
    response = ollama.generate(prompt=question, system=system_prompt)
    # Parse JSON tool calls from response
    return parse_tool_calls(response)
```

**Gemini (Google API)**
```python
def gemini_tool_call(question: str):
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=question,
        tools=[{
            "function_declarations": TOOLS  # Use the TOOLS list directly
        }]
    )
    # Gemini natively returns tool_calls
    return response.tool_calls
```

### 5.4 Complete Call Flow

```
User types: "What's the fastest metro route from MS01 to MS09?"
                                    ↓
                          [1] skeleton/ui.py
                          ↓ (passes to agent)
                          
                 [2] skeleton/agent.py (run_agent)
                 ├─ LLM reads question + TOOLS list
                 ├─ LLM selects: query_shortest_route(MS01, MS09, "metro")
                 └─ Extract params: {origin_id: "MS01", destination_id: "MS09", network: "metro"}
                                    ↓
                 [3] skeleton/agent.py (_execute_tool)
                 ├─ if tool_name == "query_shortest_route":
                 ├─ result = query_shortest_route(**params)
                 └─ Calls function from databases/graph/queries.py
                                    ↓
                [4] databases/graph/queries.py
                ├─ Connects to Neo4j
                ├─ Runs Cypher query:
                │  MATCH path = shortestPath(
                │    (a:MetroStation {id: 'MS01'})
                │    -[:METRO_LINK*..10]->
                │    (b:MetroStation {id: 'MS09'})
                │  )
                │  RETURN path
                ├─ Gets: [{stations: [MS01, MS02, MS05, MS09], time_min: 15}]
                └─ Returns JSON
                                    ↓
                [5] skeleton/agent.py (_normalise_result)
                ├─ Flattens JSON to readable text:
                │  [query_shortest_route]
                │  stations:
                │    [0] id: MS01, name: Central Square
                │    [1] id: MS02, name: North Park
                │    [2] id: MS05, name: South Terminal
                │    [3] id: MS09, name: East Gate
                │  total_time_min: 15
                └─ Returns formatted text
                                    ↓
                [6] skeleton/agent.py (LLM composes answer)
                ├─ LLM reads:
                │  Question: "What's fastest route MS01 to MS09?"
                │  Data:
                │  [query_shortest_route]
                │  stations: [MS01, MS02, MS05, MS09]
                │  total_time_min: 15
                └─ Generates: "The fastest metro route from Central Square to East 
                   Gate takes 15 minutes. The route is: Central Square → North Park 
                   → South Terminal → East Gate."
                                    ↓
                [7] skeleton/ui.py
                └─ Displays answer to user
```

### 5.5 Authentication-Aware Tools

Some tools require the user to be logged in:

```python
def query_user_bookings(user_email: str) -> dict:
    """
    Only works if user_email is provided (from login state)
    """
    # If user is not logged in, user_email is None
    if not user_email:
        return {"error": "Please log in to view your bookings"}
    
    # ... query database ...
    return {"bookings": [...]}
```

In `skeleton/agent.py`, login context is injected:

```python
if current_user_email:
    system_prompt += f"""
    The user is logged in as: {current_user_email}
    Full name: {user["first_name"]} {user["surname"]}
    
    Auth-gated tools are now available:
    - query_user_bookings() — show this user's booking history
    - execute_booking() — create a booking for this user
    - execute_cancellation() — cancel a booking
    """
else:
    system_prompt += """
    User is NOT logged in.
    Auth-gated tools are NOT available.
    """
```

---

## 6. Reflection & Key Design Decisions

### 6.1 Why Three Databases (Not One)

```
Hypothetical Scenario: "Use only PostgreSQL"

Problem 1: Route Finding
  Query: "Shortest path from NR01 to NR05"
  Solution in SQL: Recursive CTE (30+ lines, O(n²) performance)
  Solution in Neo4j: shortestPath() (3 lines, O(n log n) performance)
  Winner: Neo4j (10x simpler, 10x faster)

Problem 2: Policy Search
  Query: "Refund policy for delays"
  Solution with SQL LIKE: %delay% — misses if policy says "late trains"
  Solution with pgvector: Semantic matching — understands synonyms
  Winner: pgvector (actually understands meaning)

Problem 3: Seat Availability
  Query: "How many seats left on train NR_SCH01 today?"
  Solution: SELECT COUNT(*) ... WHERE status != 'cancelled'
  All databases: Equal (simple COUNT query)

Conclusion:
  ✅ PostgreSQL: Booking, seat management, user auth
  ✅ Neo4j: Route finding, network topology
  ✅ pgvector: Policy search by meaning
  ❌ Single database: Forces bad design + poor performance
```

### 6.2 Critical Design Trade-offs

#### Trade-off 1: Two Station Tables vs One Unified Table

**Decision: Two separate tables**

```
Pro:
  ✓ No nullable columns (zone only on metro, region only on rail)
  ✓ Type is explicit (MS01 always metro, never ambiguous)
  ✓ Constraints enforce domain validity
  ✓ Interchange relationship is clear
  
Con:
  ✗ Some code duplication (both have station_id, name, coords)
  ✗ Queries must UNION to get all stations
  
Why we chose it:
  → Clarity >> code reuse
  → This is education, not production
  → Prevents invalid states (e.g., assigning zone to rail station)
```

#### Trade-off 2: Normalized Schedules vs Denormalized

**Decision: Normalized (schedule + schedule_stops)**

```
Pro:
  ✓ Schedule info stored once (single source of truth)
  ✓ No wasted NULL columns
  ✓ Adding stops doesn't require schema changes
  ✓ Cleaner queries: WHERE schedule_id = ? AND station_id = ?
  ✓ Better indexing
  
Con:
  ✗ Requires JOIN to get full schedule
  ✗ Slightly more code to load data
  
Why we chose it:
  → Storage + query efficiency >> minor inconvenience
  → Industry standard (3NF)
```

#### Trade-off 3: Unified Payments Table vs Separate

**Decision: Unified with polymorphic FKs**

```
Options:
  A) One payments table, separate booking/trip tables
  B) Two payment tables (one per transaction type)
  
Choose A if:
  → Need "show all spending by user" without UNION
  → Schema simplicity matters
  → Can enforce constraint in code
  
Choose B if:
  → Each type needs very different fields
  → Avoid nullable columns at all costs
  
We chose A because:
  → Both bookings and metro trips have similar payment fields
  → One index on user_id is faster than two
  → CHECK constraint enforces valid states
```

### 6.3 Scaling & Future Extensions

**If Data Volume Grows (Millions of Bookings)**

```sql
-- Partitioning by date (reduces query scan)
CREATE TABLE bookings_2026_05 PARTITION OF national_rail_bookings
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- Vertical sharding: old bookings → archive table
CREATE TABLE bookings_archive (
  -- Same schema, old data only
);
```

**If Real-Time Disruptions Needed**

```sql
CREATE TABLE disruptions (
  disruption_id UUID PRIMARY KEY,
  schedule_id VARCHAR(20) FK,
  station_id VARCHAR(10) FK,
  delay_min INTEGER,
  reason TEXT,
  created_at TIMESTAMP
);

-- Neo4j can model this as:
MATCH (a)-[r:METRO_LINK]-(b)
SET r.current_delay_min = {fetch from disruptions}
```

**If Machine Learning Needed**

```python
# Add prediction column to bookings
ALTER TABLE national_rail_bookings 
ADD COLUMN predicted_cancellation_prob FLOAT;

# ML pipeline fills it:
model = load_model("booking_cancellation_predictor")
for booking in get_recent_bookings():
  prob = model.predict(booking.features)
  save(prob)

# Business logic uses it:
if booking.predicted_cancellation_prob > 0.7:
  send_confirmation_email()  # Higher risk = more touchpoints
```

### 6.4 Why This Structure Differs from Production

| Aspect | This Project | Production |
|--------|-------------|------------|
| **Code organization** | By database type (`/relational`, `/graph`) | By business domain (`/bookings`, `/routes`) |
| **Schema changes** | Edit `schema.sql`, reset DB | Migration files (Alembic), zero-downtime |
| **Testing** | Manual (you type queries) | Automated (unit + integration tests in CI) |
| **Config** | One `.env` file | Separate configs per environment (dev/staging/prod) |
| **Secrets** | In `.env` (never commit) | AWS Secrets Manager / HashiCorp Vault |
| **Deployment** | Run locally | Docker images → Kubernetes → load balancer |
| **Monitoring** | Print statements | Prometheus metrics → Grafana dashboards |

**Why this project is simpler:**
→ Teaching database design, not DevOps
→ Manual testing is OK for learning
→ Single `.env` keeps focus on data layer

### 6.5 What You Learn Here

1. **When to use each database type** (not a technical choice, a business choice)
2. **Relational design principles** (normalization, FK relationships, indexing)
3. **Graph thinking** (representing problems as networks)
4. **Vector embeddings** (semantic search, RAG)
5. **System integration** (multiple databases working together)
6. **Trade-off thinking** (choosing clarity over elegance)

### 6.6 Key Insights

```
✅ The right tool for the right job
  "Why one database?" is the wrong question
  "Which database for this job?" is the right question

✅ Constraints are features
  NOT NULL columns force valid states
  Foreign keys prevent orphan records
  CHECKs make rules explicit

✅ Normalization matters
  Even with tiny datasets
  Storage + query clarity compound

✅ Design before coding
  This document exists BEFORE queries.py
  Team B can start work immediately
  No guessing about intended behavior

✅ Documentation is communication
  Your design doc is a contract with your team
  Future you will thank present you
```

---

## Conclusion

TransitFlow's three-database architecture demonstrates **polyglot persistence**: using multiple database technologies to solve different problems optimally. 

The relational database manages structured transactional data reliably. The graph database makes route-finding natural and efficient. The vector database enables semantic search without keyword matching.

This design is neither over-engineered nor under-engineered—it's right-sized for the problem. When you encounter systems needing similar multi-database approaches, remember:

**Database choice is a business decision, not a technical one. Choose based on what questions you need to answer.**

---

**Document Version:** 1.0  
**Last Updated:** 2026-05-29  
**Status:** Ready for Team Review
