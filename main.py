"""
SituSim - decision-making trainer for degraded communication environments
SIH 2026, Problem Statement 26248 (MoD / Defence Services Staff College)

How to run:
    pip install -r requirements.txt
    python main.py
Then open http://localhost:8000 in a browser.

How the code is laid out (top to bottom):
    1. Settings
    2. Database (an append-only event log in SQLite)
    3. Exercise state (plain global variables)
    4. Logging and small helpers
    5. The communication engine: delay, loss, link outages and the four channels
       (radio, relay, runner, flare)
    6. Detectability: every radio message raises it; enemy direction finding
       can locate a role that talks too much
    7. Scenario injects, decision points, freeze probes, reads and source grades
    8. The Fog Director (optional automatic difficulty)
    9. What each trainee is allowed to see
   10. What the instructor sees
   11. The tick loop (the simulation clock)
   12. Web pages, WebSockets and the instructor API
"""

import asyncio
import base64
import json
import os
import queue
import random
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import aar
import shadow


# ============================================================
# 1. SETTINGS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCENARIO_FILE = os.path.join(BASE_DIR, "scenarios", "grey_lantern.json")
# On a host like Render, DB_PATH can point at a persistent disk so reviews survive restarts.
DB_FILE = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "situsim.db"))
if os.path.dirname(DB_FILE) != "":
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
PORT = int(os.environ.get("PORT", "8000"))
# When deployed, the public address people should open (Render sets RENDER_EXTERNAL_URL itself).
PUBLIC_URL = os.environ.get("PUBLIC_URL", os.environ.get("RENDER_EXTERNAL_URL", ""))
# When deployed: a hosted Postgres database that keeps a copy of the event log, so reviews
# survive restarts on hosts that wipe local files (see section 2b). Empty = laptop mode.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# When deployed: the passcode for the instructor console. Empty = no passcode (laptop mode).
INSTRUCTOR_PASSCODE = os.environ.get("INSTRUCTOR_PASSCODE", "")

ROLES = ["GTL", "ISR", "EW", "COMMS"]

# How foggy the network is. The difficulty presets and the Fog Director both use these.
FOG_LEVELS = [
    {"name": "Clear", "base_delay": 2, "random_delay": 6, "drop_chance": 0.0, "conflict_chance": 0.0},
    {"name": "Light fog", "base_delay": 4, "random_delay": 12, "drop_chance": 0.05, "conflict_chance": 0.10},
    {"name": "Normal fog", "base_delay": 5, "random_delay": 20, "drop_chance": 0.10, "conflict_chance": 0.15},
    {"name": "Heavy fog", "base_delay": 8, "random_delay": 30, "drop_chance": 0.18, "conflict_chance": 0.25},
    {"name": "Dense fog", "base_delay": 12, "random_delay": 40, "drop_chance": 0.28, "conflict_chance": 0.35},
]
PRESETS = {"EASY": 1, "NORMAL": 2, "HARD": 4}

# How much each kind of radio transmission raises the sender's detectability.
EMISSION_COST = {
    "chat": 12,       # plus 1 for every 12 characters
    "forward": 10,
    "read": 8,
    "ack": 4,
    "relay": 8,       # charged to the Comms Coordinator, whose station transmits it
    "flare": 25,      # a flare is seen by everyone, including the other side
}

# Everything the instructor can change. All times are in simulation seconds.
config = {
    "seed": 42,                    # same seed + same actions = same delays and losses
    "difficulty": "NORMAL",
    "fog_level": 2,
    "base_delay": 5,               # every radio message takes at least this long
    "random_delay": 20,            # plus a random 0..random_delay seconds
    "drop_chance": 0.10,           # chance that a radio message is lost
    "conflict_chance": 0.15,       # chance the engine adds a contradicting report
    "delayed_label_after": 20,     # a report older than this on arrival is shown as "Delayed"
    "stale_after": 120,            # a report older than this at decision time counts as stale
    "relay_delay": 20,             # the relay gets past jamming but is slower
    "runner_delay": 120,           # a runner always arrives, but slowly
    "flare_delay": 2,
    "share_window": 90,            # how quickly critical reports should be passed on
    "duration": 900,               # length of the exercise
    "speed": 1,                    # 1 = real time, 2 = twice as fast, and so on
    "show_degraded_to_trainee": False,  # partial jamming is often not obvious, so hide it by default
    "located_threshold": 90,       # detectability at which direction finding locates a role
    "located_penalty": 60,         # a located role relocates and is off the net this long
    "emission_decay": 0.3,         # detectability fades by this much every second
    "director": False,             # the Fog Director adjusts the fog by itself
}


# ============================================================
# 2. DATABASE - an append-only event log
# ============================================================
# Every single thing that happens is written to the "events" table and never
# changed afterwards. The After-Action Review is rebuilt only from this table,
# so it can be audited.

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session TEXT,
        sim_time INTEGER,
        wall_time REAL,
        kind TEXT,
        role TEXT,
        data TEXT
    )
""")
db.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        scenario TEXT,
        created REAL,
        status TEXT
    )
""")
db.commit()


# ============================================================
# 2b. OPTIONAL COPY OF THE EVENT LOG IN POSTGRES
# ============================================================
# Free hosting wipes local files whenever the server restarts. When DATABASE_URL
# is set, every write to the local file is also sent to a hosted Postgres
# database, and on start-up whatever Postgres has is copied back into the local
# file. Everything else (the review, the replay) keeps reading the local file.
# The copy runs in a background thread, so a slow network never holds up the
# exercise clock. Without DATABASE_URL none of this runs.

backup_queue = queue.Queue()


def backup(sql, params):
    """Queue one statement for the Postgres copy. Postgres uses %s instead of ?."""
    if DATABASE_URL != "":
        backup_queue.put((sql.replace("?", "%s"), params))


def backup_worker():
    import psycopg
    connection = None
    while True:
        item = backup_queue.get()
        attempts = 0
        while attempts < 20:
            try:
                if connection is None or connection.closed:
                    connection = psycopg.connect(DATABASE_URL, autocommit=True)
                connection.execute(item[0], item[1])
                break
            except Exception as error:
                attempts = attempts + 1
                print("Copy to Postgres failed (" + str(attempts) + "), retrying in 3 s:", repr(error)[:200])
                connection = None
                time.sleep(3)


def restore_from_postgres():
    """Create the Postgres tables if needed, then copy every saved event and session into the local file."""
    import psycopg
    connection = psycopg.connect(DATABASE_URL, autocommit=True)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id BIGINT PRIMARY KEY,
            session TEXT,
            sim_time INTEGER,
            wall_time DOUBLE PRECISION,
            kind TEXT,
            role TEXT,
            data TEXT
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            scenario TEXT,
            created DOUBLE PRECISION,
            status TEXT
        )
    """)
    newest_local = db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    rows = connection.execute("SELECT id, session, sim_time, wall_time, kind, role, data FROM events "
                              "WHERE id > %s ORDER BY id", (newest_local,)).fetchall()
    for row in rows:
        db.execute("INSERT OR IGNORE INTO events (id, session, sim_time, wall_time, kind, role, data) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(row))
    sessions = connection.execute("SELECT session_id, scenario, created, status FROM sessions").fetchall()
    for row in sessions:
        db.execute("INSERT OR IGNORE INTO sessions (session_id, scenario, created, status) VALUES (?, ?, ?, ?)",
                   tuple(row))
    db.commit()
    connection.close()
    print("  Restored " + str(len(rows)) + " events and " + str(len(sessions)) + " sessions from Postgres.")


if DATABASE_URL != "":
    try:
        restore_from_postgres()
    except Exception as error:
        print("  Could not reach the Postgres database, so reviews will not be kept:", repr(error)[:300])
    threading.Thread(target=backup_worker, daemon=True).start()


# ============================================================
# 3. EXERCISE STATE (plain global variables)
# ============================================================
# Only one exercise runs at a time. reset_exercise() fills these in.

scenario = {}
session_id = ""
status = "READY"            # READY, RUNNING, PAUSED, FROZEN (during a probe), ENDED
sim_time = 0
links = {}                  # role -> "UP", "DEGRADED" or "DOWN"
link_until = {}             # role -> time a timed outage ends by itself
located_until = {}          # role -> time a located role is back on the net
sensors = {}                # sensor name -> "ONLINE" or "OFFLINE"
sensor_until = {}           # sensor name -> time it comes back online
reports = {}                # report_id -> report
deliveries = []             # one entry for every copy of a message going to one role
decision_points = {}        # dp_id -> decision point
decisions = []              # every decision submitted (or missed)
beliefs = {}                # role -> {q_id: {"value": 0-100, "time": t}}
grades = {}                 # role -> {report_id: {"reliability": "B", "credibility": "2", "time": t}}
exposure = {}               # role -> detectability, 0 and up
df_active = False           # is enemy direction finding active?
director_history = []       # what the Fog Director did and why
fired = set()               # which scripted injects have already run
probe = None                # the freeze probe that is running right now, if any
status_before_probe = "RUNNING"
recent_log = []             # the last few events, for the instructor screen
delivery_counter = 0
chat_counter = 0
instructor_dp_counter = 0
rng_use_count = {}
variant_count = {}

role_sockets = {"GTL": [], "ISR": [], "EW": [], "COMMS": []}
role_names = {"GTL": "", "ISR": "", "EW": "", "COMMS": ""}
instructor_sockets = []


# ============================================================
# 4. LOGGING AND SMALL HELPERS
# ============================================================

def fmt(t):
    """Turn simulation seconds into 'T+04:12'."""
    if t is None:
        return "-"
    t = int(t)
    return "T+" + str(t // 60).zfill(2) + ":" + str(t % 60).zfill(2)


def log_event(kind, role, data, summary=""):
    """Write one event to the database and to the instructor's live log."""
    now = time.time()
    cursor = db.execute(
        "INSERT INTO events (session, sim_time, wall_time, kind, role, data) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, sim_time, now, kind, role, json.dumps(data)),
    )
    db.commit()
    event_id = cursor.lastrowid
    backup("INSERT INTO events (id, session, sim_time, wall_time, kind, role, data) VALUES (?, ?, ?, ?, ?, ?, ?) "
           "ON CONFLICT (id) DO NOTHING",
           (event_id, session_id, sim_time, now, kind, role, json.dumps(data)))
    if summary != "":
        recent_log.append({"id": event_id, "t": fmt(sim_time), "kind": kind, "role": role, "text": summary})
        if len(recent_log) > 80:
            recent_log.pop(0)
    return event_id


