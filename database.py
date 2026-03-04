"""
database.py  —  SQLite layer for SimLab Manager v7
All tables are created on first run; no external setup required.

v7 changes:
  - bcrypt replaces SHA-256 for password hashing
  - uuid4 replaces count-based ID generation (race condition fix)
  - slot_booking_count counts pending+approved (not just approved)
  - Soft-delete users (active=0) instead of hard DELETE
  - Notification cleanup: keep last 200 per user
  - File storage moved out of SQLite BLOB into filesystem / env-configurable path
  - Email send failures written to audit log
"""
import sqlite3
import os
import bcrypt
from uuid import uuid4
from datetime import datetime

DB_PATH      = os.environ.get("SIMLAB_DB_PATH", "simlab.db")
UPLOAD_DIR   = os.environ.get("SIMLAB_UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ── Password helpers ──────────────────────────────────────────────────────────
def hash_pw(pw: str) -> str:
    """bcrypt hash. Always returns a str for storage."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()

def check_pw(pw: str, hashed: str) -> bool:
    """Verify password against stored hash. Handles legacy SHA-256 hashes."""
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        # Legacy SHA-256 fallback (for existing accounts before migration)
        import hashlib
        return hashlib.sha256(pw.encode()).hexdigest() == hashed

# ── ID helpers ────────────────────────────────────────────────────────────────
def _uid(prefix="", length=8) -> str:
    """Generate a short collision-free ID using uuid4."""
    return f"{prefix}{uuid4().hex[:length].upper()}"

# ── Schema ────────────────────────────────────────────────────────────────────
def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            email           TEXT,
            password        TEXT NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('admin','lecturer','student')),
            security_q      TEXT,
            security_a      TEXT,
            active          INTEGER DEFAULT 1,
            failed_attempts INTEGER DEFAULT 0,
            locked_until    TEXT DEFAULT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
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
            cancelled    INTEGER DEFAULT 0,
            cancel_reason TEXT DEFAULT '',
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
            late          INTEGER DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS assignments (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            course       TEXT NOT NULL,
            session_id   TEXT DEFAULT '',
            created_by   TEXT NOT NULL,
            lecturer     TEXT NOT NULL,
            deadline     TEXT NOT NULL,
            max_score    REAL DEFAULT 100,
            active       INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id             TEXT PRIMARY KEY,
            assignment_id  TEXT NOT NULL,
            student_id     TEXT NOT NULL,
            student_name   TEXT NOT NULL,
            filename       TEXT DEFAULT '',
            file_path      TEXT DEFAULT '',
            file_type      TEXT DEFAULT '',
            submitted_at   TEXT DEFAULT (datetime('now')),
            grade          REAL DEFAULT NULL,
            feedback       TEXT DEFAULT '',
            graded_at      TEXT DEFAULT NULL,
            graded_by      TEXT DEFAULT '',
            FOREIGN KEY(assignment_id) REFERENCES assignments(id),
            FOREIGN KEY(student_id)    REFERENCES users(id)
        );
        """)
        # Safe ALTER TABLE additions for databases upgraded from earlier versions
        _alters = [
            "ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN failed_attempts INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN locked_until TEXT DEFAULT NULL",
            "ALTER TABLE attendance ADD COLUMN late INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN cancelled INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN cancel_reason TEXT DEFAULT ''",
            # v7: file_path replaces file_data BLOB
            "ALTER TABLE submissions ADD COLUMN file_path TEXT DEFAULT ''",
        ]
        for sql in _alters:
            try:
                conn.execute(sql)
            except Exception:
                pass

# ── Seeds ─────────────────────────────────────────────────────────────────────
def seed_defaults():
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            conn.executemany(
                "INSERT OR IGNORE INTO users(id,name,email,password,role,security_q,security_a) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    ("ADMIN001", "Lab Admin",   "admin@lab.edu",  hash_pw("admin123"),
                     "admin",    "What is your pet's name?",         hash_pw("buddy")),
                    ("LEC001",   "Dr. Mensah",  "mensah@lab.edu", hash_pw("lec123"),
                     "lecturer", "What city were you born in?",      hash_pw("accra")),
                    # UENR-format student ID
                    ("UEB0500001", "Kofi Asante", "kofi@lab.edu", hash_pw("stu123"),
                     "student", "What is your mother's maiden name?", hash_pw("boateng")),
                ]
            )
        if not conn.execute("SELECT 1 FROM workstations LIMIT 1").fetchone():
            conn.executemany(
                "INSERT OR IGNORE INTO workstations(id,label,status,notes) VALUES(?,?,?,?)",
                [(i, f"PC-{i:02d}", "available", "") for i in range(1, 21)]
            )

# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════
def get_user(user_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
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
                "INSERT INTO users(id,name,email,password,role,security_q,security_a) "
                "VALUES(?,?,?,?,?,?,?)",
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

def get_active_user_by_id_pw(user_id: str, pw_raw: str):
    """Return user dict, 'locked', or None. Accepts raw password (not hash)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        if not row:
            return None
        u = dict(row)
        # Check lock before verifying password (avoids timing side-channel)
        if u.get("locked_until"):
            try:
                if datetime.now() < datetime.fromisoformat(u["locked_until"]):
                    return "locked"
            except Exception:
                pass
        if not check_pw(pw_raw, u["password"]):
            return None
        return u

def record_failed_login(uid):
    from datetime import timedelta
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            return
        attempts = (row[0] or 0) + 1
        locked_until = None
        if attempts >= 5:
            locked_until = str(datetime.now() + timedelta(minutes=15))
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
    """Soft-delete: deactivates account and anonymises PII. Does not remove rows."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active=0, name='[Deleted]', email='', password='' WHERE id=?",
            (uid,)
        )

# ══════════════════════════════════════════════════════════════════════════════
# WORKSTATIONS
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
# SESSIONS
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
    sessions = get_sessions_on_date(date_str)
    for s in sessions:
        if exclude_id and s["id"] == exclude_id:
            continue
        if s.get("cancelled"):
            continue
        try:
            es = datetime.strptime(s["start_time"], "%H:%M")
            ee = datetime.strptime(s["end_time"],   "%H:%M")
            ns = datetime.strptime(start,            "%H:%M")
            ne = datetime.strptime(end,              "%H:%M")
            if ns < ee and ne > es:
                return s
        except Exception:
            pass
    return None

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
# BOOKINGS
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
    """Count pending+approved bookings for a slot (what matters for capacity)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE date=? AND time_slot=? "
            "AND status IN ('pending','approved')",
            (date_str, time_slot)
        ).fetchone()[0]

def auto_reject_expired():
    from datetime import timedelta
    now = datetime.now()
    bookings = get_all_bookings()
    for b in bookings:
        if b["status"] != "pending":
            continue
        try:
            slot_start = b["time_slot"].split("–")[0].strip()
            slot_dt = datetime.strptime(f"{b['date']} {slot_start}", "%Y-%m-%d %H:%M")
            if now > slot_dt - timedelta(hours=1):
                update_booking_status(b["id"], "rejected",
                                      "Auto-rejected: not approved before cutoff")
                add_notification(b["student_id"],
                    f"Your booking for {b['date']} {b['time_slot']} was auto-rejected "
                    f"(not approved in time).", "error")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
# ATTENDANCE
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

def is_late_for_session(session_id, checkin_time_str):
    s = get_session_by_id(session_id)
    if not s:
        return False
    try:
        start   = datetime.strptime(s["start_time"],   "%H:%M")
        checkin = datetime.strptime(checkin_time_str,  "%H:%M")
        return (checkin - start).total_seconds() > 600
    except Exception:
        return False

def get_attendance_for_session(session_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT a.*, u.email FROM attendance a
               LEFT JOIN users u ON a.student_id = u.id
               WHERE a.reference_id=? ORDER BY a.time""",
            (session_id,)).fetchall()]

# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS  (capped at 200 per user)
# ══════════════════════════════════════════════════════════════════════════════
def get_notifications(user_id, limit=100):
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute(
                    "SELECT * FROM notifications WHERE user_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit)).fetchall()]

def add_notification(user_id, message, ntype="info"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notifications(id,user_id,message,type) VALUES(?,?,?,?)",
            (_uid("N"), user_id, message, ntype)
        )
        # Prune: keep only the 200 most recent per user
        conn.execute(
            """DELETE FROM notifications WHERE user_id=? AND id NOT IN (
               SELECT id FROM notifications WHERE user_id=?
               ORDER BY created_at DESC LIMIT 200)""",
            (user_id, user_id)
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
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════
def add_audit(actor, action, detail=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log(id,actor,action,detail) VALUES(?,?,?,?)",
            (_uid("A"), actor, action, detail)
        )

def get_audit_log(limit=200, actor=None, action=None):
    with get_conn() as conn:
        sql    = "SELECT * FROM audit_log"
        params = []
        clauses = []
        if actor:
            clauses.append("actor=?"); params.append(actor)
        if action:
            clauses.append("action LIKE ?"); params.append(f"%{action}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

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
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO announcements(id,title,body,author_id,author,pinned) VALUES(?,?,?,?,?,?)",
            (_uid("ANN"), title, body, author_id, author, 1 if pinned else 0)
        )

def deactivate_announcement(ann_id):
    with get_conn() as conn:
        conn.execute("UPDATE announcements SET active=0 WHERE id=?", (ann_id,))

def toggle_pin(ann_id, pinned):
    with get_conn() as conn:
        conn.execute("UPDATE announcements SET pinned=? WHERE id=?", (1 if pinned else 0, ann_id))

# ══════════════════════════════════════════════════════════════════════════════
# BLACKOUT DATES
# ══════════════════════════════════════════════════════════════════════════════
def get_blackout_dates():
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute("SELECT * FROM blackout_dates ORDER BY date").fetchall()]

def add_blackout_date(date_str, reason):
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO blackout_dates(id,date,reason) VALUES(?,?,?)",
                (_uid("BD"), date_str, reason)
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
# ASSIGNMENTS
# ══════════════════════════════════════════════════════════════════════════════
def get_all_assignments(active_only=False):
    with get_conn() as conn:
        sql = "SELECT * FROM assignments"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY deadline ASC"
        return [dict(r) for r in conn.execute(sql).fetchall()]

def get_assignment(aid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None

def create_assignment(aid, title, description, course, session_id,
                      created_by, lecturer, deadline, max_score):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO assignments(id,title,description,course,session_id,
               created_by,lecturer,deadline,max_score) VALUES(?,?,?,?,?,?,?,?,?)""",
            (aid, title, description, course, session_id,
             created_by, lecturer, deadline, max_score)
        )

def deactivate_assignment(aid):
    with get_conn() as conn:
        conn.execute("UPDATE assignments SET active=0 WHERE id=?", (aid,))

def assignment_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]

# ══════════════════════════════════════════════════════════════════════════════
# SUBMISSIONS  (files stored on filesystem, not in BLOB)
# ══════════════════════════════════════════════════════════════════════════════
def _submission_path(assignment_id, student_id, filename):
    """Return a safe filesystem path for a submission file."""
    folder = os.path.join(UPLOAD_DIR, assignment_id, student_id)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename)

def get_submissions_for_assignment(assignment_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id=? ORDER BY submitted_at",
            (assignment_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["file_data"] = _read_file(d.get("file_path",""))
        result.append(d)
    return result

def get_submissions_for_student(student_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, a.title as assignment_title, a.course, a.deadline, a.max_score
               FROM submissions s
               JOIN assignments a ON s.assignment_id = a.id
               WHERE s.student_id=? ORDER BY s.submitted_at DESC""",
            (student_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["file_data"] = _read_file(d.get("file_path",""))
        result.append(d)
    return result

def get_submission(assignment_id, student_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id=? AND student_id=?",
            (assignment_id, student_id)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["file_data"] = _read_file(d.get("file_path",""))
    return d

def _read_file(path):
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None

def create_submission(sub_id, assignment_id, student_id, student_name,
                      filename, file_bytes, file_type):
    path = _submission_path(assignment_id, student_id, filename)
    with open(path, "wb") as f:
        f.write(file_bytes)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO submissions(id,assignment_id,student_id,student_name,
               filename,file_path,file_type) VALUES(?,?,?,?,?,?,?)""",
            (sub_id, assignment_id, student_id, student_name, filename, path, file_type)
        )

def update_submission_file(assignment_id, student_id, filename, file_bytes, file_type):
    path = _submission_path(assignment_id, student_id, filename)
    with open(path, "wb") as f:
        f.write(file_bytes)
    with get_conn() as conn:
        conn.execute(
            """UPDATE submissions SET filename=?,file_path=?,file_type=?,
               submitted_at=datetime('now'), grade=NULL, feedback='', graded_at=NULL
               WHERE assignment_id=? AND student_id=?""",
            (filename, path, file_type, assignment_id, student_id)
        )

def grade_submission(assignment_id, student_id, grade, feedback, graded_by):
    with get_conn() as conn:
        conn.execute(
            """UPDATE submissions SET grade=?,feedback=?,graded_at=datetime('now'),
               graded_by=? WHERE assignment_id=? AND student_id=?""",
            (grade, feedback, graded_by, assignment_id, student_id)
        )

def submission_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]

# ── Bootstrap ──────────────────────────────────────────────────────────────────
init_db()
seed_defaults()
