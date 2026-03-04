"""
SimLab Manager v6  —  app.py
Cleaned up and bug-fixed release. All roles properly scoped,
dead code removed, tab-return bugs fixed, auto_reject consolidated.
"""
import os, io, smtplib, hashlib, re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta

import streamlit as st
import pandas as pd
import plotly.express as px
import qrcode

import database as db

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="SimLab Manager", page_icon="🖥️",
                   layout="wide", initial_sidebar_state="expanded")

# ── Constants ─────────────────────────────────────────────────────────────────
ADMIN_CODE       = os.environ.get("SIMLAB_ADMIN_CODE", "SIMLAB2024")
GMAIL_USER       = os.environ.get("SIMLAB_GMAIL_USER", "dwumahseth444@gmail.com")
GMAIL_APP_PW     = os.environ.get("SIMLAB_GMAIL_APP_PW", "hxxj zpat seud jukj")
SESSION_TIMEOUT  = 30          # minutes
TIME_SLOTS       = ["08:00–09:00","09:00–10:00","10:00–11:00","11:00–12:00",
                    "13:00–14:00","14:00–15:00","15:00–16:00","16:00–17:00"]
MAX_PER_SLOT     = 5
MAX_BOOK_DAYS    = 2
SECURITY_QS      = ["What is your pet's name?","What city were you born in?",
                    "What is your mother's maiden name?","What was your first school's name?"]

# UENR Student ID format: starts with UEB05, e.g. UEB0501721
STUDENT_ID_PATTERN = re.compile(r'^UEB05\d+$', re.IGNORECASE)

def validate_student_id(uid: str) -> tuple[bool, str]:
    """Return (valid, error_message). Only enforced for student role."""
    uid = uid.strip()
    if not uid:
        return False, "ID cannot be empty."
    if not re.match(r'^[A-Za-z0-9]+$', uid):
        return False, "ID must contain only letters and numbers (no spaces or symbols)."
    return True, ""