def role_title(role):
    if role in scenario.get("roles", {}):
        return scenario["roles"][role]["title"]
    return str(role)


def channel_name(via):
    names = {"net": "Radio", "relay": "Relay", "runner": "Runner", "flare": "Flare"}
    return names.get(via, via)


def worst_link(role_a, role_b):
    """A radio message is only as good as the worse of the sender's and receiver's links."""
    order = {"UP": 0, "DEGRADED": 1, "DOWN": 2}
    state_a = links.get(role_a, "UP")
    if role_b is None:
        return state_a
    state_b = links.get(role_b, "UP")
    if order[state_b] > order[state_a]:
        return state_b
    return state_a


def join_address():
    """The address other devices should open: the public URL when deployed, otherwise this laptop on the Wi-Fi."""
    if PUBLIC_URL != "":
        return PUBLIC_URL
    return "http://" + get_lan_ip() + ":" + str(PORT)


def get_lan_ip():
    """Find this laptop's address on the local network so others can join."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # nothing is actually sent
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def apply_fog_level(level):
    if level < 0:
        level = 0
    if level > len(FOG_LEVELS) - 1:
        level = len(FOG_LEVELS) - 1
    config["fog_level"] = level
    for field in ["base_delay", "random_delay", "drop_chance", "conflict_chance"]:
        config[field] = FOG_LEVELS[level][field]


def reset_exercise():
    """Start a brand new session from the scenario file."""
    global scenario, session_id, status, sim_time, links, link_until, located_until, sensors, sensor_until
    global reports, deliveries, decision_points, decisions, beliefs, grades, exposure, df_active
    global director_history, fired, probe, recent_log
    global delivery_counter, chat_counter, instructor_dp_counter, rng_use_count, variant_count

    with open(SCENARIO_FILE, "r", encoding="utf-8") as f:
        scenario = json.load(f)

    session_id = "S" + time.strftime("%Y%m%d-%H%M%S")
    # Two new sessions in the same second would share an id; add a number to keep them apart.
    base_id = session_id
    number = 2
    while db.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is not None:
        session_id = base_id + "-" + str(number)
        number = number + 1
    status = "READY"
    sim_time = 0
    links = {"GTL": "UP", "ISR": "UP", "EW": "UP", "COMMS": "UP"}
    link_until = {}
    located_until = {}
    sensors = {}
    for name in scenario["sensors"]:
        sensors[name] = "ONLINE"
    sensor_until = {}
    reports = {}
    deliveries = []
    decision_points = {}
    decisions = []
    beliefs = {"GTL": {}, "ISR": {}, "EW": {}, "COMMS": {}}
    grades = {"GTL": {}, "ISR": {}, "EW": {}, "COMMS": {}}
    exposure = {"GTL": 0.0, "ISR": 0.0, "EW": 0.0, "COMMS": 0.0}
    df_active = False
    director_history = []
    fired = set()
    probe = None
    recent_log = []
    delivery_counter = 0
    chat_counter = 0
    instructor_dp_counter = 0
    rng_use_count = {}
    variant_count = {}
    config["duration"] = scenario.get("duration", 900)

    created = time.time()
    db.execute(
        "INSERT OR REPLACE INTO sessions (session_id, scenario, created, status) VALUES (?, ?, ?, ?)",
        (session_id, scenario["name"], created, "READY"),
    )
    db.commit()
    backup("INSERT INTO sessions (session_id, scenario, created, status) VALUES (?, ?, ?, ?) "
           "ON CONFLICT (session_id) DO UPDATE SET status = EXCLUDED.status",
           (session_id, scenario["name"], created, "READY"))
    log_event("SCENARIO_LOADED", None, {"scenario": scenario, "config": dict(config)},
              "Loaded " + scenario["name"])


# ============================================================
# 5. THE COMMUNICATION ENGINE
# ============================================================
# A "report" is something that happened in the fictional world (with the time it
# was observed). A "delivery" is one copy of that report travelling to one role.
# Each delivery gets its own delay and its own chance of being lost, so two
# trainees can see the same report at different times, or not at all.
#
# Team messages can go over four channels (a PACE plan):
#   net    = Radio:  fast, but jamming slows it, loses it, or stops it
#   relay  = Relay:  through the Comms Coordinator's station; gets past jamming,
#                    slower, and only works while Comms is on the net
#   runner = Runner: always arrives, never jammed, but takes minutes
#   flare  = Flare:  instant, but only a few fixed signals, and everyone sees it

def schedule_delivery(report, to_role, kind, from_role=None, via="net", note="", ack_for=None):
    """Send one copy of a report to one role."""
    global delivery_counter
    delivery_counter = delivery_counter + 1
    delivery_id = "D" + str(delivery_counter)

    # A fresh random generator for every delivery, seeded from the session seed,
    # the report and the receiver. The same seed always gives the same result,
    # even if the instructor injects something else in between.
    key = str(config["seed"]) + ":" + str(report["report_id"]) + ":" + to_role + ":" + kind + ":" + str(from_role)
    count = rng_use_count.get(key, 0)
    rng_use_count[key] = count + 1
    rng = random.Random(key + ":" + str(count))

    # Always draw the same four numbers in the same order.
    extra_delay = rng.randint(0, int(config["random_delay"]))
    drop_roll = rng.random()
    conflict_roll = rng.random()
    conflict_extra = rng.randint(5, 30)

    link_now = worst_link(to_role, from_role)

    if via == "relay":
        delay = int(config["relay_delay"])
        drop_chance = 0.0
    elif via == "runner":
        delay = int(config["runner_delay"])
        drop_chance = 0.0
    elif via == "flare":
        delay = int(config["flare_delay"])
        drop_chance = 0.0
    else:
        if "fixed_delay" in report:
            delay = int(report["fixed_delay"])
        else:
            delay = int(config["base_delay"]) + extra_delay
        drop_chance = float(config["drop_chance"])
        if report.get("no_drop"):
            drop_chance = 0.0
        if link_now == "DEGRADED" and not report.get("no_drop"):
            delay = delay * 2
            drop_chance = drop_chance * 2 + 0.10

    if delay < 1:
        delay = 1

    delivery_status = "PENDING"
    if drop_roll < drop_chance:
        delivery_status = "DROPPED"

    delivery = {
        "delivery_id": delivery_id,
        "report_id": report["report_id"],
        "role": to_role,
        "from_role": from_role,
        "kind": kind,                 # report, forward, chat, read, flare or ack
        "via": via,                   # net, relay, runner or flare
        "note": note,
        "ack_for": ack_for,
        "scheduled_at": sim_time,
        "deliver_time": sim_time + delay,
        "delay": delay,
        "status": delivery_status,    # PENDING, HELD, DELIVERED or DROPPED
        "delivered_at": None,
        "link_at_send": link_now,
        "acked_at": None,
        "ack_received_at": None,
        "scripted": report.get("scripted", False),
    }
    deliveries.append(delivery)

    if kind == "report":
        text = report["report_id"] + " to " + to_role + ", delay " + str(delay) + "s"
        if delivery_status == "DROPPED":
            text = text + ", lost in transit"
    else:
        # Team traffic already has its own log line; only report losses here.
        text = ""
        if delivery_status == "DROPPED":
            text = report["report_id"] + " " + kind + " from " + str(from_role) + " to " + to_role + " lost in transit"
    log_event("SCHEDULED", to_role, delivery, text)

    # Sometimes the engine adds a contradicting report for this one receiver.
    if kind == "report" and "contradiction" in report:
        if conflict_roll < float(config["conflict_chance"]):
            make_conflict_report(report, to_role, delay + conflict_extra, "engine")

    return delivery


def make_conflict_report(report, to_role, delay, by, text=None, sender=None, grid=None):
    """Create a contradicting version of a report and send it to one role."""
    original_id = report["report_id"]
    n = variant_count.get(original_id, 0) + 1
    variant_count[original_id] = n
    letters = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
    new_id = original_id + "-" + letters[(n - 1) % len(letters)]

    contradiction = report.get("contradiction", {})
    if text is None:
        text = contradiction.get("text", "Report contradicts " + original_id)
    if sender is None:
        sender = contradiction.get("sender", "Unknown source")
    if grid is None:
        grid = contradiction.get("grid", report.get("grid", "-"))

    # The contradiction pushes the key question the other way.
    evidence = {}
    if "evidence" in contradiction:
        evidence = contradiction["evidence"]
    else:
        original_evidence = report.get("evidence", {})
        for q_id in original_evidence:
            evidence[q_id] = round(-0.8 * original_evidence[q_id], 2)

    new_report = {
        "report_id": new_id,
        "sender": sender,
        "domain": report.get("domain", "LAND"),
        "grid": grid,
        "text": text,
        "truth": False,
        "source_time": sim_time,
        "conflicts_with": original_id,
        "synthetic": True,
        "to_roles": [to_role],
        "fixed_delay": delay,
        "no_drop": True,
        "kind": "sensor",
        "created_by": by,
        "evidence": evidence,
    }
    reports[new_id] = new_report
    log_event("REPORT_CREATED", None, new_report,
              new_id + " contradicts " + original_id + " (" + by + "), for " + to_role)
    schedule_delivery(new_report, to_role, "report")
    return new_report


def deliver_due_messages():
    """Called every tick. Hands over messages whose delay has passed."""
    for d in deliveries:
        if d["status"] != "PENDING" and d["status"] != "HELD":
            continue
        if d["deliver_time"] > sim_time:
            continue

        # A message cannot get through a link that is DOWN, so it waits (HELD).
        blocked = False
        why = ""
        if d["via"] == "net":
            if worst_link(d["role"], d["from_role"]) == "DOWN":
                blocked = True
                why = "link down"
        if d["via"] == "relay":
            if links.get("COMMS") == "DOWN":
                blocked = True
                why = "relay station off the net"
        if blocked:
            if d["status"] != "HELD":
                d["status"] = "HELD"
                log_event("HELD", d["role"], {"delivery_id": d["delivery_id"], "report_id": d["report_id"], "why": why},
                          d["report_id"] + " for " + d["role"] + " held, " + why)
            continue

        d["status"] = "DELIVERED"
        d["delivered_at"] = sim_time
        report = reports.get(d["report_id"], {})
        age = sim_time - report.get("source_time", sim_time)
        log_event("DELIVERED", d["role"],
                  {"delivery_id": d["delivery_id"], "report_id": d["report_id"], "age": age}, "")

        # When an ACK arrives, the original sender learns their message got through.
        if d["kind"] == "ack":
            for original in deliveries:
                if original["delivery_id"] == d["ack_for"]:
                    original["ack_received_at"] = sim_time


def set_link(role, state, note, by, duration=None):
    """Change one role's link. With a duration, it goes back to UP by itself."""
    if role not in links or state not in ["UP", "DEGRADED", "DOWN"]:
        return "Unknown role or link state."
    old = links[role]
    links[role] = state
    if duration is not None and state != "UP":
        link_until[role] = sim_time + int(duration)
    elif role in link_until:
        del link_until[role]
    if state == "UP" and role in located_until:
        del located_until[role]
    data = {"role": role, "old": old, "state": state, "note": note, "by": by, "duration": duration}
    text = role_title(role) + " link " + old + " to " + state + " (" + by + ")"
    if duration is not None and state != "UP":
        text = text + " for " + str(int(duration)) + " s"
    log_event("LINK_CHANGE", role, data, text)
    return ""


