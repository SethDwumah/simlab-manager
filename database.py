"""
database.py  —  SQLite layer for SimLab Manager
All tables are created on first run; no external setup required.
"""
import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.environ.get("SIMLAB_DB_PATH", "simlab.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ── Schema ────────────────────────────────────────────────────────────────────
def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT,
            password    TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('admin','lecturer','student')),
            security_q  TEXT,
            security_a  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS workstations (
            id      INTEGER PRIMARY KEY,
            label   TEXT NOT NULL UNIQUE,
            status  TEXT NOT NULL DEFAULT 'available'
                        CHECK(status IN ('available','in-use','maintenance')),
            notes   TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,
            course       TEXT NOT NULL,
            lecturer     TEXT NOT NULL,
            date         TEXT NOT NULL,
            start_time   TEXT NOT NULL,
            end_time     TEXT NOT NULL,
            max_students INTEGER NOT NULL DEFAULT 15,
            notes        TEXT DEFAULT '',
            created_by   TEXT,
            recurring    INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id               TEXT PRIMARY KEY,
            student_id       TEXT NOT NULL,
            student_name     TEXT NOT NULL,
            date             TEXT NOT NULL,
            time_slot        TEXT NOT NULL,
            purpose          TEXT DEFAULT '',
            status           TEXT NOT NULL DEFAULT 'pending'
                                 CHECK(status IN ('pending','approved','rejected')),
            rejection_reason TEXT DEFAULT '',
            created_at       TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(student_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id            TEXT PRIMARY KEY,
            student_id    TEXT NOT NULL,
            student_name  TEXT NOT NULL,
            type          TEXT NOT NULL,
            reference_id  TEXT DEFAULT '',
            workstation   TEXT DEFAULT '',
            date          TEXT NOT NULL,
            time          TEXT NOT NULL,
            status        TEXT DEFAULT 'present',
            checked_out   INTEGER DEFAULT 0,
            checkout_time TEXT DEFAULT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(student_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            message    TEXT NOT NULL,
            type       TEXT DEFAULT 'info',
            read       INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id         TEXT PRIMARY KEY,
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            detail     TEXT DEFAULT '',
            timestamp  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            body       TEXT NOT NULL,
            author_id  TEXT NOT NULL,
            author     TEXT NOT NULL,
            pinned     INTEGER DEFAULT 0,
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS blackout_dates (
            id     TEXT PRIMARY KEY,
            date   TEXT NOT NULL UNIQUE,
            reason TEXT DEFAULT ''
        );

        -- new columns added via ALTER (safe — ignored if already exist)
        """)
        # ALTER TABLE additions — each wrapped so failures are silent
        _alters = [
            "ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN failed_attempts INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN locked_until TEXT DEFAULT NULL",
            "ALTER TABLE attendance ADD COLUMN late INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN cancelled INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN cancel_reason TEXT DEFAULT ''",
        ]
        for sql in _alters:
            try:
                conn.execute(sql)
            except Exception:
                pass

# ── Seeds ─────────────────────────────────────────────────────────────────────
def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def seed_defaults():
    with get_conn() as conn:
        # users
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            conn.executemany(
                "INSERT OR IGNORE INTO users(id,name,email,password,role,security_q,security_a) VALUES(?,?,?,?,?,?,?)",
                [
                    ("ADMIN001","Lab Admin","admin@lab.edu",hash_pw("admin123"),
                     "admin","What is your pet's name?",hash_pw("buddy")),
                    ("LEC001","Dr. Mensah","mensah@lab.edu",hash_pw("lec123"),
                     "lecturer","What city were you born in?",hash_pw("accra")),
                    ("STU001","Kofi Asante","kofi@lab.edu",hash_pw("stu123"),
                     "student","What is your mother's maiden name?",hash_pw("boateng")),
                ]
            )
        # workstations
        if not conn.execute("SELECT 1 FROM workstations LIMIT 1").fetchone():
            conn.executemany(
                "INSERT OR IGNORE INTO workstations(id,label,status,notes) VALUES(?,?,?,?)",
                [(i, f"PC-{i:02d}", "available", "") for i in range(1, 21)]
            )

# ══════════════════════════════════════════════════════════════════════════════
# USER QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_user(user_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

def get_user_by_id_pw(user_id: str, pw_hash: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND password=? AND active=1", (user_id, pw_hash)
        ).fetchone()
        return dict(row) if row else None

def get_all_users(role=None):
    with get_conn() as conn:
        if role:
            rows = conn.execute("SELECT * FROM users WHERE role=? ORDER BY name", (role,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
        return [dict(r) for r in rows]

def create_user(uid, name, email, pw_hash, role, sec_q, sec_a_hash):
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users(id,name,email,password,role,security_q,security_a) VALUES(?,?,?,?,?,?,?)",
                (uid, name, email, pw_hash, role, sec_q, sec_a_hash)
            )
            return True
        except sqlite3.IntegrityError:
            return False

def update_user(uid, name, email, sec_q, sec_a_hash=None):
    with get_conn() as conn:
        if sec_a_hash:
            conn.execute(
                "UPDATE users SET name=?,email=?,security_q=?,security_a=? WHERE id=?",
                (name, email, sec_q, sec_a_hash, uid)
            )
        else:
            conn.execute(
                "UPDATE users SET name=?,email=?,security_q=? WHERE id=?",
                (name, email, sec_q, uid)
            )

def update_password(uid, pw_hash):
    with get_conn() as conn:
        conn.execute("UPDATE users SET password=? WHERE id=?", (pw_hash, uid))

def user_exists(uid):
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is not None

# ══════════════════════════════════════════════════════════════════════════════
# WORKSTATION QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_all_workstations():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM workstations ORDER BY id").fetchall()]

def update_workstation(ws_id, status, notes):
    with get_conn() as conn:
        conn.execute("UPDATE workstations SET status=?,notes=? WHERE id=?", (status, notes, ws_id))

def get_available_workstations():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM workstations WHERE status='available' ORDER BY id").fetchall()]

def set_workstation_status(label, status):
    with get_conn() as conn:
        conn.execute("UPDATE workstations SET status=? WHERE label=?", (status, label))

# ══════════════════════════════════════════════════════════════════════════════
# SESSION QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_all_sessions():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM sessions ORDER BY date DESC, start_time").fetchall()]

def get_sessions_on_date(d: str):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM sessions WHERE date=?", (d,)).fetchall()]

def create_session(sid, course, lecturer, date, start_time, end_time,
                   max_students, notes, created_by, recurring=False):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sessions(id,course,lecturer,date,start_time,end_time,
               max_students,notes,created_by,recurring) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, course, lecturer, date, start_time, end_time,
             max_students, notes, created_by, 1 if recurring else 0)
        )

def session_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

def sessions_overlap(date_str, start, end, exclude_id=None):
    from datetime import datetime as dt
    sessions = get_sessions_on_date(date_str)
    for s in sessions:
        if exclude_id and s["id"] == exclude_id:
            continue
        try:
            es = dt.strptime(s["start_time"], "%H:%M")
            ee = dt.strptime(s["end_time"],   "%H:%M")
            ns = dt.strptime(start,            "%H:%M")
            ne = dt.strptime(end,              "%H:%M")
            if ns < ee and ne > es:
                return s
        except Exception:
            pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
# BOOKING QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_all_bookings():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM bookings ORDER BY date DESC").fetchall()]

def get_bookings_for_student(student_id):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM bookings WHERE student_id=? ORDER BY date DESC",
                             (student_id,)).fetchall()]

def create_booking(bid, student_id, student_name, date, time_slot, purpose):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bookings(id,student_id,student_name,date,time_slot,purpose,status)
               VALUES(?,?,?,?,?,?,'pending')""",
            (bid, student_id, student_name, date, time_slot, purpose)
        )

def update_booking_status(bid, status, reason=""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE bookings SET status=?,rejection_reason=? WHERE id=?",
            (status, reason, bid)
        )

def booking_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

def slot_booking_count(date_str, time_slot):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE date=? AND time_slot=? AND status='approved'",
            (date_str, time_slot)
        ).fetchone()[0]

def auto_reject_expired():
    from datetime import datetime as dt, timedelta
    now = dt.now()
    bookings = get_all_bookings()
    for b in bookings:
        if b["status"] != "pending":
            continue
        try:
            slot_start = b["time_slot"].split("–")[0].strip()
            slot_dt = dt.strptime(f"{b['date']} {slot_start}", "%Y-%m-%d %H:%M")
            if now > slot_dt - timedelta(hours=1):
                update_booking_status(b["id"], "rejected",
                                      "Auto-rejected: not approved before cutoff")
                add_notification(b["student_id"],
                    f"Your booking for {b['date']} {b['time_slot']} was auto-rejected "
                    f"(not approved in time).", "error")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
# ATTENDANCE QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_all_attendance():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM attendance ORDER BY date DESC, time DESC").fetchall()]

def get_attendance_for_student(student_id):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY date DESC",
                             (student_id,)).fetchall()]