def validate_student_role_id(uid: str) -> tuple[bool, str]:
    """Strict UENR format check applied only to student IDs."""
    ok, err = validate_student_id(uid)
    if not ok:
        return False, err
    if not STUDENT_ID_PATTERN.match(uid):
        return False, "Student ID must follow UENR format: UEB05XXXXXXX (e.g. UEB0501721)."
    return True, ""

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .main-header{background:linear-gradient(135deg,#1e3a5f,#2d6a9f);padding:1.4rem 2rem;
    border-radius:12px;color:white;margin-bottom:1.4rem;}
  .main-header h1{margin:0;font-size:1.75rem;}
  .main-header p{margin:.25rem 0 0;opacity:.85;font-size:.92rem;}
  .metric-card{background:white;border:1px solid #e0e6ed;border-radius:10px;
    padding:1.1rem 1.4rem;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06);}
  .metric-card .val{font-size:1.9rem;font-weight:700;color:#1e3a5f;}
  .metric-card .lbl{font-size:.82rem;color:#666;margin-top:.15rem;}
  .nb{padding:.55rem 1rem;border-radius:8px;margin-bottom:.45rem;font-size:.88rem;}
  .nb-info{background:#cce5ff;color:#004085;}
  .nb-success{background:#d4edda;color:#155724;}
  .nb-warning{background:#fff3cd;color:#856404;}
  .nb-error{background:#f8d7da;color:#721c24;}
  .ann-card{background:white;border-left:4px solid #2d6a9f;border-radius:8px;
    padding:1rem 1.2rem;margin-bottom:.8rem;box-shadow:0 1px 6px rgba(0,0,0,.07);}
  .ann-pinned{border-left-color:#f0ad4e;}
  .late-badge{background:#fff3cd;color:#856404;padding:.15rem .5rem;
    border-radius:12px;font-size:.75rem;font-weight:600;}
  section[data-testid="stSidebar"]{background:#1e3a5f!important;}
  section[data-testid="stSidebar"] *{color:white!important;}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
for k, v in [("logged_in",False),("user",None),("last_active",None),
             ("ann_popup_shown",False),("sel_book_date",None),("sel_book_slot",None)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════════════
# SESSION TIMEOUT
# ══════════════════════════════════════════════════════════════════════════════
def check_timeout():
    if not st.session_state.logged_in:
        return
    now = datetime.now()
    if st.session_state.last_active:
        elapsed = (now - st.session_state.last_active).total_seconds() / 60
        if elapsed > SESSION_TIMEOUT:
            db.add_audit(st.session_state.user["id"], "AUTO_LOGOUT", "session timeout")
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.last_active = None
            st.warning("⏱️ You were logged out due to 30 minutes of inactivity.")
            st.rerun()
    st.session_state.last_active = now

# ══════════════════════════════════════════════════════════════════════════════
# EMAIL HELPER
# ══════════════════════════════════════════════════════════════════════════════
def send_email(to_addr, subject, body_html):
    """Send via Gmail SMTP. Silently skips if credentials not configured."""
    if not GMAIL_USER or not GMAIL_APP_PW or not to_addr:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"SimLab Manager <{GMAIL_USER}>"
        msg["To"]      = to_addr
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_APP_PW)
            srv.sendmail(GMAIL_USER, to_addr, msg.as_string())
        return True
    except Exception:
        return False

def notify_and_email(user_id, message, ntype="info", subject=None, email_body=None):
    """Add in-app notification and optionally send email."""
    db.add_notification(user_id, message, ntype)
    u = db.get_user(user_id)
    if u and u.get("email"):
        send_email(u["email"],
                   subject or f"SimLab: {message[:60]}",
                   email_body or f"<p>{message}</p><p>Log in to SimLab Manager for details.</p>")

# ══════════════════════════════════════════════════════════════════════════════
# QR CODE HELPER
# ══════════════════════════════════════════════════════════════════════════════
def generate_qr_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=8, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1e3a5f", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# SHARED UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def metric(col, val, label):
    col.markdown(f'<div class="metric-card"><div class="val">{val}</div>'
                 f'<div class="lbl">{label}</div></div>', unsafe_allow_html=True)

def header(title, sub=""):
    st.markdown(f'<div class="main-header"><h1>{title}</h1>'
                + (f'<p>{sub}</p>' if sub else '') + '</div>', unsafe_allow_html=True)

def search_box(placeholder="Search..."):
    return st.text_input("🔍", placeholder=placeholder, label_visibility="collapsed")

def next_session_id():  return f"SES{db.session_count()+1:04d}"
def next_booking_id():  return f"BK{db.booking_count()+1:04d}"
def next_att_id():      return f"ATT{db.attendance_count()+1:05d}"

# ══════════════════════════════════════════════════════════════════════════════
# ANNOUNCEMENT POP-UP (shown once per login)
# ══════════════════════════════════════════════════════════════════════════════
def maybe_show_ann_popup():
    if st.session_state.ann_popup_shown:
        return
    anns = db.get_announcements(active_only=True)
    if not anns:
        st.session_state.ann_popup_shown = True
        return
    with st.expander("📢 Announcements — click to dismiss", expanded=True):
        for a in anns[:3]:
            pinned = "📌 " if a["pinned"] else ""
            st.markdown(f"**{pinned}{a['title']}**")
            st.write(a["body"])
            st.caption(f"Posted by {a['author']} · {str(a['created_at'])[:10]}")
            st.markdown("---")
        if st.button("✅ Dismiss", width='stretch'):
            st.session_state.ann_popup_shown = True
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# AUTH PAGES
# ══════════════════════════════════════════════════════════════════════════════
def auth_pages():
    col1, col2, col3 = st.columns([1,1.6,1])
    with col2:
        st.markdown("""<div class="main-header" style="text-align:center">
          <h1>🖥️ SimLab Manager</h1>
          <p>Simulation Laboratory Management System</p>
        </div>""", unsafe_allow_html=True)

        tab_l, tab_r, tab_rst = st.tabs(["🔑 Login","📝 Register","🔒 Reset Password"])

        # LOGIN ────────────────────────────────────────────────────────────
        with tab_l:
            with st.form("lf"):
                uid = st.text_input("Student / Staff ID", placeholder="e.g. STU001")
                pw  = st.text_input("Password", type="password")
                if st.form_submit_button("Login", width='stretch'):
                    result = db.get_active_user_by_id_pw(uid, hash_pw(pw))
                    if result == "locked":
                        st.error("🔒 Account temporarily locked after too many failed attempts. Try again in 15 minutes.")
                    elif result:
                        db.reset_failed_login(uid)
                        st.session_state.logged_in   = True
                        st.session_state.user        = result
                        st.session_state.last_active = datetime.now()
                        st.session_state.ann_popup_shown = False
                        db.add_audit(uid, "LOGIN")
                        st.rerun()
                    else:
                        # check if user exists to record failed attempt
                        u = db.get_user(uid)
                        if u:
                            db.record_failed_login(uid)
                        st.error("Invalid ID or password.")
            st.info("👆 Enter your UENR Student ID (e.g. UEB0501721) and password to log in.")

        # REGISTER ─────────────────────────────────────────────────────────
        with tab_r:
            role_choice = st.selectbox("Registering as a...", ["student","lecturer","admin"])
            with st.form("rf"):
                c1,c2     = st.columns(2)
                new_id    = c1.text_input("ID *",               placeholder="e.g. STU050")
                new_name  = c2.text_input("Full Name *")
                new_email = c1.text_input("Email *")
                new_pw    = c2.text_input("Password *",         type="password")
                new_pw2   = c1.text_input("Confirm Password *", type="password")
                sec_q     = c2.selectbox("Security Question",   SECURITY_QS)
                sec_a     = st.text_input("Security Answer *")
                invite    = st.text_input("Admin Invite Code *", type="password") \
                            if role_choice=="admin" else None
                if st.form_submit_button("Create Account", width='stretch'):
                    errs = []
                    if not all([new_id,new_name,new_email,new_pw,sec_a]): errs.append("All fields required.")
                    if new_pw != new_pw2:   errs.append("Passwords do not match.")
                    if len(new_pw) < 6:     errs.append("Password must be ≥ 6 characters.")
                    if role_choice=="admin" and invite!=ADMIN_CODE: errs.append("Invalid invite code.")
                    # ID format validation
                    if role_choice == "student":
                        id_ok, id_err = validate_student_role_id(new_id)
                        if not id_ok: errs.append(id_err)
                    else:
                        id_ok, id_err = validate_student_id(new_id)
                        if not id_ok: errs.append(id_err)
                    if db.user_exists(new_id): errs.append("ID already registered.")
                    if errs:
                        for e in errs: st.error(e)
                    else:
                        ok = db.create_user(new_id, new_name, new_email,
                                            hash_pw(new_pw), role_choice,
                                            sec_q, hash_pw(sec_a.lower().strip()))
                        if ok:
                            db.add_audit(new_id,"REGISTER",f"role={role_choice}")
                            st.success(f"Account created! Log in with **{new_id}**.")
                        else:
                            st.error("Registration failed.")

        # RESET PASSWORD ───────────────────────────────────────────────────
        with tab_rst:
            with st.form("rsf"):
                r_id  = st.text_input("Your ID *")
                u_chk = db.get_user(r_id) if r_id else None
                if u_chk: st.info(f"Security Question: **{u_chk['security_q']}**")
                r_ans  = st.text_input("Security Answer *")
                r_pw   = st.text_input("New Password *",      type="password")
                r_pw2  = st.text_input("Confirm Password *",  type="password")
                if st.form_submit_button("Reset Password", width='stretch'):
                    u = db.get_user(r_id)
                    if not u:                                           st.error("ID not found.")
                    elif u.get("security_a")!=hash_pw(r_ans.lower().strip()): st.error("Wrong answer.")
                    elif r_pw!=r_pw2:                                  st.error("Passwords don't match.")
                    elif len(r_pw)<6:                                  st.error("Min 6 characters.")
                    else:
                        db.update_password(r_id, hash_pw(r_pw))
                        db.add_audit(r_id,"PASSWORD_RESET")
                        st.success("Password reset! You can now log in.")

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
def sidebar_nav():
    user   = st.session_state.user
    role   = user["role"]
    unread = db.unread_count(user["id"])
    with st.sidebar:
        st.markdown(f"### 👤 {user['name']}")
        st.markdown(f"**{role.title()}** · `{user['id']}`")
        if unread:
            st.markdown(f"🔔 **{unread} unread**")
        st.caption(f"⏱️ Auto-logout in {SESSION_TIMEOUT} min inactivity")
        st.markdown("---")
        pages = {
            "admin":    ["📊 Dashboard","🔔 Notifications","📢 Announcements",
                         "🎓 Students","📅 Lab Sessions","🗓️ Bookings",
                         "🖥️ Workstations","📋 Attendance","📝 Assignments",
                         "📈 Reports","🚫 Blackout Dates","⚙️ Profile & Settings"],
            "lecturer": ["📊 Dashboard","🔔 Notifications","📢 Announcements",
                         "📅 My Sessions","📋 Attendance","📝 Assignments",
                         "⚙️ Profile & Settings"],
            "student":  ["📊 My Dashboard","🔔 Notifications","📢 Announcements",
                         "🗓️ Book a Slot","📋 My History","📝 Assignments",
                         "🪪 My QR Code","⚙️ Profile & Settings"],
        }[role]
        choice = st.radio("Nav", pages, label_visibility="collapsed")
        st.markdown("---")
        if st.button("🚪 Logout", width='stretch'):
            db.add_audit(user["id"],"LOGOUT")
            for k in ["logged_in","user","last_active","ann_popup_shown",
                      "sel_book_date","sel_book_slot"]:
                st.session_state[k] = False if k=="logged_in" else None
            st.rerun()
    return choice

# ══════════════════════════════════════════════════════════════════════════════
# SHARED PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_notifications():
    user = st.session_state.user
    header("🔔 Notifications")
    notifs = db.get_notifications(user["id"])
    if not notifs: st.info("No notifications yet."); return
    c1,c2 = st.columns([6,1])
    c1.write(f"**{sum(1 for n in notifs if not n['read'])} unread** of {len(notifs)} total")
    if c2.button("Mark all read"): db.mark_all_read(user["id"]); st.rerun()
    for n in notifs:
        css = {"info":"nb-info","success":"nb-success","warning":"nb-warning","error":"nb-error"}.get(n["type"],"nb-info")
        dot = "" if n["read"] else "🔵 "
        st.markdown(f'<div class="nb {css}">{dot}{n["message"]}<br>'
                    f'<small style="opacity:.7">{str(n["created_at"])[:16]}</small></div>',
                    unsafe_allow_html=True)
        if not n["read"]:
            if st.button("Mark read", key=f"mr_{n['id']}"): db.mark_notification_read(n["id"]); st.rerun()


def page_announcements():
    user = st.session_state.user
    role = user["role"]
    header("📢 Announcements")

    if role == "admin":
        tab1, tab2 = st.tabs(["📋 All Announcements","➕ Post Announcement"])
        with tab1:
            anns = db.get_announcements(active_only=False)
            if not anns: st.info("No announcements yet.")
            for a in anns:
                status = "✅ Active" if a["active"] else "🗃️ Archived"
                pinned = "📌 " if a["pinned"] else ""
                cls = "ann-pinned" if a["pinned"] else "ann-card"
                st.markdown(f'<div class="ann-card {cls}"><b>{pinned}{a["title"]}</b> '
                            f'<span style="font-size:.8rem;color:#666">· {status} · {str(a["created_at"])[:10]}</span>'
                            f'<br>{a["body"]}</div>', unsafe_allow_html=True)
                c1,c2,c3 = st.columns([2,2,6])
                if a["active"]:
                    if c1.button("📌 Toggle Pin", key=f"pin_{a['id']}"):
                        db.toggle_pin(a["id"], not a["pinned"]); st.rerun()
                    if c2.button("🗃️ Archive", key=f"arc_{a['id']}"):
                        db.deactivate_announcement(a["id"]); st.rerun()
        with tab2:
            with st.form("ann_form"):
                title  = st.text_input("Title *")
                body   = st.text_area("Message *")
                pinned = st.checkbox("📌 Pin this announcement")
                if st.form_submit_button("Post Announcement", width='stretch'):
                    if title and body:
                        db.create_announcement(title, body, user["id"], user["name"], pinned)
                        # notify all users
                        for u in db.get_all_users():
                            if u["id"] != user["id"]:
                                notify_and_email(u["id"], f"📢 New announcement: {title}",
                                    "info", f"SimLab Announcement: {title}", f"<h3>{title}</h3><p>{body}</p>")
                        st.success("Announcement posted!"); st.rerun()
                    else:
                        st.error("Title and message are required.")
    else:
        anns = db.get_announcements(active_only=True)
        if not anns: st.info("No active announcements.")
        for a in anns:
            pinned = "📌 " if a["pinned"] else ""
            cls = "ann-pinned" if a["pinned"] else "ann-card"
            st.markdown(f'<div class="ann-card {cls}"><b>{pinned}{a["title"]}</b> '
                        f'<br><span style="margin-top:.4rem;display:block">{a["body"]}</span>'
                        f'<br><small style="color:#888">Posted by {a["author"]} · {str(a["created_at"])[:10]}</small>'
                        f'</div>', unsafe_allow_html=True)


def page_profile():
    user = st.session_state.user
    header("⚙️ Profile & Settings")
    tab1, tab2 = st.tabs(["👤 My Profile","🔑 Change Password"])
    with tab1:
        with st.form("pf"):
            c1,c2     = st.columns(2)
            new_name  = c1.text_input("Full Name",  value=user["name"])
            new_email = c2.text_input("Email",       value=user.get("email",""))
            sq_index  = SECURITY_QS.index(user["security_q"]) if user.get("security_q") in SECURITY_QS else 0
            new_sec_q = c1.selectbox("Security Question", SECURITY_QS, index=sq_index)
            new_sec_a = c2.text_input("New Security Answer (blank = keep current)")
            if st.form_submit_button("Save Changes", width='stretch'):
                sec_a_hash = hash_pw(new_sec_a.lower().strip()) if new_sec_a.strip() else None
                db.update_user(user["id"], new_name, new_email, new_sec_q, sec_a_hash)
                db.add_audit(user["id"],"PROFILE_UPDATE")
                st.session_state.user = db.get_user(user["id"])
                st.success("Profile updated!")
    with tab2:
        with st.form("cpf"):
            old_pw  = st.text_input("Current Password *", type="password")
            new_pw  = st.text_input("New Password *",      type="password")
            new_pw2 = st.text_input("Confirm Password *",  type="password")
            if st.form_submit_button("Change Password", width='stretch'):
                u = db.get_user(user["id"])
                if u["password"]!=hash_pw(old_pw): st.error("Current password incorrect.")
                elif new_pw!=new_pw2:              st.error("Passwords don't match.")
                elif len(new_pw)<6:                st.error("Min 6 characters.")
                else:
                    db.update_password(user["id"], hash_pw(new_pw))
                    db.add_audit(user["id"],"PASSWORD_CHANGE")
                    st.success("Password changed!")

# ══════════════════════════════════════════════════════════════════════════════
# STUDENT QR CODE PAGE
# ══════════════════════════════════════════════════════════════════════════════
def page_my_qr():
    user = st.session_state.user
    header("🪪 My QR Code", "Show this to the lab technician when checking in")
    col1, col2, col3 = st.columns([1,1.5,1])
    with col2:
        qr_data  = f"SIMLAB_CHECKIN:{user['id']}"
        qr_bytes = generate_qr_bytes(qr_data)
        st.image(qr_bytes, caption=f"{user['name']} · {user['id']}", width='stretch')
        st.download_button("📥 Download QR Code", qr_bytes,
                           f"simlab_qr_{user['id']}.png", "image/png",
                           width='stretch')
        st.info("💡 Save this QR to your phone and show it at the door for instant check-in.")

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_lecturer_dashboard():
    user = st.session_state.user
    header("📊 Dashboard", f"Welcome, {user['name']}")
    maybe_show_ann_popup()

    today        = str(date.today())
    all_sessions = db.get_all_sessions()
    my_sessions  = [s for s in all_sessions
                    if s["lecturer"] == user["name"] and not s.get("cancelled")]
    today_sess   = [s for s in my_sessions if s["date"] == today]
    att_all      = db.get_all_attendance()

    # metrics scoped to this lecturer's sessions only
    my_ref_ids   = {s["id"] for s in my_sessions}
    my_att       = [a for a in att_all if a["reference_id"] in my_ref_ids]
    today_att    = [a for a in my_att if a["date"] == today]
    active_now   = [a for a in today_att if not a["checked_out"]]

    c1, c2, c3, c4 = st.columns(4)
    metric(c1, len(my_sessions),  "My Total Sessions")
    metric(c2, len(today_sess),   "My Sessions Today")
    metric(c3, len(today_att),    "Check-ins Today")
    metric(c4, len(active_now),   "Currently In Lab")

    st.markdown("---")
    cl, cr = st.columns(2)

    with cl:
        st.subheader("📅 My Sessions Today")
        if today_sess:
            for s in today_sess:
                cnt  = len([a for a in att_all if a["reference_id"] == s["id"]])
                late = len([a for a in att_all if a["reference_id"] == s["id"]
                            and a.get("late")])
                label = (f"**{s['course']}** | {s['start_time']}–{s['end_time']} "
                         f"| {cnt}/{s['max_students']} present")
                if late:
                    label += f" · ⚠️ {late} late"
                st.info(label)
        else:
            st.write("No sessions scheduled for today.")

    with cr:
        st.subheader("📋 Recent Attendance (My Sessions)")
        if my_att:
            recent = sorted(my_att, key=lambda a: (a["date"], a["time"]), reverse=True)[:8]
            df = pd.DataFrame(recent)[["student_name", "date", "time",
                                       "workstation", "checked_out"]]
            df.columns = ["Student", "Date", "Time", "Workstation", "Checked Out"]
            df["Checked Out"] = df["Checked Out"].map({0: "❌", 1: "✅", False: "❌", True: "✅"})
            st.dataframe(df, width='stretch', hide_index=True)
        else:
            st.write("No attendance records for your sessions yet.")


def page_admin_dashboard():
    header("📊 Dashboard","Overview of lab activity")
    maybe_show_ann_popup()
    today    = str(date.today())
    students = db.get_all_users("student")
    t_sess   = [s for s in db.get_sessions_on_date(today) if not s.get("cancelled")]
    pending  = [b for b in db.get_all_bookings() if b["status"]=="pending"]
    t_att    = db.get_active_checkins(today)
    all_att  = [a for a in db.get_all_attendance() if a["date"]==today]

    c1,c2,c3,c4,c5 = st.columns(5)
    metric(c1,len(students),"Registered Students")
    metric(c2,len(t_sess),"Today's Sessions")
    metric(c3,len(pending),"Pending Bookings")
    metric(c4,len(all_att),"Check-ins Today")
    metric(c5,len(t_att),"Currently In Lab")

    # show unread announcements hint
    anns = db.get_announcements()
    if anns:
        st.info(f"📢 {len(anns)} active announcement(s). Go to **Announcements** to manage.")

    st.markdown("---")
    cl,cr = st.columns(2)
    with cl:
        st.subheader("📅 Today's Sessions")
        if t_sess:
            att_all = db.get_all_attendance()
            for s in t_sess:
                cnt = len([a for a in att_all if a["reference_id"]==s["id"]])
                late = len([a for a in att_all if a["reference_id"]==s["id"] and a.get("late")])
                label = f"**{s['course']}** | {s['start_time']}–{s['end_time']} | {cnt}/{s['max_students']} in"
                if late: label += f" · ⚠️ {late} late"
                st.info(label)
        else:
            st.write("No sessions today.")
    with cr:
        st.subheader("🔔 Pending Booking Requests")
        if pending:
            for b in pending[:6]:
                cols = st.columns([4,1,1])
                cols[0].write(f"**{b['student_name']}** — {b['date']} {b['time_slot']}")
                if cols[1].button("✅",key=f"da_{b['id']}"):
                    db.update_booking_status(b["id"],"approved")
                    notify_and_email(b["student_id"],
                        f"Your booking for {b['date']} {b['time_slot']} is approved! ✅",
                        "success","SimLab: Booking Approved",
                        f"<p>Hi {b['student_name']},</p>"
                        f"<p>Your booking for <b>{b['date']}</b> at <b>{b['time_slot']}</b> has been approved.</p>"
                        f"<p>Please arrive on time and bring your student ID.</p>")
                    st.rerun()
                if cols[2].button("❌",key=f"dr_{b['id']}"):
                    db.update_booking_status(b["id"],"rejected")
                    notify_and_email(b["student_id"],
                        f"Your booking for {b['date']} {b['time_slot']} was not approved.",
                        "error","SimLab: Booking Not Approved",
                        f"<p>Hi {b['student_name']},</p>"
                        f"<p>Your booking for <b>{b['date']}</b> at <b>{b['time_slot']}</b> was not approved.</p>"
                        f"<p>Please contact the lab technician for assistance.</p>")
                    st.rerun()
        else:
            st.write("No pending requests.")


def page_students():
    header("🎓 Student & User Management")
    tab1, tab2, tab3 = st.tabs(["📋 All Users","➕ Register Student","🔒 Account Control"])

    with tab1:
        q     = search_box("Search by name or ID...")
        role_f = st.selectbox("Filter by role",["all","student","lecturer","admin"])
        users = db.get_all_users(role_f if role_f!="all" else None)
        if q: users = [u for u in users if q.lower() in u["name"].lower() or q.lower() in u["id"].lower()]
        if users:
            df = pd.DataFrame([{"ID":u["id"],"Name":u["name"],"Role":u["role"],
                                 "Email":u.get("email",""),
                                 "Active":"✅" if u.get("active",1) else "🔴"}
                                for u in users])
            st.dataframe(df, width='stretch', hide_index=True)
            st.caption(f"{len(users)} user(s)")
        else:
            st.info("No users found.")

    with tab2:
        with st.form("rs"):
            c1,c2 = st.columns(2)
            sid   = c1.text_input("Student ID *")
            name  = c2.text_input("Full Name *")
            email = c1.text_input("Email")
            pw    = c2.text_input("Password *", type="password")
            if st.form_submit_button("Register", width='stretch'):
                if sid and name and pw:
                    id_ok, id_err = validate_student_role_id(sid)
                    if not id_ok:
                        st.error(id_err)
                    else:
                        ok = db.create_user(sid,name,email,hash_pw(pw),"student",
                                            SECURITY_QS[0],hash_pw("changeme"))
                        if ok:
                            db.add_audit(st.session_state.user["id"],"REGISTER_STUDENT",sid)
                            st.success(f"Student {name} registered!"); st.rerun()
                        else: st.error("ID already exists.")
                else: st.error("Fill all required fields.")

    with tab3:
        st.subheader("Deactivate, Reactivate or Delete Accounts")
        all_users = [u for u in db.get_all_users() if u["id"] != st.session_state.user["id"]]
        if not all_users:
            st.info("No other users.")
        else:
            def _user_label(u):
                inactive = "🔴 Inactive" if not u.get("active", 1) else ""
                return f"{u['id']} — {u['name']} ({u['role']}) {inactive}".strip()

            target_id = st.selectbox("Select user", [_user_label(u) for u in all_users])
            selected  = all_users[[_user_label(u) for u in all_users].index(target_id)]
            is_active = bool(selected.get("active", 1))

            c1, c2, c3 = st.columns(3)
            if is_active:
                if c1.button("🔴 Deactivate Account", width='stretch'):
                    db.set_user_active(selected["id"], False)
                    db.add_audit(st.session_state.user["id"], "DEACTIVATE", selected["id"])
                    st.success(f"{selected['name']} deactivated."); st.rerun()
            else:
                if c1.button("✅ Reactivate Account", width='stretch'):
                    db.set_user_active(selected["id"], True)
                    db.add_audit(st.session_state.user["id"], "REACTIVATE", selected["id"])
                    st.success(f"{selected['name']} reactivated."); st.rerun()

            st.markdown("---")
            st.error("⚠️ Danger Zone")
            confirm = st.text_input(f"Type  {selected['id']}  to confirm deletion")
            if c3.button("🗑️ Delete Permanently", width='stretch'):
                if confirm == selected["id"]:
                    db.delete_user(selected["id"])
                    db.add_audit(st.session_state.user["id"], "DELETE_USER", selected["id"])
                    st.success("User deleted."); st.rerun()
                else:
                    st.error("ID confirmation does not match.")


def page_lab_sessions():
    header("📅 Lab Sessions")
    tab1, tab2, tab3 = st.tabs(["📋 All Sessions","➕ Create Session","✏️ Edit / Cancel Session"])

    with tab1:
        q = search_box("Search by course or lecturer...")
        sessions = db.get_all_sessions()
        if q: sessions = [s for s in sessions if q.lower() in s["course"].lower()
                          or q.lower() in s["lecturer"].lower()]
        if sessions:
            att_all = db.get_all_attendance()
            rows = []
            for s in sessions:
                cnt  = len([a for a in att_all if a["reference_id"]==s["id"]])
                late = len([a for a in att_all if a["reference_id"]==s["id"] and a.get("late")])
                rows.append({"ID":s["id"],"Course":s["course"],"Date":s["date"],
                             "Time":f"{s['start_time']}–{s['end_time']}",
                             "Lecturer":s["lecturer"],
                             "Attendance":f"{cnt}/{s['max_students']}",
                             "Late":late,
                             "Status":"❌ Cancelled" if s.get("cancelled") else "✅ Active",
                             "Recurring":bool(s["recurring"])})
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        else: st.info("No sessions yet.")

    with tab2:
        users    = db.get_all_users()
        lecs     = [u for u in users if u["role"] in ("lecturer","admin")]
        lec_names = [l["name"] for l in lecs]
        sub1, sub2 = st.tabs(["Single Session","Recurring (Weekly)"])
        with sub1:
            with st.form("ss"):
                c1,c2  = st.columns(2)
                course  = c1.text_input("Course Name / Code *")
                lec     = c2.selectbox("Lecturer", lec_names)
                s_date  = c1.date_input("Date", min_value=date.today())
                s_start = c2.text_input("Start Time *", placeholder="08:00")
                s_end   = c1.text_input("End Time *",   placeholder="10:00")
                max_stu = c2.number_input("Max Students",1,20,15)
                notes   = st.text_area("Notes")
                if st.form_submit_button("Create Session", width='stretch'):
                    if course and s_start and s_end:
                        if db.is_blackout(str(s_date)):
                            st.error(f"⛔ {s_date} is a blackout date — lab is closed.")
                        else:
                            conflict = db.sessions_overlap(str(s_date),s_start,s_end)
                            if conflict: st.error(f"Time conflict with **{conflict['course']}**.")
                            else:
                                sid = next_session_id()
                                db.create_session(sid,course,lec,str(s_date),s_start,s_end,
                                                  int(max_stu),notes,st.session_state.user["id"])
                                db.add_audit(st.session_state.user["id"],"CREATE_SESSION",sid)
                                st.success(f"Session **{sid}** created!"); st.rerun()
                    else: st.error("Fill all required fields.")
        with sub2:
            with st.form("rs2"):
                c1,c2    = st.columns(2)
                r_course = c1.text_input("Course *",key="rc")
                r_lec    = c2.selectbox("Lecturer",lec_names,key="rl")
                r_date   = c1.date_input("First Date",min_value=date.today(),key="rd")
                r_start  = c2.text_input("Start Time *",placeholder="08:00",key="rs_t")
                r_end    = c1.text_input("End Time *",  placeholder="10:00",key="re_t")
                r_weeks  = c2.number_input("Weeks",1,24,12)
                r_max    = c1.number_input("Max Students",1,20,15,key="rm")
                r_notes  = st.text_area("Notes",key="rn")
                if st.form_submit_button("Create Recurring Sessions", width='stretch'):
                    if r_course and r_start and r_end:
                        added = 0
                        for w in range(int(r_weeks)):
                            d_w = r_date + timedelta(weeks=w)
                            if db.is_blackout(str(d_w)):
                                st.warning(f"Week {w+1} ({d_w}) skipped — blackout date.")
                                continue
                            conflict = db.sessions_overlap(str(d_w),r_start,r_end)
                            if conflict:
                                st.warning(f"Week {w+1} ({d_w}) skipped — conflicts with '{conflict['course']}'.")
                                continue
                            db.create_session(next_session_id(),r_course,r_lec,str(d_w),
                                              r_start,r_end,int(r_max),r_notes,
                                              st.session_state.user["id"],recurring=True)
                            added += 1
                        st.success(f"{added} session(s) created."); st.rerun()
                    else: st.error("Fill all required fields.")

    with tab3:
        sessions = [s for s in db.get_all_sessions() if not s.get("cancelled")]
        if not sessions:
            st.info("No active sessions to edit.")
        else:
            sel_label = st.selectbox("Select session to edit / cancel",
                [f"{s['id']} | {s['course']} | {s['date']} {s['start_time']}–{s['end_time']}"
                 for s in sessions])
            sel_id = sel_label.split(" | ")[0]
            s = db.get_session_by_id(sel_id)

            edit_tab, cancel_tab = st.tabs(["✏️ Edit Session","❌ Cancel Session"])
            with edit_tab:
                users     = db.get_all_users()
                lecs      = [u for u in users if u["role"] in ("lecturer","admin")]
                lec_names = [l["name"] for l in lecs]
                with st.form("edit_sess"):
                    c1, c2   = st.columns(2)
                    e_course = c1.text_input("Course *", value=s["course"])
                    lec_idx  = lec_names.index(s["lecturer"]) if s["lecturer"] in lec_names else 0
                    e_lec    = c2.selectbox("Lecturer", lec_names, index=lec_idx)
                    e_date   = c1.date_input("Date", value=date.fromisoformat(s["date"]))
                    e_start  = c2.text_input("Start Time *", value=s["start_time"])
                    e_end    = c1.text_input("End Time *",   value=s["end_time"])
                    e_max    = c2.number_input("Max Students", 1, 20, s["max_students"])
                    e_notes  = st.text_area("Notes", value=s.get("notes",""))
                    if st.form_submit_button("💾 Save Changes", width='stretch'):
                        conflict = db.sessions_overlap(str(e_date), e_start, e_end, exclude_id=sel_id)
                        if conflict:
                            st.error(f"Time conflict with **{conflict['course']}**.")
                        else:
                            db.update_session(sel_id, e_course, e_lec, str(e_date),
                                              e_start, e_end, int(e_max), e_notes)
                            db.add_audit(st.session_state.user["id"], "EDIT_SESSION", sel_id)
                            st.success("Session updated!"); st.rerun()
            with cancel_tab:
                reason = st.text_area("Cancellation reason *")
                if st.button("❌ Cancel This Session", width='stretch'):
                    if reason:
                        db.cancel_session(sel_id, reason)
                        db.add_audit(st.session_state.user["id"], "CANCEL_SESSION", sel_id)
                        att_all  = db.get_all_attendance()
                        affected = [a["student_id"] for a in att_all
                                    if a["reference_id"] == sel_id]
                        for affected_id in affected:
                            notify_and_email(affected_id,
                                f"Session '{s['course']}' on {s['date']} cancelled. "
                                f"Reason: {reason}", "warning",
                                "SimLab: Session Cancelled",
                                f"<p>The session <b>{s['course']}</b> on <b>{s['date']}</b> "
                                f"has been cancelled.</p><p>Reason: {reason}</p>")
                        st.success("Session cancelled and affected students notified.")
                        st.rerun()
                    else:
                        st.error("Please provide a cancellation reason.")


def page_bookings():
    header("🗓️ Open-Access Bookings")
    tab1, tab2 = st.tabs(["🔔 Requests","📅 Slot Overview"])
    with tab1:
        q        = search_box("Search by student name or ID...")
        c1,c2    = st.columns(2)
        f_status = c1.selectbox("Status",["all","pending","approved","rejected"])
        f_date   = c2.date_input("Date filter",value=None)
        bks = db.get_all_bookings()
        if f_status!="all": bks=[b for b in bks if b["status"]==f_status]
        if f_date:          bks=[b for b in bks if b["date"]==str(f_date)]
        if q:               bks=[b for b in bks if q.lower() in b["student_name"].lower()
                                 or q.lower() in b["student_id"].lower()]
        if bks:
            for b in bks:
                icon={"pending":"🟡","approved":"🟢","rejected":"🔴"}.get(b["status"],"⚪")
                cols = st.columns([4,2,1,1])
                cols[0].write(f"**{b['student_name']}** (`{b['student_id']}`) — {b['date']} @ {b['time_slot']}")
                cols[1].write(f"{icon} {b['status'].title()}")
                if b["status"]=="pending":
                    if cols[2].button("✅",key=f"ap_{b['id']}"):
                        db.update_booking_status(b["id"],"approved")
                        notify_and_email(b["student_id"],
                            f"Your booking for {b['date']} {b['time_slot']} is approved! ✅","success",
                            "SimLab: Booking Approved",
                            f"<p>Hi {b['student_name']},</p><p>Your booking for <b>{b['date']}</b> at <b>{b['time_slot']}</b> is confirmed.</p>")
                        st.rerun()
                    if cols[3].button("❌",key=f"rj_{b['id']}"):
                        db.update_booking_status(b["id"],"rejected")
                        notify_and_email(b["student_id"],
                            f"Your booking for {b['date']} {b['time_slot']} was not approved.","error",
                            "SimLab: Booking Not Approved",
                            f"<p>Hi {b['student_name']},</p><p>Your booking for <b>{b['date']}</b> at <b>{b['time_slot']}</b> was not approved.</p>")
                        st.rerun()
        else: st.info("No bookings match your filters.")
    with tab2:
        st.subheader("Slot Availability — Next 3 Days")
        for offset in range(3):
            chk = date.today()+timedelta(days=offset)
            bd  = db.is_blackout(str(chk))
            label = f"**📅 {chk.strftime('%A, %d %B %Y')}**"
            if bd: label += " 🚫 BLACKOUT — Lab Closed"
            st.markdown(label)
            if not bd:
                cols = st.columns(len(TIME_SLOTS))
                for i,slot in enumerate(TIME_SLOTS):
                    cnt   = db.slot_booking_count(str(chk),slot)
                    avail = MAX_PER_SLOT-cnt
                    cols[i].markdown(
                        f"<div style='text-align:center;font-size:.73rem'>{slot}<br>"
                        f"<b style='color:{'#155724' if avail>0 else '#721c24'}'>"
                        f"{'✅' if avail>0 else '🔴'} {avail} left</b></div>",
                        unsafe_allow_html=True)
            st.markdown("")


def page_workstations():
    header("🖥️ Workstation Management")
    ws_list = db.get_all_workstations()
    tab1, tab2 = st.tabs(["📋 Status Board","📜 Usage History"])
    with tab1:
        avail  = sum(1 for w in ws_list if w["status"]=="available")
        in_use = sum(1 for w in ws_list if w["status"]=="in-use")
        maint  = sum(1 for w in ws_list if w["status"]=="maintenance")
        c1,c2,c3 = st.columns(3)
        c1.success(f"✅ Available: {avail}")
        c2.warning(f"🟡 In Use: {in_use}")
        c3.error(f"🔴 Maintenance: {maint}")
        st.markdown("---")
        cols = st.columns(4)
        for i,ws in enumerate(ws_list):
            with cols[i%4]:
                icon={"available":"🟢","in-use":"🟡","maintenance":"🔴"}.get(ws["status"],"⚪")
                st.markdown(f"**{icon} {ws['label']}**")
                new_status = st.selectbox("Status",["available","in-use","maintenance"],
                    index=["available","in-use","maintenance"].index(ws["status"]),
                    key=f"ws_{ws['id']}",label_visibility="collapsed")
                note = st.text_input("Note",value=ws.get("notes",""),
                    key=f"wn_{ws['id']}",placeholder="e.g. Screen broken") \
                    if new_status=="maintenance" or ws["status"]=="maintenance" else ws.get("notes","")
                if new_status!=ws["status"] or note!=ws.get("notes",""):
                    db.update_workstation(ws["id"],new_status,note); st.rerun()
    with tab2:
        q   = search_box("Filter by workstation (e.g. PC-01)...")
        att = db.get_all_attendance()
        if q: att=[a for a in att if q.lower() in a.get("workstation","").lower()]
        if att:
            df = pd.DataFrame(att)[["workstation","student_id","student_name","date","time","type"]]
            df.columns=["Workstation","Stu. ID","Name","Date","Time","Type"]
            st.dataframe(df,width='stretch',hide_index=True)
        else: st.info("No usage records yet.")


def page_attendance():
    header("📋 Attendance & Check-In / Out")
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "✅ Check-In","👥 Bulk Session Check-In",
        "🚪 Check-Out","📋 Records & Export","📄 Per-Session Export"])

    # SINGLE CHECK-IN ─────────────────────────────────────────────────────────
    with tab1:
        st.subheader("QR Scan or Manual ID Entry")
        sessions = db.get_all_sessions()
        bookings = [b for b in db.get_all_bookings() if b["status"]=="approved"]
        avail_ws = [w["label"] for w in db.get_available_workstations()]
        check_type = st.radio("Type",["Scheduled Session","Open-Access Booking"],horizontal=True)

        # QR scan input (admin pastes or types scanned value)
        qr_input = st.text_input("📷 QR Scan Result (or type Student ID manually)",
                                  placeholder="Scan QR → paste here, or type ID directly")
        stu_id = qr_input.replace("SIMLAB_CHECKIN:","").strip() if qr_input else ""

        with st.form("ci"):
            stu_id_form = st.text_input("Student ID *", value=stu_id)
            if check_type=="Scheduled Session":
                opts=[f"{s['id']} | {s['course']} | {s['date']} {s['start_time']}–{s['end_time']}"
                      for s in sessions if not s.get("cancelled")]
                sel = st.selectbox("Session",opts) if opts else st.text_input("No sessions")
            else:
                opts=[f"{b['id']} | {b['student_id']} | {b['date']} {b['time_slot']}"
                      for b in bookings]
                sel = st.selectbox("Booking",opts) if opts else st.text_input("No approved bookings")
            ws = st.selectbox("Assign Workstation",avail_ws) if avail_ws else st.text_input("None available")
            if st.form_submit_button("✅ Check In",width='stretch'):
                sid_to_use = stu_id_form.strip()
                student = db.get_user(sid_to_use)
                if not student: st.error("Student ID not found.")
                elif db.student_already_checked_in(sid_to_use,str(date.today())): st.warning("Already checked in.")
                else:
                    ref_id  = sel.split(" | ")[0] if sel else ""
                    t_now   = datetime.now().strftime("%H:%M")
                    # detect late
                    late = False
                    if check_type=="Scheduled Session" and ref_id:
                        late = db.is_late_for_session(ref_id, t_now)
                    db.create_attendance_v2(next_att_id(), sid_to_use, student["name"],
                                            check_type, ref_id, ws,
                                            str(date.today()), t_now, late=late)
                    if ws and ws in avail_ws:
                        db.set_workstation_status(ws, "in-use")
                    msg = f"Checked in at {ws} · {date.today()} {t_now}"
                    if late: msg += " ⚠️ (marked late)"
                    notify_and_email(sid_to_use, msg, "info")
                    db.add_audit(st.session_state.user["id"],"CHECKIN",sid_to_use)
                    success_msg = f"✅ {student['name']} checked in at {ws}"
                    if late: success_msg += " — **⚠️ Late arrival flagged**"
                    st.success(success_msg)

    # BULK CHECK-IN ───────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Bulk Check-In for a Scheduled Session")
        sessions = [s for s in db.get_all_sessions() if not s.get("cancelled")]
        if not sessions:
            st.info("No active sessions.")
        else:
            sel_label = st.selectbox("Select Session",
                [f"{s['id']} | {s['course']} | {s['date']} {s['start_time']}–{s['end_time']}"
                 for s in sessions])
            sel_id   = sel_label.split(" | ")[0]
            sel_sess = db.get_session_by_id(sel_id)
            avail_ws = [w["label"] for w in db.get_available_workstations()]
            all_students = db.get_all_users("student")
            already_in   = {a["student_id"] for a in db.get_active_checkins(str(date.today()))}
            eligible     = [s for s in all_students if s["id"] not in already_in]

            if not eligible:
                st.info("All registered students are already checked in today.")
            else:
                selected_studs = st.multiselect(
                    "Select students to check in",
                    options=[f"{s['id']} — {s['name']}" for s in eligible],
                    help="Tick each student present in this session"
                )
                if st.button(f"✅ Check In {len(selected_studs)} Student(s)",
                             width='stretch', disabled=not selected_studs):
                    t_now      = datetime.now().strftime("%H:%M")
                    late       = db.is_late_for_session(sel_id, t_now)
                    assigned_ws = avail_ws.copy()
                    checked    = 0
                    for entry in selected_studs:
                        sid     = entry.split(" — ")[0]
                        student = db.get_user(sid)
                        if not student:
                            continue
                        ws_label = assigned_ws.pop(0) if assigned_ws else "Unassigned"
                        db.create_attendance_v2(next_att_id(), sid, student["name"],
                                                "Scheduled Session", sel_id, ws_label,
                                                str(date.today()), t_now, late=late)
                        if ws_label != "Unassigned":
                            db.set_workstation_status(ws_label, "in-use")
                        notify_and_email(sid,
                            f"Checked in to {sel_sess['course']} at {ws_label}"
                            + (" ⚠️ (late)" if late else ""), "info")
                        checked += 1
                    db.add_audit(st.session_state.user["id"], "BULK_CHECKIN",
                                 f"{checked} students for {sel_id}")
                    st.success(f"✅ {checked} student(s) checked in!"
                               + (" Late arrivals flagged." if late else ""))
                    st.rerun()

    # CHECK-OUT ───────────────────────────────────────────────────────────────
    with tab3:
        active = db.get_active_checkins(str(date.today()))
        if not active: st.info("No students currently in lab.")
        else:
            st.write(f"**{len(active)} student(s) in lab right now:**")
            for a in active:
                c1,c2 = st.columns([5,1])
                late_badge = ' <span class="late-badge">⚠️ LATE</span>' if a.get("late") else ""
                c1.markdown(f"**{a['student_name']}** (`{a['student_id']}`) — "
                            f"{a['workstation']} since {a['time']}{late_badge}",
                            unsafe_allow_html=True)
                if c2.button("🚪 Out",key=f"co_{a['id']}"):
                    t_out = datetime.now().strftime("%H:%M")
                    db.checkout_attendance(a["id"],t_out)
                    db.set_workstation_status(a["workstation"],"available")
                    notify_and_email(a["student_id"],f"Checked out of {a['workstation']} at {t_out}","info")
                    db.add_audit(st.session_state.user["id"],"CHECKOUT",a["student_id"])
                    st.rerun()

    # RECORDS ─────────────────────────────────────────────────────────────────
    with tab4:
        att = db.get_all_attendance()
        if not att:
            st.info("No records yet.")
        else:
            df = pd.DataFrame(att)
            c1,c2,c3 = st.columns(3)
            d_f = c1.date_input("Date", value=None)
            t_f = c2.selectbox("Type", ["All","Scheduled Session","Open-Access Booking"])
            s_f = c3.text_input("Student ID / Name")
            if d_f: df = df[df["date"] == str(d_f)]
            if t_f != "All": df = df[df["type"] == t_f]
            if s_f:
                df = df[df["student_id"].str.contains(s_f, case=False) |
                        df["student_name"].str.contains(s_f, case=False)]
            st.dataframe(df, width='stretch', hide_index=True)
            c1, c2 = st.columns(2)
            c1.download_button("📥 Download CSV", df.to_csv(index=False),
                               "attendance.csv", "text/csv", width='stretch')
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Attendance")
            c2.download_button("📊 Download Excel", buf.getvalue(), "attendance.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch')

    # PER-SESSION EXPORT ──────────────────────────────────────────────────────
    with tab5:
        st.subheader("📄 Export Attendance for a Specific Session")
        sessions = db.get_all_sessions()
        if not sessions:
            st.info("No sessions available.")
        else:
            sel = st.selectbox("Select Session",
                [f"{s['id']} | {s['course']} | {s['date']} {s['start_time']}–{s['end_time']}"
                 for s in sessions])
            sel_id   = sel.split(" | ")[0]
            sel_sess = db.get_session_by_id(sel_id)
            att      = db.get_attendance_for_session(sel_id)

            if not att:
                st.warning("No attendance records for this session yet.")
            else:
                df = pd.DataFrame(att)
                display_cols = [c for c in
                    ["student_id","student_name","email","time","late","workstation","checkout_time"]
                    if c in df.columns]
                df_display = df[display_cols].copy()
                df_display.columns = [c.replace("_"," ").title() for c in display_cols]

                total  = len(df)
                late_c = int(df["late"].sum()) if "late" in df.columns else 0
                st.markdown(f"**Session:** {sel_sess['course']} · "
                            f"{sel_sess['date']} {sel_sess['start_time']}–{sel_sess['end_time']} · "
                            f"Lecturer: {sel_sess['lecturer']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Present", total)
                c2.metric("On Time",       total - late_c)
                c3.metric("Late Arrivals", late_c)

                st.dataframe(df_display, width='stretch', hide_index=True)

                fname_base = f"attendance_{sel_sess['course'].replace(' ','_')}_{sel_sess['date']}"
                c1, c2 = st.columns(2)
                c1.download_button("📥 Download CSV",
                                   df_display.to_csv(index=False),
                                   f"{fname_base}.csv", "text/csv",
                                   width='stretch')
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    info_df = pd.DataFrame({
                        "Field": ["Course","Date","Time","Lecturer","Max Students","Present","Late"],
                        "Value": [sel_sess["course"], sel_sess["date"],
                                  f"{sel_sess['start_time']}–{sel_sess['end_time']}",
                                  sel_sess["lecturer"], sel_sess["max_students"],
                                  total, late_c]
                    })
                    info_df.to_excel(writer, index=False, sheet_name="Session Info")
                    df_display.to_excel(writer, index=False, sheet_name="Attendance")
                c2.download_button("📊 Download Excel", buf.getvalue(),
                                   f"{fname_base}.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   width='stretch')


def page_reports():
    header("📈 Reports & Analytics")
    att = db.get_all_attendance()
    if not att: st.info("No data yet."); return
    df  = pd.DataFrame(att)
    df["date"] = pd.to_datetime(df["date"])

    c1,c2 = st.columns(2)
    with c1:
        st.subheader("Daily Check-ins (Last 14 Days)")
        daily = df.groupby("date").size().reset_index(name="count").tail(14)
        fig = px.bar(daily,x="date",y="count",color_discrete_sequence=["#2d6a9f"])
        fig.update_layout(margin=dict(t=10),height=270)
        st.plotly_chart(fig,width='stretch')
    with c2:
        st.subheader("Session vs Open-Access Split")
        tc = df["type"].value_counts().reset_index(); tc.columns=["Type","Count"]
        fig2 = px.pie(tc,values="Count",names="Type",color_discrete_sequence=["#1e3a5f","#2d9fd6"])
        fig2.update_layout(margin=dict(t=10),height=270)
        st.plotly_chart(fig2,width='stretch')

    c3,c4 = st.columns(2)
    with c3:
        st.subheader("Workstation Usage")
        wc = df["workstation"].value_counts().reset_index(); wc.columns=["Workstation","Uses"]
        fig3 = px.bar(wc,x="Workstation",y="Uses",color_discrete_sequence=["#1e3a5f"])
        fig3.update_layout(margin=dict(t=10),height=260)
        st.plotly_chart(fig3,width='stretch')
    with c4:
        if "late" in df.columns:
            st.subheader("Late vs On-Time Arrivals")
            lc = df["late"].map({0:"On Time",1:"Late",True:"Late",False:"On Time"}).value_counts().reset_index()
            lc.columns=["Status","Count"]
            fig4 = px.pie(lc,values="Count",names="Status",color_discrete_sequence=["#2d9fd6","#f0ad4e"])
            fig4.update_layout(margin=dict(t=10),height=260)
            st.plotly_chart(fig4,width='stretch')

    st.subheader("Most Active Students")
    top = df.groupby(["student_id","student_name"]).size().reset_index(name="Visits")
    top = top.sort_values("Visits",ascending=False).head(10)
    st.dataframe(top,width='stretch',hide_index=True)


def page_blackout_dates():
    header("🚫 Blackout Dates","Mark days the lab is closed")
    tab1, tab2 = st.tabs(["📋 Current Blackouts","➕ Add Blackout Date"])
    with tab1:
        dates = db.get_blackout_dates()
        if dates:
            for d in dates:
                c1,c2,c3 = st.columns([3,4,1])
                c1.write(f"**{d['date']}**")
                c2.write(d["reason"] or "No reason given")
                if c3.button("🗑️",key=f"rm_{d['id']}"):
                    db.remove_blackout_date(d["date"]); st.rerun()
        else:
            st.info("No blackout dates set.")
    with tab2:
        with st.form("bd_form"):
            bd_date   = st.date_input("Date *",min_value=date.today())
            bd_reason = st.text_input("Reason *",placeholder="e.g. Public Holiday, Maintenance Day")
            if st.form_submit_button("Add Blackout Date",width='stretch'):
                if bd_reason:
                    ok = db.add_blackout_date(str(bd_date),bd_reason)
                    if ok: st.success(f"{bd_date} marked as blackout."); st.rerun()
                    else:  st.error("That date is already a blackout date.")
                else: st.error("Please provide a reason.")

# ══════════════════════════════════════════════════════════════════════════════
# STUDENT PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_student_dashboard():
    user = st.session_state.user
    header(f"👋 Welcome, {user['name']}","Your lab activity overview")
    maybe_show_ann_popup()

    my_att   = db.get_attendance_for_student(user["id"])
    my_bk    = db.get_bookings_for_student(user["id"])
    pending  = [b for b in my_bk if b["status"]=="pending"]
    approved = [b for b in my_bk if b["status"]=="approved" and b["date"]>=str(date.today())]
    active   = [a for a in my_att if not a["checked_out"] and a["date"]==str(date.today())]

    c1,c2,c3,c4 = st.columns(4)
    metric(c1,len(my_att),"Total Visits")
    metric(c2,len(my_bk),"My Bookings")
    metric(c3,len(pending),"Pending Requests")
    metric(c4,len(approved),"Upcoming Approved")

    if active:
        late_note = " · ⚠️ You were marked late" if active[0].get("late") else ""
        st.success(f"🟢 Currently in lab at **{active[0]['workstation']}** since {active[0]['time']}{late_note}")

    st.markdown("---")
    st.subheader("Recent Activity")
    if my_att:
        df = pd.DataFrame(my_att)
        wanted=[c for c in ["date","time","type","workstation","status","late","checkout_time"] if c in df.columns]
        st.dataframe(df[wanted].head(10),width='stretch',hide_index=True)
    else: st.info("No visits yet.")

    st.subheader("Upcoming Approved Bookings")
    if approved:
        df2=pd.DataFrame(approved)[["id","date","time_slot"]]
        df2.columns=["Booking ID","Date","Time Slot"]
        st.dataframe(df2,width='stretch',hide_index=True)
    else: st.info("No upcoming bookings.")


def page_book_slot():
    user = st.session_state.user
    header("🗓️ Book a Lab Slot", f"Up to {MAX_BOOK_DAYS} days ahead")

    # ── Interactive availability grid ─────────────────────────────────────────
    st.subheader("📊 Click a slot to book it")
    st.caption("🟢 Available — click to select  |  🔴 Full — cannot book")

    for offset in range(MAX_BOOK_DAYS + 1):
        chk = date.today() + timedelta(days=offset)
        if db.is_blackout(str(chk)):
            st.markdown(f"**{chk.strftime('%A, %d %b')}** 🚫 Lab Closed (Blackout Day)")
            continue

        st.markdown(f"**📅 {chk.strftime('%A, %d %b %Y')}**")
        cols = st.columns(len(TIME_SLOTS))
        for i, slot in enumerate(TIME_SLOTS):
            cnt   = db.slot_booking_count(str(chk), slot)
            avail = MAX_PER_SLOT - cnt
            is_selected = (st.session_state.sel_book_date == str(chk) and
                           st.session_state.sel_book_slot == slot)
            with cols[i]:
                if avail > 0:
                    # Highlight selected slot
                    label = f"{'🔵' if is_selected else '🟢'} {slot}\n{avail} left"
                    if st.button(label, key=f"slot_{chk}_{i}",
                                 type="primary" if is_selected else "secondary",
                                 use_container_width=False):
                        st.session_state.sel_book_date = str(chk)
                        st.session_state.sel_book_slot = slot
                        st.rerun()
                else:
                    st.markdown(
                        f"<div style='text-align:center;font-size:.72rem;padding:.4rem;"
                        f"border:1px solid #f5c2c7;border-radius:8px;color:#721c24;"
                        f"background:#f8d7da'>{slot}<br><b>🔴 Full</b></div>",
                        unsafe_allow_html=True)
        st.markdown("")

    # ── Booking form — pre-filled if slot was clicked ─────────────────────────
    st.markdown("---")
    if st.session_state.sel_book_date and st.session_state.sel_book_slot:
        st.info(f"✅ Selected: **{st.session_state.sel_book_slot}** on "
                f"**{date.fromisoformat(st.session_state.sel_book_date).strftime('%A, %d %b %Y')}**"
                f" — fill in your purpose below and submit.")

    with st.form("bkf"):
        c1, c2 = st.columns(2)
        # Pre-fill date and slot from clicked selection, else default
        default_date = (date.fromisoformat(st.session_state.sel_book_date)
                        if st.session_state.sel_book_date else date.today())
        default_slot_idx = (TIME_SLOTS.index(st.session_state.sel_book_slot)
                            if st.session_state.sel_book_slot in TIME_SLOTS else 0)
        bk_date = c1.date_input("Date", value=default_date,
                                 min_value=date.today(),
                                 max_value=date.today() + timedelta(days=MAX_BOOK_DAYS))
        bk_slot = c2.selectbox("Time Slot", TIME_SLOTS, index=default_slot_idx)
        purpose = st.text_area("Purpose / Reason (max 300 chars)", max_chars=300)
        if st.form_submit_button("📩 Submit Request", width='stretch'):
            if db.is_blackout(str(bk_date)):
                st.error("⛔ That date is a blackout day — the lab is closed.")
            elif db.slot_booking_count(str(bk_date), bk_slot) >= MAX_PER_SLOT:
                st.error("That slot is full. Please choose another.")
            else:
                bks      = db.get_bookings_for_student(user["id"])
                conflict = any(b["date"] == str(bk_date) and b["time_slot"] == bk_slot
                               and b["status"] != "rejected" for b in bks)
                if conflict:
                    st.error("You already have a request for that slot.")
                else:
                    db.create_booking(next_booking_id(), user["id"], user["name"],
                                      str(bk_date), bk_slot, purpose)
                    for admin in db.get_all_users("admin"):
                        notify_and_email(admin["id"],
                            f"New booking from {user['name']} — {bk_date} {bk_slot}", "info")
                    # Clear selection after successful submit
                    st.session_state.sel_book_date = None
                    st.session_state.sel_book_slot = None
                    st.success("✅ Request submitted! You'll be notified when approved.")

    st.markdown("---")
    st.subheader("My Booking History")
    bks=db.get_bookings_for_student(user["id"])
    if bks:
        df=pd.DataFrame(bks)[["id","date","time_slot","status","purpose"]]
        df.columns=["ID","Date","Time Slot","Status","Purpose"]
        st.dataframe(df,width='stretch',hide_index=True)
        # cancel pending bookings
        pending=[b for b in bks if b["status"]=="pending"]
        if pending:
            st.markdown("**Cancel a pending booking:**")
            cancel_label=st.selectbox("Select booking to cancel",
                [f"{b['id']} — {b['date']} {b['time_slot']}" for b in pending])
            cancel_id=cancel_label.split(" — ")[0]
            if st.button("❌ Cancel This Booking",width='stretch'):
                db.update_booking_status(cancel_id,"rejected","Cancelled by student")
                db.add_audit(user["id"],"CANCEL_BOOKING",cancel_id)
                st.success("Booking cancelled."); st.rerun()
    else: st.info("No bookings yet.")


def page_my_history():
    user=st.session_state.user
    header("📋 My Visit History")
    att=db.get_attendance_for_student(user["id"])
    if att:
        df=pd.DataFrame(att)
        wanted=[c for c in ["date","time","type","workstation","status","late","checkout_time"] if c in df.columns]
        st.dataframe(df[wanted],width='stretch',hide_index=True)
        st.download_button("📥 Download CSV",df.to_csv(index=False),"my_visits.csv","text/csv")
    else: st.info("No visit records yet.")

# ══════════════════════════════════════════════════════════════════════════════
# ASSIGNMENTS — LECTURER / ADMIN
# ══════════════════════════════════════════════════════════════════════════════
def page_assignments_staff():
    user = st.session_state.user
    header("📝 Assignments","Create, manage and grade lab assignments")

    tab1, tab2, tab3 = st.tabs(["📋 All Assignments","➕ Create Assignment","✏️ Grade Submissions"])

    # ALL ASSIGNMENTS ─────────────────────────────────────────────────────────
    with tab1:
        q    = search_box("Search by title or course...")
        asgs = db.get_all_assignments()
        if user["role"] == "lecturer":
            asgs = [a for a in asgs if a["created_by"] == user["id"]]
        if q:
            asgs = [a for a in asgs if q.lower() in a["title"].lower()
                    or q.lower() in a["course"].lower()]
        if asgs:
            rows = []
            for a in asgs:
                subs      = db.get_submissions_for_assignment(a["id"])
                graded    = sum(1 for s in subs if s["grade"] is not None)
                past_due  = date.today() > date.fromisoformat(a["deadline"][:10])
                rows.append({
                    "ID": a["id"], "Title": a["title"], "Course": a["course"],
                    "Deadline": a["deadline"][:10],
                    "Submissions": f"{len(subs)} ({graded} graded)",
                    "Status": "⌛ Past Due" if past_due else "✅ Open",
                    "Active": "✅" if a["active"] else "🗃️"
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            # archive
            active_asgs = [a for a in asgs if a["active"]]
            if active_asgs:
                arc_label = st.selectbox("Archive an assignment",
                    ["-- select --"] + [f"{a['id']} — {a['title']}" for a in active_asgs])
                if arc_label != "-- select --" and st.button("🗃️ Archive Selected"):
                    db.deactivate_assignment(arc_label.split(" — ")[0])
                    st.success("Assignment archived."); st.rerun()
        else:
            st.info("No assignments yet.")

    # CREATE ASSIGNMENT ───────────────────────────────────────────────────────
    with tab2:
        sessions = db.get_all_sessions()
        if user["role"] == "lecturer":
            sessions = [s for s in sessions if s["lecturer"] == user["name"]]
        courses = sorted(set(s["course"] for s in sessions)) or ["General"]

        with st.form("create_asg"):
            c1, c2 = st.columns(2)
            title       = c1.text_input("Assignment Title *")
            course      = c2.selectbox("Course *", courses)
            deadline    = c1.date_input("Deadline *", min_value=date.today())
            deadline_t  = c2.time_input("Deadline Time", value=datetime.strptime("23:59","%H:%M").time())
            max_score   = c1.number_input("Max Score", 1, 100, 100)
            link_sess   = c2.selectbox("Link to Session (optional)",
                ["None"] + [f"{s['id']} | {s['course']} | {s['date']}" for s in sessions])
            description = st.text_area("Instructions / Description *")

            if st.form_submit_button("📤 Publish Assignment", width='stretch'):
                if title and description:
                    from uuid import uuid4
                    aid        = f"ASG{db.assignment_count()+1:04d}"
                    sess_id    = link_sess.split(" | ")[0] if link_sess != "None" else ""
                    deadline_str = f"{deadline} {deadline_t.strftime('%H:%M')}"
                    db.create_assignment(aid, title, description, course, sess_id,
                                         user["id"], user["name"], deadline_str, max_score)
                    # notify all students
                    for stu in db.get_all_users("student"):
                        notify_and_email(stu["id"],
                            f"📝 New assignment: {title} — due {deadline}",
                            "info", f"SimLab: New Assignment — {title}",
                            f"<h3>{title}</h3><p><b>Course:</b> {course}</p>"
                            f"<p><b>Deadline:</b> {deadline_str}</p>"
                            f"<p><b>Instructions:</b><br>{description}</p>"
                            f"<p>Log in to SimLab to submit your work.</p>")
                    db.add_audit(user["id"], "CREATE_ASSIGNMENT", aid)
                    st.success(f"Assignment **{aid}** published and students notified!"); st.rerun()
                else:
                    st.error("Title and description are required.")

    # GRADE SUBMISSIONS ───────────────────────────────────────────────────────
    with tab3:
        asgs = db.get_all_assignments()
        if user["role"] == "lecturer":
            asgs = [a for a in asgs if a["created_by"] == user["id"]]
        if not asgs:
            st.info("No assignments to grade.")
        else:
            asg_label = st.selectbox("Select Assignment",
                [f"{a['id']} — {a['title']} ({a['course']})" for a in asgs])
            asg_id  = asg_label.split(" — ")[0]
            asg     = db.get_assignment(asg_id)
            subs    = db.get_submissions_for_assignment(asg_id)

            if not subs:
                st.info("No submissions yet for this assignment.")
            else:
                graded   = [s for s in subs if s["grade"] is not None]
                ungraded = [s for s in subs if s["grade"] is None]
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Submissions", len(subs))
                c2.metric("Graded",   len(graded))
                c3.metric("Ungraded", len(ungraded))

                st.markdown("---")
                filter_g  = st.radio("Show", ["All","Ungraded only","Graded only"], horizontal=True)
                show_subs = subs
                if filter_g == "Ungraded only": show_subs = ungraded
                if filter_g == "Graded only":   show_subs = graded

                for sub in show_subs:
                    with st.expander(
                        f"{'✅' if sub['grade'] is not None else '⏳'} "
                        f"{sub['student_name']} ({sub['student_id']}) — "
                        f"submitted {str(sub['submitted_at'])[:16]}"
                    ):
                        c1, c2 = st.columns([3,1])
                        c1.write(f"**File:** {sub['filename']}")
                        if sub["file_data"]:
                            c2.download_button("📥 Download",
                                data=sub["file_data"],
                                file_name=sub["filename"],
                                mime=sub["file_type"],
                                key=f"dl_{sub['id']}")

                        if sub["grade"] is not None:
                            st.success(f"**Grade:** {sub['grade']}/{asg['max_score']}  |  "
                                       f"**Feedback:** {sub['feedback']}")
                            st.caption(f"Graded by {sub['graded_by']} at "
                                       f"{str(sub['graded_at'])[:16]}")

                        with st.form(f"grade_form_{sub['id']}"):
                            gc1, gc2 = st.columns(2)
                            g_score  = gc1.number_input("Score *", 0.0, float(asg["max_score"]),
                                                        value=float(sub["grade"]) if sub["grade"] else 0.0,
                                                        step=0.5, key=f"gs_{sub['id']}")
                            g_fb     = gc2.text_area("Feedback", value=sub.get("feedback",""),
                                                     key=f"gf_{sub['id']}")
                            if st.form_submit_button("💾 Save Grade", width='stretch'):
                                db.grade_submission(asg_id, sub["student_id"],
                                                    g_score, g_fb, user["name"])
                                notify_and_email(sub["student_id"],
                                    f"Your submission for '{asg['title']}' has been graded: "
                                    f"{g_score}/{asg['max_score']}",
                                    "success", f"SimLab: Assignment Graded — {asg['title']}",
                                    f"<h3>Assignment: {asg['title']}</h3>"
                                    f"<p><b>Score:</b> {g_score} / {asg['max_score']}</p>"
                                    f"<p><b>Feedback:</b><br>{g_fb}</p>")
                                db.add_audit(user["id"], "GRADE_SUBMISSION",
                                             f"asg={asg_id} stu={sub['student_id']}")
                                st.success("Grade saved!"); st.rerun()

                # bulk export grades
                st.markdown("---")
                st.subheader("📊 Export Grades")
                if graded:
                    grade_rows = [{"Student ID":   s["student_id"],
                                   "Student Name": s["student_name"],
                                   "Score":        s["grade"],
                                   "Max Score":    asg["max_score"],
                                   "Percentage":   f"{(s['grade']/asg['max_score']*100):.1f}%",
                                   "Feedback":     s["feedback"],
                                   "Submitted":    str(s["submitted_at"])[:16],
                                   "Graded":       str(s["graded_at"])[:16] if s["graded_at"] else ""}
                                  for s in graded]
                    gdf = pd.DataFrame(grade_rows)
                    c1, c2 = st.columns(2)
                    c1.download_button("📥 CSV", gdf.to_csv(index=False),
                                       f"grades_{asg_id}.csv", "text/csv", width='stretch')
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as w:
                        gdf.to_excel(w, index=False, sheet_name="Grades")
                    c2.download_button("📊 Excel", buf.getvalue(),
                                       f"grades_{asg_id}.xlsx",
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       width='stretch')
                else:
                    st.info("No graded submissions yet to export.")


# ══════════════════════════════════════════════════════════════════════════════
# ASSIGNMENTS — STUDENT
# ══════════════════════════════════════════════════════════════════════════════
def page_assignments_student():
    user = st.session_state.user
    header("📝 My Assignments","View, submit and track your lab assignments")

    tab1, tab2 = st.tabs(["📋 Available Assignments","📊 My Grades"])

    # AVAILABLE & SUBMIT ──────────────────────────────────────────────────────
    with tab1:
        asgs = db.get_all_assignments(active_only=True)
        if not asgs:
            st.info("No assignments published yet.")
        else:
            for asg in asgs:
                existing = db.get_submission(asg["id"], user["id"])
                past_due = date.today() > date.fromisoformat(asg["deadline"][:10])
                status   = ("✅ Submitted" if existing and existing["grade"] is None else
                            "🏆 Graded"   if existing and existing["grade"] is not None else
                            "⌛ Past Due" if past_due else "📤 Pending")

                with st.expander(f"{status}  ·  **{asg['title']}**  —  {asg['course']}  "
                                 f"|  Due: {asg['deadline'][:16]}"):
                    st.write(f"**Instructions:** {asg['description']}")
                    st.write(f"**Max Score:** {asg['max_score']}  |  **Lecturer:** {asg['lecturer']}")

                    if existing and existing["grade"] is not None:
                        pct = existing["grade"] / asg["max_score"] * 100
                        st.success(f"**Your Grade: {existing['grade']}/{asg['max_score']} ({pct:.1f}%)**")
                        if existing["feedback"]:
                            st.info(f"**Feedback:** {existing['feedback']}")

                    if existing:
                        st.write(f"📎 Current submission: **{existing['filename']}** "
                                 f"· submitted {str(existing['submitted_at'])[:16]}")
                        if existing["file_data"]:
                            st.download_button("📥 Download My Submission",
                                data=existing["file_data"], file_name=existing["filename"],
                                mime=existing["file_type"], key=f"mydl_{asg['id']}")

                    if not past_due:
                        with st.form(f"submit_{asg['id']}"):
                            label    = "📤 Replace Submission" if existing else "📤 Submit Assignment"
                            uploaded = st.file_uploader("Upload your work (PDF or DOCX)",
                                type=["pdf","docx"], key=f"up_{asg['id']}")
                            if st.form_submit_button(label, width='stretch'):
                                if uploaded:
                                    file_bytes = uploaded.read()
                                    if len(file_bytes) > 10 * 1024 * 1024:
                                        st.error("File too large. Maximum size is 10 MB.")
                                    else:
                                        if existing:
                                            db.update_submission_file(
                                                asg["id"], user["id"],
                                                uploaded.name, file_bytes, uploaded.type)
                                            action = "resubmitted"
                                        else:
                                            sub_id = f"SUB{db.submission_count()+1:05d}"
                                            db.create_submission(sub_id, asg["id"], user["id"],
                                                                 user["name"], uploaded.name,
                                                                 file_bytes, uploaded.type)
                                            action = "submitted"
                                        lec = next((u for u in db.get_all_users()
                                                    if u["name"] == asg["lecturer"]), None)
                                        if lec:
                                            notify_and_email(lec["id"],
                                                f"{user['name']} {action} '{asg['title']}'", "info",
                                                f"SimLab: New Submission — {asg['title']}",
                                                f"<p>{user['name']} ({user['id']}) has {action} "
                                                f"their work for <b>{asg['title']}</b>. "
                                                f"Log in to review and grade.</p>")
                                        db.add_audit(user["id"], "SUBMIT_ASSIGNMENT", asg["id"])
                                        st.success(f"✅ Assignment {action} successfully!")
                                        st.rerun()
                                else:
                                    st.error("Please select a file to upload.")
                    elif not existing:
                        st.error("⌛ Deadline has passed. Submissions are closed.")

    # MY GRADES ───────────────────────────────────────────────────────────────
    with tab2:
        my_subs = db.get_submissions_for_student(user["id"])
        if not my_subs:
            st.info("No submissions yet.")
        else:
            graded = [s for s in my_subs if s["grade"] is not None]
            if graded:
                avg = sum(s["grade"] / s["max_score"] * 100 for s in graded) / len(graded)
                c1, c2, c3 = st.columns(3)
                c1.metric("Assignments Submitted", len(my_subs))
                c2.metric("Graded",                len(graded))
                c3.metric("Average Score",         f"{avg:.1f}%")
                st.markdown("---")

            rows = []
            for s in my_subs:
                past_due = date.today() > date.fromisoformat(s["deadline"][:10])
                if s["grade"] is not None:
                    status = f"🏆 {s['grade']}/{s['max_score']} ({s['grade']/s['max_score']*100:.0f}%)"
                elif past_due:
                    status = "⌛ Awaiting Grade"
                else:
                    status = "✅ Submitted"
                rows.append({
                    "Assignment": s["assignment_title"],
                    "Course":     s["course"],
                    "Deadline":   s["deadline"][:10],
                    "Submitted":  str(s["submitted_at"])[:16],
                    "File":       s["filename"],
                    "Result":     status,
                    "Feedback":   s.get("feedback","") or "—"
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, width='stretch', hide_index=True)
            st.download_button("📥 Download My Grades",
                               df.to_csv(index=False), "my_grades.csv", "text/csv")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state.logged_in:
    auth_pages()
else:
    check_timeout()
    db.auto_reject_expired()   # single authoritative call
    page = sidebar_nav()
    role = st.session_state.user["role"]

    routes = {
        "admin": {
            "📊 Dashboard":          page_admin_dashboard,
            "🔔 Notifications":      page_notifications,
            "📢 Announcements":      page_announcements,
            "🎓 Students":           page_students,
            "📅 Lab Sessions":       page_lab_sessions,
            "🗓️ Bookings":           page_bookings,
            "🖥️ Workstations":       page_workstations,
            "📋 Attendance":         page_attendance,
            "📝 Assignments":        page_assignments_staff,
            "📈 Reports":            page_reports,
            "🚫 Blackout Dates":     page_blackout_dates,
            "⚙️ Profile & Settings": page_profile,
        },
        "lecturer": {
            "📊 Dashboard":          page_lecturer_dashboard,
            "🔔 Notifications":      page_notifications,
            "📢 Announcements":      page_announcements,
            "📅 My Sessions":        page_lab_sessions,
            "📋 Attendance":         page_attendance,
            "📝 Assignments":        page_assignments_staff,
            "⚙️ Profile & Settings": page_profile,
        },
        "student": {
            "📊 My Dashboard":       page_student_dashboard,
            "🔔 Notifications":      page_notifications,
            "📢 Announcements":      page_announcements,
            "🗓️ Book a Slot":        page_book_slot,
            "📋 My History":         page_my_history,
            "📝 Assignments":        page_assignments_student,
            "🪪 My QR Code":         page_my_qr,
            "⚙️ Profile & Settings": page_profile,
        },
    }

    fn = routes.get(role,{}).get(page)
    if fn: fn()