def set_sensor(sensor, state, until, by):
    if sensor not in sensors or state not in ["ONLINE", "OFFLINE"]:
        return "Unknown sensor or state."
    sensors[sensor] = state
    if state == "OFFLINE" and until is not None:
        sensor_until[sensor] = int(until)
    if state == "ONLINE" and sensor in sensor_until:
        del sensor_until[sensor]
    log_event("SENSOR_CHANGE", None, {"sensor": sensor, "state": state, "until": until, "by": by},
              sensor + " " + state + " (" + by + ")")
    return ""


# ============================================================
# 6. DETECTABILITY ("emissions equal target")
# ============================================================
# Every radio transmission raises the sender's detectability. It fades slowly.
# While enemy direction finding is active, a role that reaches the threshold is
# located: it has to relocate and is off the net for a while. The team must
# trade talking (coordination) against staying quiet (survival).

def add_emission(role, amount, reason):
    if role not in exposure or amount <= 0:
        return
    exposure[role] = exposure[role] + amount
    log_event("EMIT", role, {"amount": amount, "reason": reason, "exposure": round(exposure[role], 1)}, "")
    if df_active and exposure[role] >= config["located_threshold"]:
        if located_until.get(role, -1) <= sim_time and links[role] != "DOWN":
            locate(role)


def locate(role):
    penalty = int(config["located_penalty"])
    log_event("LOCATED", role, {"exposure": round(exposure[role], 1), "penalty": penalty},
              role_title(role) + " was located by direction finding and is relocating for " + str(penalty) + " s")
    exposure[role] = 30.0
    located_until[role] = sim_time + penalty
    set_link(role, "DOWN", "Located by direction finding, relocating", "engine", duration=penalty)


def set_df(state, by):
    global df_active
    df_active = bool(state)
    log_event("DF_CHANGE", None, {"active": df_active, "by": by},
              "Enemy direction finding " + ("active" if df_active else "off") + " (" + by + ")")


# ============================================================
# 7. SCENARIO INJECTS, DECISIONS, PROBES, READS AND GRADES
# ============================================================

def create_report(row, by):
    """Create a report from a scenario row (or an instructor inject) and send it out."""
    sender = row.get("sender", "")
    if sender in sensors and sensors[sender] == "OFFLINE":
        log_event("REPORT_SUPPRESSED", None, {"report_id": row["report_id"], "sender": sender, "by": by},
                  row["report_id"] + " not sent, " + sender + " is offline")
        return None

    report = {}
    for key in row:
        if key != "at" and key != "action":
            report[key] = row[key]
    report["source_time"] = sim_time
    report["kind"] = "sensor"
    report["scripted"] = (by == "script")
    report["created_by"] = by
    reports[report["report_id"]] = report
    log_event("REPORT_CREATED", None, report, report["report_id"] + " from " + sender + ": " + report["text"])

    for role in report.get("to_roles", []):
        if role in ROLES:
            schedule_delivery(report, role, "report")
    return report


def open_decision_point(row, by):
    dp = {}
    for key in row:
        if key != "at" and key != "action":
            dp[key] = row[key]
    dp["opened_at"] = sim_time
    dp["deadline"] = sim_time + int(dp.get("window", 90))
    dp["expires_at"] = sim_time + 2 * int(dp.get("window", 90))
    dp["status"] = "OPEN"
    dp["responses"] = {}
    dp["created_by"] = by
    decision_points[dp["dp_id"]] = dp
    log_event("DP_OPEN", None, dp, dp["dp_id"] + " opened for " + ", ".join(dp["roles"]) + ": " + dp["prompt"])


def run_inject(row, by):
    action = row.get("action", "")
    if action == "REPORT":
        create_report(row, by)
    elif action == "LINK":
        set_link(row["role"], row["state"], row.get("note", ""), by, row.get("duration"))
    elif action == "SENSOR":
        set_sensor(row["sensor"], row["state"], row.get("until"), by)
    elif action == "DF":
        set_df(row.get("state", True), by)
    elif action == "DECISION_POINT":
        open_decision_point(row, by)
    elif action == "END":
        end_exercise()


def visible_for(role):
    """(report_id, first time it reached this role) for everything this role has received."""
    best = {}
    for d in deliveries:
        if d["role"] != role or d["status"] != "DELIVERED" or d["kind"] == "ack":
            continue
        rid = d["report_id"]
        if rid not in best or d["delivered_at"] < best[rid]:
            best[rid] = d["delivered_at"]
    result = []
    for rid in best:
        result.append((rid, best[rid]))
    return result


def shadow_now(role):
    """What a careful reader with exactly this role's reports would believe right now."""
    visible = visible_for(role)
    result = {}
    for q in scenario.get("questions", []):
        value, reasons = shadow.shadow_value(q, visible, sim_time, reports, scenario)
        result[q["q_id"]] = value
    return result


def truth_now():
    result = {}
    for q in scenario.get("questions", []):
        result[q["q_id"]] = shadow.truth_value(q, sim_time)
    return result


def build_knowledge_snapshot(role):
    """What did this role actually have in front of them right now?"""
    best = {}   # report_id -> earliest delivery of it to this role
    for d in deliveries:
        if d["role"] != role or d["status"] != "DELIVERED" or d["kind"] == "ack":
            continue
        rid = d["report_id"]
        if rid not in best or d["delivered_at"] < best[rid]["delivered_at"]:
            best[rid] = d

    visible = []
    for rid in best:
        d = best[rid]
        report = reports.get(rid, {})
        visible.append({
            "report_id": rid,
            "received_at": d["delivered_at"],
            "source_time": report.get("source_time", 0),
            "age": sim_time - report.get("source_time", 0),
            "grid": report.get("grid", "-"),
            "via": d["kind"] if d["from_role"] is None else d["kind"] + " from " + d["from_role"],
        })

    conflict_pairs = []
    for rid in best:
        partner = reports.get(rid, {}).get("conflicts_with")
        if partner is not None and partner in best:
            pair = sorted([rid, partner])
            if pair not in conflict_pairs:
                conflict_pairs.append(pair)

    return visible, list(best.keys()), conflict_pairs


def who_had_report(report_id, except_role):
    roles = []
    for d in deliveries:
        if d["report_id"] == report_id and d["status"] == "DELIVERED" and d["kind"] != "ack":
            if d["role"] != except_role and d["role"] not in roles:
                roles.append(d["role"])
    return roles


def save_decision(role, name, dp_id, choice, rationale, cited, confidence):
    dp = decision_points.get(dp_id)
    if dp is None or dp["status"] != "OPEN":
        return "This decision point is already closed."
    if role not in dp["roles"]:
        return "This decision point is not for your role."
    if role in dp["responses"]:
        return "You have already submitted this decision."

    choice_label = "No decision"
    if choice is not None:
        found = False
        for option in dp["options"]:
            if option["id"] == choice:
                choice_label = option["label"]
                found = True
        if not found:
            return "Pick one of the options."

    visible, visible_ids, conflict_pairs = build_knowledge_snapshot(role)

    relevant_missing = []
    for rid in dp.get("relevant_reports", []):
        if rid not in visible_ids:
            relevant_missing.append({
                "report_id": rid,
                "exists": rid in reports,
                "had_by": who_had_report(rid, role),
            })

    clean_cited = []
    for rid in cited:
        if rid in visible_ids:
            clean_cited.append(rid)

    my_reads = {}
    for q_id in beliefs[role]:
        my_reads[q_id] = beliefs[role][q_id]["value"]

    decision = {
        "dp_id": dp_id,
        "prompt": dp["prompt"],
        "role": role,
        "name": name,
        "choice": choice,
        "choice_label": choice_label,
        "rationale": rationale.strip(),
        "cited": clean_cited,
        "confidence": confidence,
        "time": sim_time,
        "opened_at": dp["opened_at"],
        "latency": sim_time - dp["opened_at"],
        "window": int(dp.get("window", 90)),
        "late": (sim_time - dp["opened_at"]) > int(dp.get("window", 90)),
        "team": dp.get("team", False),
        "is_team_decision": dp.get("team", False) and role == "GTL",
        "visible": visible,
        "visible_ids": visible_ids,
        "conflict_pairs": conflict_pairs,
        "relevant_missing": relevant_missing,
        "links": dict(links),
        "reads": my_reads,
        "shadow": shadow_now(role),
        "truth": truth_now(),
        "question": dp.get("question"),
    }
    decision["score"] = aar.score_decision(decision, dp, reports, config)
    dp["responses"][role] = decision
    decisions.append(decision)

    if choice is None:
        text = role_title(role) + " made no decision at " + dp_id
    else:
        text = role_title(role) + " decided " + choice + " at " + dp_id + " after " + str(decision["latency"]) + "s"
    log_event("DECISION", role, decision, text)
    return ""


def close_decision_point(dp):
    for role in dp["roles"]:
        if role not in dp["responses"]:
            save_decision(role, role_names.get(role, ""), dp["dp_id"], None, "", [], "")
    dp["status"] = "CLOSED"
    log_event("DP_CLOSE", None, {"dp_id": dp["dp_id"]}, dp["dp_id"] + " closed")