def get_active_checkins(date_str):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM attendance WHERE date=? AND checked_out=0",
                             (date_str,)).fetchall()]

def create_attendance(aid, student_id, student_name, atype, reference_id,
                      workstation, date_str, time_str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO attendance(id,student_id,student_name,type,reference_id,
               workstation,date,time,status,checked_out)
               VALUES(?,?,?,?,?,?,?,?,'present',0)""",
            (aid, student_id, student_name, atype, reference_id, workstation, date_str, time_str)
        )

def checkout_attendance(att_id, checkout_time):
    with get_conn() as conn:
        conn.execute(
            "UPDATE attendance SET checked_out=1,checkout_time=? WHERE id=?",
            (checkout_time, att_id)
        )

def attendance_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]

def student_already_checked_in(student_id, date_str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM attendance WHERE student_id=? AND date=? AND checked_out=0",
            (student_id, date_str)
        ).fetchone() is not None

# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def get_notifications(user_id):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
                             (user_id,)).fetchall()]

def add_notification(user_id, message, ntype="info"):
    from uuid import uuid4
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notifications(id,user_id,message,type) VALUES(?,?,?,?)",
            (f"N{uuid4().hex[:8]}", user_id, message, ntype)
        )

def mark_notification_read(notif_id):
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET read=1 WHERE id=?", (notif_id,))

def mark_all_read(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user_id,))

def unread_count(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (user_id,)
        ).fetchone()[0]

# ══════════════════════════════════════════════════════════════════════════════
# AUDIT QUERIES
# ══════════════════════════════════════════════════════════════════════════════
def add_audit(actor, action, detail=""):
    from uuid import uuid4
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log(id,actor,action,detail) VALUES(?,?,?,?)",
            (f"A{uuid4().hex[:8]}", actor, action, detail)
        )

# ══════════════════════════════════════════════════════════════════════════════
# USER — ACCOUNT STATUS / LOGIN SECURITY
# ══════════════════════════════════════════════════════════════════════════════
def get_active_user_by_id_pw(user_id: str, pw_hash: str):
    """Return user only if active and not locked."""
    from datetime import datetime as dt
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND password=? AND active=1", (user_id, pw_hash)
        ).fetchone()
        if not row:
            return None
        u = dict(row)
        if u.get("locked_until"):
            try:
                if dt.now() < dt.fromisoformat(u["locked_until"]):
                    return "locked"
            except Exception:
                pass
        return u

def record_failed_login(uid):
    """Increment failed attempts; lock for 15 min after 5 failures."""
    from datetime import datetime as dt, timedelta
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            return
        attempts = (row[0] or 0) + 1
        locked_until = None
        if attempts >= 5:
            locked_until = str(dt.now() + timedelta(minutes=15))
            attempts = 0
        conn.execute(
            "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
            (attempts, locked_until, uid)
        )

def reset_failed_login(uid):
    with get_conn() as conn:
        conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))

def set_user_active(uid, active: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))

def delete_user(uid):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))

def get_session_by_id(sid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

def update_session(sid, course, lecturer, date_str, start_time, end_time, max_students, notes):
    with get_conn() as conn:
        conn.execute(
            """UPDATE sessions SET course=?,lecturer=?,date=?,start_time=?,
               end_time=?,max_students=?,notes=? WHERE id=?""",
            (course, lecturer, date_str, start_time, end_time, max_students, notes, sid)
        )

def cancel_session(sid, reason):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET cancelled=1, cancel_reason=? WHERE id=?", (reason, sid)
        )

# ══════════════════════════════════════════════════════════════════════════════
# ATTENDANCE — LATE FLAG + LATE-CHECK HELPER
# ══════════════════════════════════════════════════════════════════════════════
def create_attendance_v2(aid, student_id, student_name, atype, reference_id,
                          workstation, date_str, time_str, late=False):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO attendance(id,student_id,student_name,type,reference_id,
               workstation,date,time,status,checked_out,late)
               VALUES(?,?,?,?,?,?,?,?,'present',0,?)""",
            (aid, student_id, student_name, atype, reference_id,
             workstation, date_str, time_str, 1 if late else 0)
        )