def check_decision_points():
    for dp_id in list(decision_points.keys()):
        dp = decision_points[dp_id]
        if dp["status"] == "OPEN":
            everyone_answered = True
            for role in dp["roles"]:
                if role not in dp["responses"]:
                    everyone_answered = False
            if everyone_answered:
                dp["status"] = "CLOSED"
                log_event("DP_CLOSE", None, {"dp_id": dp_id}, dp_id + " closed, all answers in")
            elif sim_time >= dp["expires_at"]:
                close_decision_point(dp)


def save_belief(role, q_id, value):
    """A trainee moved one of their 'your read' sliders."""
    question = None
    for q in scenario.get("questions", []):
        if q["q_id"] == q_id:
            question = q
    if question is None:
        return "Unknown question."
    try:
        value = int(round(float(value)))
    except Exception:
        return "The read must be a number from 0 to 100."
    if value < 0:
        value = 0
    if value > 100:
        value = 100
    old = beliefs[role].get(q_id)
    if old is not None and old["value"] == value:
        return ""
    beliefs[role][q_id] = {"value": value, "time": sim_time}
    shadow_values = shadow_now(role)
    log_event("BELIEF", role, {"q_id": q_id, "value": value, "old": old["value"] if old else None,
                               "shadow": shadow_values.get(q_id), "truth": shadow.truth_value(question, sim_time)},
              role_title(role) + " read " + question["short"] + " at " + str(value) + "%")
    return ""


def save_grade(role, report_id, reliability, credibility):
    """A trainee graded a report with the NATO source-grading system (A-F, 1-6)."""
    if reliability not in ["A", "B", "C", "D", "E", "F"] or str(credibility) not in ["1", "2", "3", "4", "5", "6"]:
        return "Grades are a letter A to F and a number 1 to 6."
    had_it = False
    for item in visible_for(role):
        if item[0] == report_id:
            had_it = True
    if not had_it:
        return "You can only grade reports you have received."
    grades[role][report_id] = {"reliability": reliability, "credibility": str(credibility), "time": sim_time}
    report = reports.get(report_id, {})
    log_event("GRADE", role, {"report_id": report_id, "reliability": reliability, "credibility": str(credibility),
                              "truth": report.get("truth"), "sender": report.get("sender")},
              role_title(role) + " graded " + report_id + " as " + reliability + str(credibility))
    return ""


def start_probe(probe_id):
    global probe, status, status_before_probe
    if status != "RUNNING" and status != "PAUSED":
        return "Start the exercise before running a probe."
    if probe is not None:
        return "A probe is already running."
    chosen = None
    for p in scenario.get("probes", []):
        if p["probe_id"] == probe_id:
            chosen = p
    if chosen is None:
        return "Unknown probe."
    status_before_probe = status
    status = "FROZEN"
    probe = {"probe_id": chosen["probe_id"], "label": chosen["label"], "questions": chosen["questions"],
             "started_at": sim_time, "answers": {}}
    log_event("PROBE_START", None, {"probe_id": probe_id}, "Freeze probe " + probe_id + " started, clock stopped")
    return ""


def answer_probe(role, name, probe_id, answers):
    if probe is None or probe["probe_id"] != probe_id:
        return "This probe has ended."
    if role in probe["answers"]:
        return "You have already answered."
    visible, visible_ids, conflict_pairs = build_knowledge_snapshot(role)
    results = []
    for q in probe["questions"]:
        given = answers.get(q["q_id"], "")
        evidence_visible = False
        for rid in q.get("evidence", []):
            if rid in visible_ids:
                evidence_visible = True
        results.append({
            "q_id": q["q_id"],
            "level": q["level"],
            "text": q["text"],
            "answer": given,
            "correct_answer": q["answer"],
            "correct": given == q["answer"],
            "evidence_visible": evidence_visible,
        })
    probe["answers"][role] = results
    correct = 0
    for r in results:
        if r["correct"]:
            correct = correct + 1
    log_event("PROBE_ANSWER", role, {"probe_id": probe_id, "name": name, "results": results},
              role_title(role) + " answered " + probe_id + ": " + str(correct) + "/" + str(len(results)) + " correct")

    # End the probe automatically when every connected trainee has answered.
    all_done = True
    for r in ROLES:
        if len(role_sockets[r]) > 0 and r not in probe["answers"]:
            all_done = False
    if all_done:
        end_probe()
    return ""


def end_probe():
    global probe, status
    if probe is None:
        return "No probe is running."
    log_event("PROBE_END", None, {"probe_id": probe["probe_id"], "answered": list(probe["answers"].keys())},
              "Freeze probe " + probe["probe_id"] + " ended, clock running")
    probe = None
    status = status_before_probe
    return ""


def end_exercise():
    global status
    if status == "ENDED":
        return
    if probe is not None:
        end_probe()
    for dp_id in list(decision_points.keys()):
        if decision_points[dp_id]["status"] == "OPEN":
            close_decision_point(decision_points[dp_id])
    status = "ENDED"
    log_event("SESSION_END", None, {"sim_time": sim_time}, "Exercise ended at " + fmt(sim_time))
    db.execute("UPDATE sessions SET status = ? WHERE session_id = ?", ("ENDED", session_id))
    db.commit()
    backup("UPDATE sessions SET status = ? WHERE session_id = ?", ("ENDED", session_id))


# ============================================================
# 8. THE FOG DIRECTOR (optional automatic difficulty)
# ============================================================
# Every minute it compares each trainee's reads with their shadow analyst.
# Reads that track the shadow closely mean the team is coping with the fog,
# so it thickens. Reads that drift away, or decisions left overdue, mean the
# team is overloaded, so it thins. This keeps the exercise hard but learnable.

def director_step():
    gaps = []
    for role in ROLES:
        if len(role_sockets[role]) == 0:
            continue
        shadow_values = shadow_now(role)
        for q_id in beliefs[role]:
            gaps.append(abs(beliefs[role][q_id]["value"] - shadow_values.get(q_id, 50)))
    overdue = 0
    for dp_id in decision_points:
        dp = decision_points[dp_id]
        if dp["status"] == "OPEN" and sim_time > dp["deadline"]:
            overdue = overdue + 1

    old_level = config["fog_level"]
    if len(gaps) == 0:
        reason = "No reads yet, so the fog stays where it is."
        new_level = old_level
        tracking = None
    else:
        tracking = round(100 - sum(gaps) / len(gaps))
        if overdue > 0:
            new_level = old_level - 1
            reason = str(overdue) + " decision(s) overdue, easing the fog."
        elif tracking >= 80:
            new_level = old_level + 1
            reason = "Reads track the evidence closely (" + str(tracking) + "%), thickening the fog."
        elif tracking < 65:
            new_level = old_level - 1
            reason = "Reads are drifting from the evidence (" + str(tracking) + "%), easing the fog."
        else:
            new_level = old_level
            reason = "Reads track the evidence reasonably (" + str(tracking) + "%), holding."
    if new_level < 0:
        new_level = 0
    if new_level > len(FOG_LEVELS) - 1:
        new_level = len(FOG_LEVELS) - 1
    apply_fog_level(new_level)
    entry = {"t": sim_time, "from": old_level, "to": new_level, "tracking": tracking, "overdue": overdue, "reason": reason}
    director_history.append(entry)
    summary = ""
    if new_level != old_level:
        summary = "Fog Director: " + FOG_LEVELS[old_level]["name"] + " to " + FOG_LEVELS[new_level]["name"] + ". " + reason
    log_event("DIRECTOR", None, entry, summary)


# ============================================================
# 9. WHAT EACH TRAINEE IS ALLOWED TO SEE
# ============================================================
# A trainee never sees ground truth, lost messages or other people's feeds.

def build_hello(role):
    """Sent once when a trainee connects: the parts of the scenario that never change."""
    questions = []
    for q in scenario.get("questions", []):
        questions.append({"q_id": q["q_id"], "text": q["text"], "short": q["short"], "yes": q["yes"], "no": q["no"]})
    return {
        "type": "hello",
        "session_id": session_id,
        "role": role,
        "role_title": role_title(role),
        "role_brief": scenario["roles"][role]["brief"],
        "name": scenario["name"],
        "area": scenario["area"],
        "disclaimer": scenario["disclaimer"],
        "mission": scenario["mission"],
        "intent": scenario["intent"],
        "map": scenario["map"],
        "questions": questions,
        "flares": scenario.get("flares", []),
        "relay_delay": config["relay_delay"],
        "runner_delay": config["runner_delay"],
        "roles": [{"role": r, "title": role_title(r), "short": scenario["roles"][r].get("short", r)} for r in ROLES],
    }