def is_late_for_session(session_id, checkin_time_str):
    """Return True if checkin_time_str is more than 10 min after session start."""
    from datetime import datetime as dt
    s = get_session_by_id(session_id)
    if not s:
        return False
    try:
        start = dt.strptime(s["start_time"], "%H:%M")
        checkin = dt.strptime(checkin_time_str, "%H:%M")
        return (checkin - start).total_seconds() > 600
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════════════════
# BLACKOUT DATES
# ══════════════════════════════════════════════════════════════════════════════
def get_blackout_dates():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM blackout_dates ORDER BY date").fetchall()]

def add_blackout_date(date_str, reason):
    from uuid import uuid4
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO blackout_dates(id,date,reason) VALUES(?,?,?)",
                (f"BD{uuid4().hex[:6]}", date_str, reason)
            )
            return True
        except Exception:
            return False

def remove_blackout_date(date_str):
    with get_conn() as conn:
        conn.execute("DELETE FROM blackout_dates WHERE date=?", (date_str,))

def is_blackout(date_str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM blackout_dates WHERE date=?", (date_str,)
        ).fetchone() is not None

# ══════════════════════════════════════════════════════════════════════════════
# ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════════════
def get_announcements(active_only=True):
    with get_conn() as conn:
        sql = "SELECT * FROM announcements"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY pinned DESC, created_at DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]

def create_announcement(title, body, author_id, author, pinned=False):
    from uuid import uuid4
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO announcements(id,title,body,author_id,author,pinned) VALUES(?,?,?,?,?,?)",
            (f"ANN{uuid4().hex[:6]}", title, body, author_id, author, 1 if pinned else 0)
        )

def deactivate_announcement(ann_id):
    with get_conn() as conn:
        conn.execute("UPDATE announcements SET active=0 WHERE id=?", (ann_id,))

def toggle_pin(ann_id, pinned):
    with get_conn() as conn:
        conn.execute("UPDATE announcements SET pinned=? WHERE id=?", (1 if pinned else 0, ann_id))

# ── Bootstrap ─────────────────────────────────────────────────────────────────
init_db()
seed_defaults()