def build_trainee_state(role):
    visible, visible_ids, conflict_pairs = build_knowledge_snapshot(role)

    feed = []
    team_reads = {}
    for d in deliveries:
        if d["role"] != role or d["status"] != "DELIVERED" or d["kind"] == "ack":
            continue
        report = reports.get(d["report_id"], {})
        age_on_arrival = d["delivered_at"] - report.get("source_time", 0)

        if d["kind"] == "read":
            for q_id in report.get("reads", {}):
                team_reads.setdefault(q_id, {})
                team_reads[q_id][d["from_role"]] = {"from_role": d["from_role"], "from_title": role_title(d["from_role"]),
                                                    "value": report["reads"][q_id], "sent_at": report.get("source_time", 0),
                                                    "received_at": d["delivered_at"]}

        chips = []
        if d["kind"] == "chat" or d["kind"] == "read":
            chips.append("Team message")
        if d["kind"] == "flare":
            chips.append("Flare")
        if d["kind"] == "forward":
            chips.append("Passed on")
        if d["from_role"] is not None and d["via"] != "net":
            chips.append(channel_name(d["via"]))
        if age_on_arrival > int(config["delayed_label_after"]):
            chips.append("Delayed")
        else:
            chips.append("Received")
        conflicts_with = []
        partner = report.get("conflicts_with")
        if partner is not None and partner in visible_ids:
            conflicts_with.append(partner)
        for other_id in visible_ids:
            if reports.get(other_id, {}).get("conflicts_with") == d["report_id"] and other_id not in conflicts_with:
                conflicts_with.append(other_id)
        if len(conflicts_with) > 0:
            chips.append("Conflicting")

        grade = grades[role].get(d["report_id"])
        feed.append({
            "delivery_id": d["delivery_id"],
            "report_id": d["report_id"],
            "kind": d["kind"],
            "via": d["via"],
            "from_role": d["from_role"],
            "from_title": role_title(d["from_role"]) if d["from_role"] else "",
            "note": d["note"],
            "sender": report.get("sender", ""),
            "domain": report.get("domain", ""),
            "grid": report.get("grid", "-"),
            "text": report.get("text", ""),
            "source_time": report.get("source_time", 0),
            "delivered_at": d["delivered_at"],
            "transit": age_on_arrival,
            "age": sim_time - report.get("source_time", 0),
            "chips": chips,
            "conflicts_with": conflicts_with,
            "ackable": d["from_role"] is not None and d["acked_at"] is None and d["kind"] != "flare",
            "acked": d["acked_at"] is not None,
            "gradeable": d["kind"] in ["report", "forward"],
            "grade": (grade["reliability"] + grade["credibility"]) if grade else "",
        })
    feed.sort(key=lambda item: item["delivered_at"], reverse=True)

    reads_out = []
    for q_id in team_reads:
        for from_role in team_reads[q_id]:
            item = team_reads[q_id][from_role]
            item["q_id"] = q_id
            reads_out.append(item)

    sent = []
    for d in deliveries:
        if d["from_role"] == role and d["kind"] in ["chat", "forward", "read", "flare"]:
            report = reports.get(d["report_id"], {})
            sent.append({
                "delivery_id": d["delivery_id"],
                "report_id": d["report_id"],
                "kind": d["kind"],
                "via": d["via"],
                "channel": channel_name(d["via"]),
                "to_role": d["role"],
                "to_title": role_title(d["role"]),
                "text": report.get("text", ""),
                "sent_at": d["scheduled_at"],
                "ack_received_at": d["ack_received_at"],
            })
    sent.sort(key=lambda item: item["sent_at"], reverse=True)

    open_dps = []
    for dp_id in decision_points:
        dp = decision_points[dp_id]
        if dp["status"] != "OPEN" or role not in dp["roles"]:
            continue
        mine = dp["responses"].get(role)
        open_dps.append({
            "dp_id": dp_id,
            "prompt": dp["prompt"],
            "options": dp["options"],
            "opened_at": dp["opened_at"],
            "deadline": dp["deadline"],
            "team": dp.get("team", False),
            "is_team_lead": role == "GTL",
            "question": dp.get("question"),
            "submitted": mine is not None,
            "my_choice": mine["choice_label"] if mine else "",
        })

    my_decisions = []
    for decision in decisions:
        if decision["role"] == role:
            my_decisions.append({"dp_id": decision["dp_id"], "choice": decision["choice_label"],
                                 "time": decision["time"]})

    probe_view = None
    if probe is not None:
        if role in probe["answers"]:
            probe_view = {"probe_id": probe["probe_id"], "answered": True}
        else:
            questions = []
            for q in probe["questions"]:
                questions.append({"q_id": q["q_id"], "text": q["text"], "options": q["options"]})
            probe_view = {"probe_id": probe["probe_id"], "answered": False, "questions": questions}

    link_view = "UP"
    if links[role] == "DOWN":
        link_view = "DOWN"
    if links[role] == "DEGRADED" and config["show_degraded_to_trainee"]:
        link_view = "DEGRADED"
    back_in = None
    if links[role] == "DOWN" and role in link_until:
        back_in = link_until[role] - sim_time

    teammates = []
    for r in ROLES:
        teammates.append({"role": r, "title": role_title(r), "name": role_names[r],
                          "online": len(role_sockets[r]) > 0})

    my_reads = {}
    for q_id in beliefs[role]:
        my_reads[q_id] = beliefs[role][q_id]

    my_grades = {}
    for rid in grades[role]:
        my_grades[rid] = grades[role][rid]["reliability"] + grades[role][rid]["credibility"]

    return {
        "type": "state",
        "session_id": session_id,
        "status": status,
        "sim_time": sim_time,
        "clock": fmt(sim_time),
        "duration": config["duration"],
        "link": link_view,
        "back_in": back_in,
        "located": located_until.get(role, -1) > sim_time,
        "exposure": round(exposure[role]),
        "threshold": config["located_threshold"],
        "feed": feed,
        "sent": sent,
        "open_dps": open_dps,
        "my_decisions": my_decisions,
        "probe": probe_view,
        "teammates": teammates,
        "reads": my_reads,
        "team_reads": reads_out,
        "grades": my_grades,
    }


# ============================================================
# 10. WHAT THE INSTRUCTOR SEES (everything, including ground truth)
# ============================================================

def build_contradictions():
    """Every pair of reports that disagree, who saw which side, and whether it got resolved."""
    pairs = []
    seen = []
    for rid in reports:
        report = reports[rid]
        partner_id = report.get("conflicts_with")
        if partner_id is None or partner_id not in reports:
            continue
        key = sorted([rid, partner_id])
        if key in seen:
            continue
        seen.append(key)
        a = reports[key[0]]
        b = reports[key[1]]
        q_id = None
        for q in a.get("evidence", {}):
            q_id = q
        for q in b.get("evidence", {}):
            if q_id is None:
                q_id = q
        saw_a = []
        saw_b = []
        for role in ROLES:
            ids = [item[0] for item in visible_for(role)]
            if key[0] in ids:
                saw_a.append(role)
            if key[1] in ids:
                saw_b.append(role)
        both = [r for r in saw_a if r in saw_b]
        resolved = []
        question = None
        for q in scenario.get("questions", []):
            if q["q_id"] == q_id:
                question = q
        if question is not None:
            truth = shadow.truth_value(question, sim_time)
            for role in both:
                read = beliefs[role].get(q_id)
                if read is None:
                    continue
                if truth and read["value"] >= 65:
                    resolved.append(role)
                if (not truth) and read["value"] <= 35:
                    resolved.append(role)
        if len(resolved) > 0:
            state = "Resolved"
        elif len(both) > 0:
            state = "Seen, unresolved"
        else:
            state = "Nobody has both sides"
        pairs.append({
            "a": key[0], "b": key[1],
            "a_sender": a.get("sender", ""), "b_sender": b.get("sender", ""),
            "a_text": a.get("text", ""), "b_text": b.get("text", ""),
            "a_truth": a.get("truth"), "b_truth": b.get("truth"),
            "a_time": fmt(a.get("source_time", 0)), "b_time": fmt(b.get("source_time", 0)),
            "question": question["short"] if question else "",
            "saw_a": saw_a, "saw_b": saw_b, "both": both, "resolved": resolved, "state": state,
        })
    return pairs


def build_instructor_state():
    rows = []
    for rid in reports:
        report = reports[rid]
        if report.get("kind") != "sensor":
            continue
        cells = {}
        for role in ROLES:
            best_status = ""
            delivered_at = None
            delay = None
            via_text = ""
            for d in deliveries:
                if d["report_id"] != rid or d["role"] != role or d["kind"] == "ack":
                    continue
                if d["status"] == "DELIVERED":
                    if delivered_at is None or d["delivered_at"] < delivered_at:
                        delivered_at = d["delivered_at"]
                        delay = d["delivered_at"] - d["scheduled_at"]
                        if d["from_role"] is not None:
                            via_text = "via " + d["from_role"]
                        else:
                            via_text = ""
                    best_status = "DELIVERED"
                elif d["status"] == "HELD" and best_status != "DELIVERED":
                    best_status = "HELD"
                elif d["status"] == "PENDING" and best_status not in ["DELIVERED", "HELD"]:
                    best_status = "PENDING"
                elif d["status"] == "DROPPED" and best_status == "":
                    best_status = "DROPPED"
            cells[role] = {"status": best_status, "at": fmt(delivered_at) if delivered_at is not None else "",
                           "delay": delay, "via": via_text}
        rows.append({
            "report_id": rid,
            "sender": report.get("sender", ""),
            "domain": report.get("domain", ""),
            "grid": report.get("grid", "-"),
            "text": report.get("text", ""),
            "truth": report.get("truth"),
            "synthetic": report.get("synthetic", False),
            "conflicts_with": report.get("conflicts_with"),
            "critical": report.get("critical", False),
            "source_time": fmt(report.get("source_time", 0)),
            "cells": cells,
        })

    dps = []
    for dp_id in decision_points:
        dp = decision_points[dp_id]
        dps.append({"dp_id": dp_id, "prompt": dp["prompt"], "roles": dp["roles"], "status": dp["status"],
                    "opened_at": fmt(dp["opened_at"]), "deadline": dp["deadline"],
                    "answered": list(dp["responses"].keys())})

    decision_list = []
    for decision in reversed(decisions):
        decision_list.append({
            "dp_id": decision["dp_id"],
            "role": decision["role"],
            "name": decision["name"],
            "choice": decision["choice_label"],
            "rationale": decision["rationale"],
            "confidence": decision["confidence"],
            "time": fmt(decision["time"]),
            "latency": decision["latency"],
            "late": decision["late"],
            "visible_count": len(decision["visible_ids"]),
            "missing": [m["report_id"] for m in decision["relevant_missing"]],
            "conflicts": decision["conflict_pairs"],
            "percent": decision["score"]["percent"],
        })

    upcoming = []
    for i in range(len(scenario["injects"])):
        row = scenario["injects"][i]
        action = row["action"]
        if action == "REPORT":
            text = row["report_id"] + " from " + row["sender"] + " to " + ", ".join(row["to_roles"])
        elif action == "LINK":
            text = role_title(row["role"]) + " link goes " + row["state"].lower()
        elif action == "SENSOR":
            text = row["sensor"] + " goes " + row["state"].lower()
        elif action == "DF":
            text = "Enemy direction finding " + ("starts" if row.get("state", True) else "stops")
        elif action == "DECISION_POINT":
            text = row["dp_id"] + " for " + ", ".join(row["roles"])
        else:
            text = action
        upcoming.append({"at": fmt(row["at"]), "seconds": row["at"], "action": action, "text": text,
                         "fired": i in fired})

    truth = truth_now()
    trainees = []
    for r in ROLES:
        until = link_until.get(r)
        shadow_values = shadow_now(r)
        picture = []
        for q in scenario.get("questions", []):
            read = beliefs[r].get(q["q_id"])
            picture.append({"q_id": q["q_id"], "short": q["short"],
                            "read": read["value"] if read else None,
                            "read_at": read["time"] if read else None,
                            "shadow": shadow_values.get(q["q_id"]),
                            "truth": truth.get(q["q_id"])})
        pending = 0
        held = 0
        for d in deliveries:
            if d["role"] == r and d["kind"] != "ack":
                if d["status"] == "PENDING":
                    pending = pending + 1
                if d["status"] == "HELD":
                    held = held + 1
        trainees.append({"role": r, "title": role_title(r), "short": scenario["roles"][r].get("short", r),
                         "name": role_names[r], "online": len(role_sockets[r]) > 0, "link": links[r],
                         "until": (until - sim_time) if until is not None else None,
                         "located": located_until.get(r, -1) > sim_time,
                         "exposure": round(exposure[r]), "picture": picture,
                         "pending": pending, "held": held})

    counts = {"PENDING": 0, "HELD": 0, "DELIVERED": 0, "DROPPED": 0}
    for d in deliveries:
        if d["kind"] != "ack":
            counts[d["status"]] = counts[d["status"]] + 1

    # Messages in flight, for the live network picture.
    packets = []
    for d in deliveries:
        if d["status"] != "PENDING" and d["status"] != "HELD":
            continue
        report = reports.get(d["report_id"], {})
        if d["from_role"] is None:
            source = report.get("sender", "")
            source_type = "source"
        else:
            source = d["from_role"]
            source_type = "role"
        span = max(1, d["deliver_time"] - d["scheduled_at"])
        progress = (sim_time - d["scheduled_at"]) / span
        if progress > 1:
            progress = 1
        packets.append({"id": d["delivery_id"], "from": source, "from_type": source_type, "to": d["role"],
                        "kind": d["kind"], "via": d["via"], "status": d["status"], "progress": round(progress, 3),
                        "eta": d["deliver_time"] - sim_time, "report_id": d["report_id"]})
    arrivals = []
    for d in deliveries:
        if d["status"] == "DELIVERED" and d["delivered_at"] is not None and d["delivered_at"] >= sim_time - 1:
            arrivals.append({"id": d["delivery_id"], "to": d["role"], "kind": d["kind"]})

    source_names = []
    for name in scenario["sensors"]:
        source_names.append(name)
    for rid in reports:
        sender = reports[rid].get("sender")
        if reports[rid].get("kind") == "sensor" and sender and sender not in source_names:
            source_names.append(sender)
    source_list = []
    for name in source_names:
        until = sensor_until.get(name)
        feeds = []
        for row in scenario["injects"]:
            if row.get("action") == "REPORT" and row.get("sender") == name:
                for r in row.get("to_roles", []):
                    if r not in feeds:
                        feeds.append(r)
        for rid in reports:
            if reports[rid].get("sender") == name:
                for r in reports[rid].get("to_roles", []):
                    if r not in feeds:
                        feeds.append(r)
        source_list.append({"name": name, "state": sensors.get(name, "ONLINE"), "scripted": name in sensors,
                            "until": (until - sim_time) if until is not None else None, "feeds": feeds})

    probe_view = None
    if probe is not None:
        probe_view = {"probe_id": probe["probe_id"], "label": probe["label"],
                      "answered": list(probe["answers"].keys())}

    questions = []
    for q in scenario.get("questions", []):
        questions.append({"q_id": q["q_id"], "short": q["short"], "text": q["text"]})

    return {
        "type": "state",
        "session_id": session_id,
        "scenario_name": scenario["name"],
        "status": status,
        "sim_time": sim_time,
        "clock": fmt(sim_time),
        "duration": config["duration"],
        "config": config,
        "fog_levels": [level["name"] for level in FOG_LEVELS],
        "director_history": director_history[-12:],
        "df_active": df_active,
        "trainees": trainees,
        "sources": source_list,
        "questions": questions,
        "reports": rows,
        "contradictions": build_contradictions(),
        "decision_points": dps,
        "decisions": decision_list,
        "upcoming": upcoming,
        "log": list(reversed(recent_log)),
        "counts": counts,
        "packets": packets,
        "arrivals": arrivals,
        "probe": probe_view,
        "probes": [{"probe_id": p["probe_id"], "label": p["label"]} for p in scenario.get("probes", [])],
        "join_url": join_address(),
        "fingerprint": aar.fingerprint(deliveries),
    }


async def send_safely(ws, message):
    try:
        await ws.send_json(message)
        return True
    except Exception:
        return False


async def push_all():
    """Send the latest state to every connected screen."""
    for role in ROLES:
        if len(role_sockets[role]) == 0:
            continue
        message = build_trainee_state(role)
        dead = []
        for ws in role_sockets[role]:
            ok = await send_safely(ws, message)
            if not ok:
                dead.append(ws)
        for ws in dead:
            if ws in role_sockets[role]:
                role_sockets[role].remove(ws)

    if len(instructor_sockets) > 0:
        message = build_instructor_state()
        dead = []
        for ws in instructor_sockets:
            ok = await send_safely(ws, message)
            if not ok:
                dead.append(ws)
        for ws in dead:
            if ws in instructor_sockets:
                instructor_sockets.remove(ws)


# ============================================================
# 11. THE TICK LOOP (the simulation clock)
# ============================================================
# Runs forever in the background. Each tick is one simulation second.
# Never use time.sleep() in here: it would freeze every connection.

async def tick_loop():
    global sim_time
    while True:
        speed = float(config.get("speed", 1))
        if speed <= 0:
            speed = 1
        await asyncio.sleep(1.0 / speed)
        try:
            if status == "RUNNING":
                sim_time = sim_time + 1

                for i in range(len(scenario["injects"])):
                    row = scenario["injects"][i]
                    if i not in fired and row["at"] <= sim_time:
                        fired.add(i)
                        run_inject(row, "script")

                for name in list(sensor_until.keys()):
                    if sim_time >= sensor_until[name]:
                        set_sensor(name, "ONLINE", None, "timer")

                for role in list(link_until.keys()):
                    if sim_time >= link_until[role]:
                        set_link(role, "UP", "Timed outage ended", "timer")

                for role in ROLES:
                    exposure[role] = max(0.0, exposure[role] - float(config["emission_decay"]))

                deliver_due_messages()
                check_decision_points()

                if config["director"] and sim_time % 60 == 0:
                    director_step()

                if sim_time >= int(config["duration"]):
                    end_exercise()

            await push_all()
        except Exception as error:
            # Keep the clock alive even if one tick goes wrong, and show why.
            print("Tick error:", repr(error))


@asynccontextmanager
async def lifespan(app):
    reset_exercise()
    task = asyncio.create_task(tick_loop())
    print("")
    print("  SituSim is running.")
    print("  On this laptop:     http://localhost:" + str(PORT))
    print("  Other devices join: " + join_address())
    print("")
    yield
    task.cancel()


app = FastAPI(title="SituSim", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ---------- instructor passcode (only when INSTRUCTOR_PASSCODE is set) ----------
# The console, its API, the live state (which includes the ground truth) and the
# review of an exercise that is still running all need the passcode. Trainee
# pages and reviews of finished exercises stay open. The instructor types the
# passcode once; the browser keeps it in a cookie.

INSTRUCTOR_PATHS = ["/api/state", "/api/control", "/api/config", "/api/link", "/api/sensor", "/api/df",
                    "/api/director", "/api/inject", "/api/probe", "/docs", "/openapi.json"]


def passcode_ok(given):
    if INSTRUCTOR_PASSCODE == "":
        return True
    if given is None:
        return False
    return secrets.compare_digest(given.encode("utf-8"), INSTRUCTOR_PASSCODE.encode("utf-8"))


def needs_passcode(path):
    for prefix in INSTRUCTOR_PATHS:
        if path.startswith(prefix):
            return True
    if path.startswith("/aar/") and path.endswith("/summary"):
        return True
    # The review of the exercise that is running right now shows the truth, so keep it closed until it ends.
    if path == "/aar" or path.startswith("/aar/" + session_id):
        if status in ["RUNNING", "PAUSED", "FROZEN"]:
            return True
    return False


@app.middleware("http")
async def check_passcode(request: Request, call_next):
    if INSTRUCTOR_PASSCODE != "" and needs_passcode(request.url.path):
        given = request.headers.get("x-passcode") or request.cookies.get("situsim_passcode")
        if not passcode_ok(given):
            if request.url.path.startswith("/aar"):
                return HTMLResponse("<p style='font-family:sans-serif;padding:2rem'>This exercise is still running. "
                                    "Its review opens for the instructor now and for everyone once it ends. "
                                    "<a href='/instructor'>Instructor console</a></p>", status_code=401)
            return JSONResponse({"ok": False, "error": "Enter the instructor passcode first."}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
async def auth_status(request: Request):
    """Tells the console whether a passcode is needed and whether this browser already has it."""
    return {"required": INSTRUCTOR_PASSCODE != "", "ok": passcode_ok(request.cookies.get("situsim_passcode"))}


@app.post("/api/login")
async def login(request: Request, data: dict):
    if not passcode_ok(str(data.get("passcode", ""))):
        await asyncio.sleep(1)   # slows down guessing
        return JSONResponse({"ok": False, "error": "That passcode is not right."}, status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie("situsim_passcode", str(data.get("passcode", "")), max_age=7 * 24 * 3600, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https" or PUBLIC_URL.startswith("https"))
    return response


# ============================================================
# 12. WEB PAGES, WEBSOCKETS AND THE INSTRUCTOR API
# ============================================================

@app.get("/")
async def index_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/trainee")
async def trainee_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "trainee.html"))


@app.get("/instructor")
async def instructor_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "instructor.html"))


@app.get("/api/info")
async def info():
    roles = []
    for r in ROLES:
        roles.append({"role": r, "title": role_title(r), "brief": scenario["roles"][r]["brief"],
                      "name": role_names[r], "online": len(role_sockets[r]) > 0})
    rows = db.execute("SELECT session_id, scenario, created, status FROM sessions WHERE status != 'READY' "
                      "ORDER BY created DESC LIMIT 15").fetchall()
    sessions = []
    for row in rows:
        counts = db.execute("SELECT COUNT(*), SUM(kind = 'DECISION'), MAX(sim_time) FROM events WHERE session = ?",
                            (row[0],)).fetchone()
        sessions.append({"session_id": row[0], "scenario": row[1],
                         "created": time.strftime("%d %b %Y, %H:%M", time.localtime(row[2])), "status": row[3],
                         "events": counts[0] or 0, "decisions": counts[1] or 0, "length": fmt(counts[2] or 0)})
    # The scenario script as short lines, for the scrolling log on the home page
    script = []
    for inject in scenario["injects"]:
        if inject["action"] == "REPORT":
            line = inject["report_id"] + " " + inject["sender"] + ": " + inject["text"]
        elif inject["action"] == "DECISION_POINT":
            line = inject["dp_id"] + " opens: " + inject["prompt"]
        elif "note" in inject:
            line = inject["note"]
        else:
            continue
        script.append({"t": fmt(inject["at"]), "text": line})
    return {"scenario": scenario["name"], "area": scenario["area"], "disclaimer": scenario["disclaimer"],
            "session_id": session_id, "status": status, "roles": roles, "sessions": sessions,
            "script": script, "questions": [q["text"] for q in scenario["questions"]],
            "join_url": join_address()}


def send_team_message(role, report, targets, kind, via, note=""):
    """Schedule one team message to each target over the chosen channel, and charge detectability."""
    for to_role in targets:
        schedule_delivery(report, to_role, kind, from_role=role, via=via, note=note)
    if via == "net":
        cost = EMISSION_COST.get(kind, 8)
        if kind == "chat":
            cost = cost + len(report.get("text", "")) // 12
        add_emission(role, cost, kind + " by radio")
    elif via == "relay":
        add_emission("COMMS", EMISSION_COST["relay"] * len(targets), "relayed " + kind + " for " + role_title(role))
    elif via == "flare":
        add_emission(role, EMISSION_COST["flare"], "flare")


def channel_to_via(channel):
    if channel in ["relay", "runner", "flare"]:
        return channel
    return "net"


def handle_trainee_message(role, name, msg):
    """Everything a trainee can do. Returns an error message, or "" if it worked."""
    global chat_counter
    kind = msg.get("type", "")

    if kind == "decision":
        if status != "RUNNING":
            return "Decisions can only be submitted while the exercise is running."
        cited = msg.get("cited", [])
        if not isinstance(cited, list):
            cited = []
        return save_decision(role, name, msg.get("dp_id", ""), msg.get("choice"),
                             str(msg.get("rationale", ""))[:1500], cited, str(msg.get("confidence", "")))

    if kind == "belief":
        if status in ["FROZEN", "ENDED"]:
            return "You can't change your read right now."
        return save_belief(role, str(msg.get("q_id", "")), msg.get("value", 50))

    if kind == "grade":
        if status in ["FROZEN", "ENDED"]:
            return "You can't grade reports right now."
        return save_grade(role, str(msg.get("report_id", "")), str(msg.get("reliability", "")),
                          str(msg.get("credibility", "")))

    if kind == "probe_answer":
        answers = msg.get("answers", {})
        if not isinstance(answers, dict):
            answers = {}
        return answer_probe(role, name, msg.get("probe_id", ""), answers)

    if status != "RUNNING":
        if status == "FROZEN":
            return "The exercise is frozen for a probe."
        return "The exercise is not running."

    via = channel_to_via(msg.get("channel", "radio"))

    if kind == "forward":
        original = None
        for d in deliveries:
            if d["delivery_id"] == msg.get("delivery_id") and d["role"] == role and d["status"] == "DELIVERED":
                original = d
        if original is None:
            return "You can only pass on reports you have received."
        if via == "flare":
            return "Flares can only carry the fixed signals."
        report = reports[original["report_id"]]
        targets = []
        for to_role in msg.get("to_roles", []):
            if to_role in ROLES and to_role != role and to_role not in targets:
                targets.append(to_role)
        if len(targets) == 0:
            return "Pick at least one teammate."
        note = str(msg.get("note", ""))[:300]
        log_event("FORWARD", role, {"report_id": report["report_id"], "to_roles": targets, "via": via, "note": note},
                  role_title(role) + " passed " + report["report_id"] + " to " + ", ".join(targets) + " by " + channel_name(via).lower())
        send_team_message(role, report, targets, "forward", via, note)
        return ""

    if kind == "chat" or kind == "share_read":
        if via == "flare":
            return "Flares can only carry the fixed signals."
        to = msg.get("to", "ALL")
        targets = []
        if to == "ALL":
            for r in ROLES:
                if r != role:
                    targets.append(r)
        elif to in ROLES and to != role:
            targets.append(to)
        else:
            return "Pick who the message is for."
        chat_counter = chat_counter + 1
        message_id = "M" + str(chat_counter)
        if kind == "chat":
            text = str(msg.get("text", "")).strip()[:500]
            if text == "":
                return "Write a message first."
            report = {"report_id": message_id, "sender": role_title(role), "domain": "TEAM", "grid": "-",
                      "text": text, "truth": None, "source_time": sim_time, "kind": "chat", "from_role": role}
            message_kind = "chat"
        else:
            reads = {}
            parts = []
            for q in scenario.get("questions", []):
                read = beliefs[role].get(q["q_id"])
                if read is not None:
                    reads[q["q_id"]] = read["value"]
                    parts.append(q["short"] + " " + str(read["value"]) + "%")
            if len(reads) == 0:
                return "Set your read first, then share it."
            text = "My read: " + ", ".join(parts)
            report = {"report_id": message_id, "sender": role_title(role), "domain": "TEAM", "grid": "-",
                      "text": text, "truth": None, "source_time": sim_time, "kind": "read", "from_role": role,
                      "reads": reads}
            message_kind = "read"
        reports[message_id] = report
        log_event("CHAT", role, {"report": report, "to_roles": targets, "via": via},
                  role_title(role) + " to " + to + " by " + channel_name(via).lower() + ": " + text)
        send_team_message(role, report, targets, message_kind, via)
        return ""

    if kind == "flare":
        signal = str(msg.get("signal", ""))
        if signal not in scenario.get("flares", []):
            return "Pick one of the flare signals."
        chat_counter = chat_counter + 1
        message_id = "M" + str(chat_counter)
        report = {"report_id": message_id, "sender": role_title(role), "domain": "TEAM", "grid": "-",
                  "text": "Flare: " + signal, "truth": None, "source_time": sim_time, "kind": "flare", "from_role": role}
        reports[message_id] = report
        targets = [r for r in ROLES if r != role]
        log_event("CHAT", role, {"report": report, "to_roles": targets, "via": "flare"},
                  role_title(role) + " fired a flare: " + signal)
        send_team_message(role, report, targets, "flare", "flare")
        return ""

    if kind == "ack":
        target = None
        for d in deliveries:
            if d["delivery_id"] == msg.get("delivery_id") and d["role"] == role and d["status"] == "DELIVERED":
                target = d
        if target is None or target["from_role"] is None:
            return "Nothing to acknowledge."
        if target["acked_at"] is not None:
            return "Already acknowledged."
        target["acked_at"] = sim_time
        log_event("ACK", role, {"delivery_id": target["delivery_id"], "report_id": target["report_id"],
                                "to_role": target["from_role"], "via": via},
                  role_title(role) + " acknowledged " + target["report_id"])
        schedule_delivery(reports[target["report_id"]], target["from_role"], "ack", from_role=role, via=via,
                          ack_for=target["delivery_id"])
        if via == "net":
            add_emission(role, EMISSION_COST["ack"], "ack by radio")
        elif via == "relay":
            add_emission("COMMS", EMISSION_COST["relay"], "relayed ack")
        return ""

    return "Unknown action."


@app.websocket("/ws/trainee/{role}/{name}")
async def trainee_socket(websocket: WebSocket, role: str, name: str):
    role = role.upper()
    name = name.strip()[:40]
    await websocket.accept()
    if role not in ROLES:
        await websocket.send_json({"type": "toast", "text": "Unknown role."})
        await websocket.close()
        return

    role_sockets[role].append(websocket)
    role_names[role] = name
    log_event("JOIN", role, {"name": name}, name + " joined as " + role_title(role))
    await websocket.send_json(build_hello(role))
    await push_all()

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                msg = {}
            error = handle_trainee_message(role, name, msg)
            if error != "":
                await send_safely(websocket, {"type": "toast", "text": error})
            await push_all()
    except WebSocketDisconnect:
        pass
    except Exception as error:
        print("Trainee connection error:", repr(error))
    finally:
        if websocket in role_sockets[role]:
            role_sockets[role].remove(websocket)
        log_event("LEAVE", role, {"name": name}, name + " left " + role_title(role))


@app.websocket("/ws/instructor")
async def instructor_socket(websocket: WebSocket):
    await websocket.accept()
    if not passcode_ok(websocket.cookies.get("situsim_passcode") or websocket.query_params.get("passcode")):
        await websocket.close(code=4401)   # the console then asks for the passcode
        return
    instructor_sockets.append(websocket)
    await send_safely(websocket, build_instructor_state())
    try:
        while True:
            await websocket.receive_text()   # the instructor uses the API below instead
    except WebSocketDisconnect:
        pass
    except Exception as error:
        print("Instructor connection error:", repr(error))
    finally:
        if websocket in instructor_sockets:
            instructor_sockets.remove(websocket)


# ---------- instructor API (also browsable at /docs) ----------

def result(error):
    if error == "":
        return {"ok": True}
    return JSONResponse({"ok": False, "error": error}, status_code=400)


def read_duration(data):
    value = data.get("duration")
    if value in [None, "", 0, "0"]:
        return None
    try:
        value = int(float(value))
    except Exception:
        return None
    if value < 5:
        value = 5
    return value


@app.get("/api/state")
async def instructor_state():
    return build_instructor_state()


@app.post("/api/control")
async def control(data: dict):
    global status
    action = data.get("action", "")
    error = ""
    if action == "start":
        if status == "READY":
            status = "RUNNING"
            log_event("SESSION_START", None, {"config": dict(config)}, "Exercise started, seed " + str(config["seed"]))
            db.execute("UPDATE sessions SET status = ? WHERE session_id = ?", ("RUNNING", session_id))
            db.commit()
            backup("UPDATE sessions SET status = ? WHERE session_id = ?", ("RUNNING", session_id))
        else:
            error = "The exercise has already started."
    elif action == "pause":
        if status == "RUNNING":
            status = "PAUSED"
            log_event("SESSION_PAUSE", None, {}, "Paused")
        else:
            error = "The exercise is not running."
    elif action == "resume":
        if status == "PAUSED":
            status = "RUNNING"
            log_event("SESSION_RESUME", None, {}, "Resumed")
        else:
            error = "The exercise is not paused."
    elif action == "end":
        if status == "READY":
            error = "The exercise has not started."
        else:
            end_exercise()
    elif action == "reset":
        if status != "READY" and status != "ENDED":
            end_exercise()
        reset_exercise()
    else:
        error = "Unknown action."
    await push_all()
    return result(error)


@app.post("/api/config")
async def change_config(data: dict):
    changed = {}
    number_fields = ["base_delay", "random_delay", "delayed_label_after", "stale_after", "relay_delay",
                     "runner_delay", "share_window", "duration", "located_threshold", "located_penalty"]
    for field in number_fields:
        if field in data:
            value = int(float(data[field]))
            if value < 0:
                value = 0
            config[field] = value
            changed[field] = value
    for field in ["drop_chance", "conflict_chance"]:
        if field in data:
            value = float(data[field])
            if value < 0:
                value = 0.0
            if value > 0.9:
                value = 0.9
            config[field] = value
            changed[field] = value
    if "speed" in data:
        value = float(data["speed"])
        if value < 0.5:
            value = 0.5
        if value > 20:
            value = 20
        config["speed"] = value
        changed["speed"] = value
    if "show_degraded_to_trainee" in data:
        config["show_degraded_to_trainee"] = bool(data["show_degraded_to_trainee"])
        changed["show_degraded_to_trainee"] = config["show_degraded_to_trainee"]
    if "seed" in data:
        if status == "READY":
            config["seed"] = int(float(data["seed"]))
            changed["seed"] = config["seed"]
        else:
            return result("The seed can only be changed before the exercise starts.")
    if "difficulty" in data and data["difficulty"] in PRESETS:
        config["difficulty"] = data["difficulty"]
        changed["difficulty"] = data["difficulty"]
        apply_fog_level(PRESETS[data["difficulty"]])
        changed["fog_level"] = config["fog_level"]
    if "fog_level" in data:
        apply_fog_level(int(float(data["fog_level"])))
        changed["fog_level"] = config["fog_level"]
    if status != "ENDED" and config["duration"] < sim_time + 10:
        config["duration"] = sim_time + 10
    if len(changed) > 0:
        text = []
        for field in changed:
            text.append(field + "=" + str(changed[field]))
        log_event("CONFIG_CHANGE", None, {"changed": changed, "config": dict(config)}, "Settings: " + ", ".join(text))
    await push_all()
    return result("")


@app.post("/api/link")
async def change_link(data: dict):
    error = set_link(data.get("role", ""), data.get("state", ""), data.get("note", "Instructor inject"), "instructor",
                     read_duration(data))
    await push_all()
    return result(error)


@app.post("/api/sensor")
async def change_sensor(data: dict):
    duration = read_duration(data)
    until = None
    if duration is not None and data.get("state") == "OFFLINE":
        until = sim_time + duration
    error = set_sensor(data.get("sensor", ""), data.get("state", ""), until, "instructor")
    await push_all()
    return result(error)


@app.post("/api/df")
async def change_df(data: dict):
    set_df(bool(data.get("active", False)), "instructor")
    await push_all()
    return result("")


@app.post("/api/director")
async def change_director(data: dict):
    config["director"] = bool(data.get("enabled", False))
    log_event("CONFIG_CHANGE", None, {"changed": {"director": config["director"]}, "config": dict(config)},
              "Fog Director " + ("on" if config["director"] else "off"))
    await push_all()
    return result("")


@app.post("/api/inject/report")
async def inject_report(data: dict):
    if status != "RUNNING" and status != "PAUSED":
        return result("Start the exercise first.")
    text = str(data.get("text", "")).strip()
    to_roles = [r for r in data.get("to_roles", []) if r in ROLES]
    if text == "" or len(to_roles) == 0:
        return result("Write the report and pick who receives it.")
    highest = 0
    for rid in reports:
        if rid.startswith("R") and rid[1:].isdigit():
            highest = max(highest, int(rid[1:]))
    for row in scenario["injects"]:
        rid = row.get("report_id", "")
        if rid.startswith("R") and rid[1:].isdigit():
            highest = max(highest, int(rid[1:]))
    row = {
        "report_id": "R" + str(highest + 1),
        "sender": str(data.get("sender", "Instructor")).strip() or "Instructor",
        "domain": data.get("domain", "LAND"),
        "grid": str(data.get("grid", "-")).strip().upper() or "-",
        "text": text,
        "truth": bool(data.get("truth", True)),
        "to_roles": to_roles,
        "critical": bool(data.get("critical", False)),
    }
    # Optional: which key question this report bears on, so the shadow analyst can use it.
    q_id = data.get("question")
    direction = data.get("direction")
    if q_id and direction in ["yes", "no"]:
        row["evidence"] = {q_id: 1.5 if direction == "yes" else -1.5}
    log_event("INJECT", None, {"type": "report", "row": row}, "Instructor inject: report " + row["report_id"])
    create_report(row, "instructor")
    await push_all()
    return result("")


@app.post("/api/inject/conflict")
async def inject_conflict(data: dict):
    if status != "RUNNING" and status != "PAUSED":
        return result("Start the exercise first.")
    original = reports.get(data.get("report_id", ""))
    if original is None or original.get("kind") != "sensor":
        return result("Pick a report to contradict.")
    to_roles = [r for r in data.get("to_roles", []) if r in ROLES]
    if len(to_roles) == 0:
        return result("Pick who receives the contradicting report.")
    text = str(data.get("text", "")).strip()
    if text == "":
        text = original.get("contradiction", {}).get("text", "")
    if text == "":
        text = "No sign of what " + original.get("sender", "the earlier report") + " reported"
        if original.get("grid", "-") != "-":
            text = text + " at " + original["grid"]
        text = text + "."
    log_event("INJECT", None, {"type": "conflict", "report_id": original["report_id"], "to_roles": to_roles},
              "Instructor inject: contradiction of " + original["report_id"])
    for role in to_roles:
        delay = int(config["base_delay"]) + 3
        make_conflict_report(original, role, delay, "instructor", text=text,
                             sender=str(data.get("sender", "")).strip() or None)
    await push_all()
    return result("")


@app.post("/api/inject/decision")
async def inject_decision(data: dict):
    global instructor_dp_counter
    if status != "RUNNING" and status != "PAUSED":
        return result("Start the exercise first.")
    prompt = str(data.get("prompt", "")).strip()
    labels = [str(x).strip() for x in data.get("options", []) if str(x).strip() != ""]
    roles = [r for r in data.get("roles", []) if r in ROLES]
    if prompt == "" or len(labels) < 2 or len(roles) == 0:
        return result("Write the question, at least two options, and pick who decides.")
    instructor_dp_counter = instructor_dp_counter + 1
    options = []
    for i in range(len(labels)):
        options.append({"id": "OPT" + str(i + 1), "label": labels[i]})
    row = {"dp_id": "IDP" + str(instructor_dp_counter), "roles": roles, "window": int(data.get("window", 90)),
           "team": len(roles) > 1 and "GTL" in roles, "prompt": prompt, "options": options,
           "relevant_reports": [], "verify_options": []}
    log_event("INJECT", None, {"type": "decision", "row": row}, "Instructor inject: decision point " + row["dp_id"])
    open_decision_point(row, "instructor")
    await push_all()
    return result("")


@app.post("/api/probe/start")
async def probe_start(data: dict):
    error = start_probe(data.get("probe_id", ""))
    await push_all()
    return result(error)


@app.post("/api/probe/end")
async def probe_end():
    error = end_probe()
    await push_all()
    return result(error)


# ---------- After-Action Review ----------

def load_events(sid):
    rows = db.execute("SELECT id, sim_time, wall_time, kind, role, data FROM events WHERE session = ? ORDER BY id",
                      (sid,)).fetchall()
    events = []
    for row in rows:
        events.append({"id": row[0], "sim_time": row[1], "wall_time": row[2], "kind": row[3], "role": row[4],
                       "data": json.loads(row[5])})
    return events


@app.get("/aar")
async def latest_aar():
    return RedirectResponse("/aar/" + session_id)


@app.get("/aar/{sid}")
async def aar_page(request: Request, sid: str):
    events = load_events(sid)
    if len(events) == 0:
        return JSONResponse({"error": "No session called " + sid}, status_code=404)
    report = aar.build_aar(sid, events)
    return templates.TemplateResponse(request, "aar.html", {"r": report, "llm": aar.llm_provider(),
                                                            "live": sid == session_id and status != "ENDED",
                                                            "standalone": False})


@app.get("/aar/{sid}/download")
async def aar_download(request: Request, sid: str):
    """The whole review as one HTML file that opens offline, with styles and scripts inside it."""
    events = load_events(sid)
    if len(events) == 0:
        return JSONResponse({"error": "No session called " + sid}, status_code=404)
    report = aar.build_aar(sid, events)
    inline = {}
    for name in ["style.css", "aar.css", "aar.js"]:
        with open(os.path.join(BASE_DIR, "static", name), "r", encoding="utf-8") as f:
            inline[name] = f.read()
    # Put the fonts inside the file too, so the downloaded review looks the same offline.
    fonts_dir = os.path.join(BASE_DIR, "static", "fonts")
    for font in os.listdir(fonts_dir):
        if font.endswith(".woff2"):
            with open(os.path.join(fonts_dir, font), "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            inline["style.css"] = inline["style.css"].replace('url("fonts/' + font + '")',
                                                              'url("data:font/woff2;base64,' + encoded + '")')
    page = templates.get_template("aar.html").render({"r": report, "llm": None, "live": False,
                                                      "standalone": True, "inline": inline})
    headers = {"Content-Disposition": "attachment; filename=situsim-review-" + sid + ".html"}
    return HTMLResponse(page, headers=headers)


@app.get("/aar/{sid}/json")
async def aar_json(sid: str):
    events = load_events(sid)
    if len(events) == 0:
        return JSONResponse({"error": "No session called " + sid}, status_code=404)
    report = aar.build_aar(sid, events)
    export = {"aar": report, "events": events}
    headers = {"Content-Disposition": "attachment; filename=situsim-aar-" + sid + ".json"}
    return JSONResponse(export, headers=headers)


@app.post("/aar/{sid}/summary")
async def aar_summary(sid: str):
    events = load_events(sid)
    if len(events) == 0:
        return JSONResponse({"ok": False, "reason": "No such session."}, status_code=404)
    report = aar.build_aar(sid, events)
    # The AI call is slow, so run it in a thread and keep the clock ticking.
    answer = await asyncio.to_thread(aar.llm_summary, report)
    return answer


if __name__ == "__main__":
    # One worker only: all exercise state lives in this one process.
    uvicorn.run(app, host="0.0.0.0", port=PORT)
