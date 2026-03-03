"""
SimLab Manager  —  app.py
Streamlit frontend — all data via database.py (SQLite)
Run:  streamlit run app.py
"""
import os
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import plotly.express as px
import hashlib

import database as db

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SimLab Manager",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Constants ─────────────────────────────────────────────────────────────────
ADMIN_CODE    = os.environ.get("SIMLAB_ADMIN_CODE", "SIMLAB2024")
TIME_SLOTS    = ["08:00–09:00","09:00–10:00","10:00–11:00","11:00–12:00",
                 "13:00–14:00","14:00–15:00","15:00–16:00","16:00–17:00"]
MAX_PER_SLOT  = 5
MAX_BOOK_DAYS = 2

SECURITY_QUESTIONS = [
    "What is your pet's name?",
    "What city were you born in?",
    "What is your mother's maiden name?",
    "What was your first school's name?",
]

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
  section[data-testid="stSidebar"]{background:#1e3a5f!important;}
  section[data-testid="stSidebar"] *{color:white!important;}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
for k, v in [("logged_in", False), ("user", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── Shared helpers ────────────────────────────────────────────────────────────
def metric(col, val, label):
    col.markdown(f'<div class="metric-card"><div class="val">{val}</div>'
                 f'<div class="lbl">{label}</div></div>', unsafe_allow_html=True)

def header(title, sub=""):
    sub_html = f"<p>{sub}</p>" if sub else ""
    st.markdown(f'<div class="main-header"><h1>{title}</h1>{sub_html}</div>',
                unsafe_allow_html=True)

def search_box(placeholder="Search..."):
    return st.text_input("🔍", placeholder=placeholder, label_visibility="collapsed")

def next_session_id():  return f"SES{db.session_count()+1:04d}"
def next_booking_id():  return f"BK{db.booking_count()+1:04d}"
def next_att_id():      return f"ATT{db.attendance_count()+1:05d}"

# ══════════════════════════════════════════════════════════════════════════════
# AUTH / LANDING
# ══════════════════════════════════════════════════════════════════════════════
def auth_pages():
    db.auto_reject_expired()
    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        st.markdown("""
        <div class="main-header" style="text-align:center">
          <h1>🖥️ SimLab Manager</h1>
          <p>Simulation Laboratory Management System</p>
        </div>""", unsafe_allow_html=True)

        tab_l, tab_r, tab_rst = st.tabs(["🔑 Login", "📝 Register", "🔒 Reset Password"])

        # LOGIN
        with tab_l:
            with st.form("lf"):
                uid = st.text_input("Student / Staff ID", placeholder="e.g. STU001")
                pw  = st.text_input("Password", type="password")
                if st.form_submit_button("Login", use_container_width=True):
                    u = db.get_user_by_id_pw(uid, hash_pw(pw))
                    if u:
                        st.session_state.logged_in = True
                        st.session_state.user = u
                        db.add_audit(uid, "LOGIN")
                        st.rerun()
                    else:
                        st.error("Invalid ID or password.")
            st.caption("Demo — Admin: ADMIN001/admin123 · Lecturer: LEC001/lec123 · Student: STU001/stu123")

        # REGISTER
        with tab_r:
            role_choice = st.selectbox("I am registering as a...", ["student","lecturer","admin"])
            with st.form("rf"):
                c1, c2 = st.columns(2)
                new_id    = c1.text_input("ID *",               placeholder="e.g. STU050")
                new_name  = c2.text_input("Full Name *")
                new_email = c1.text_input("Email *")
                new_pw    = c2.text_input("Password *",         type="password")
                new_pw2   = c1.text_input("Confirm Password *", type="password")
                sec_q     = c2.selectbox("Security Question",   SECURITY_QUESTIONS)
                sec_a     = st.text_input("Security Answer *")
                invite    = st.text_input("Admin Invite Code *", type="password") \
                            if role_choice == "admin" else None
                if st.form_submit_button("Create Account", use_container_width=True):
                    errs = []
                    if not all([new_id, new_name, new_email, new_pw, sec_a]):
                        errs.append("All fields are required.")
                    if new_pw != new_pw2:
                        errs.append("Passwords do not match.")
                    if len(new_pw) < 6:
                        errs.append("Password must be at least 6 characters.")
                    if role_choice == "admin" and invite != ADMIN_CODE:
                        errs.append("Invalid admin invite code.")
                    if db.user_exists(new_id):
                        errs.append("That ID is already registered.")
                    if errs:
                        for e in errs: st.error(e)
                    else:
                        ok = db.create_user(new_id, new_name, new_email,
                                            hash_pw(new_pw), role_choice,
                                            sec_q, hash_pw(sec_a.lower().strip()))
                        if ok:
                            db.add_audit(new_id, "REGISTER", f"role={role_choice}")
                            st.success(f"Account created! Log in with **{new_id}**.")
                        else:
                            st.error("Registration failed — ID may already exist.")

        # RESET PASSWORD
        with tab_rst:
            with st.form("rsf"):
                r_id = st.text_input("Your ID *")
                u_chk = db.get_user(r_id) if r_id else None
                if u_chk:
                    st.info(f"Security Question: **{u_chk['security_q']}**")
                r_ans  = st.text_input("Security Answer *")
                r_pw   = st.text_input("New Password *",      type="password")
                r_pw2  = st.text_input("Confirm Password *",  type="password")
                if st.form_submit_button("Reset Password", use_container_width=True):
                    u = db.get_user(r_id)
                    if not u:
                        st.error("ID not found.")
                    elif u.get("security_a") != hash_pw(r_ans.lower().strip()):
                        st.error("Security answer is incorrect.")
                    elif r_pw != r_pw2:
                        st.error("Passwords do not match.")
                    elif len(r_pw) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        db.update_password(r_id, hash_pw(r_pw))
                        db.add_audit(r_id, "PASSWORD_RESET")
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
            st.markdown(f"🔔 **{unread} unread notification{'s' if unread>1 else ''}**")
        st.markdown("---")

        page_map = {
            "admin":    ["📊 Dashboard","🔔 Notifications","🎓 Students",
                         "📅 Lab Sessions","🗓️ Bookings","🖥️ Workstations",
                         "📋 Attendance","📈 Reports","⚙️ Profile & Settings"],
            "lecturer": ["📊 Dashboard","🔔 Notifications","📅 Lab Sessions",
                         "📋 Attendance","⚙️ Profile & Settings"],
            "student":  ["📊 My Dashboard","🔔 Notifications","🗓️ Book a Slot",
                         "📋 My History","⚙️ Profile & Settings"],
        }
        choice = st.radio("Nav", page_map[role], label_visibility="collapsed")
        st.markdown("---")
        if st.button("🚪 Logout", use_container_width=True):
            db.add_audit(user["id"], "LOGOUT")
            st.session_state.logged_in = False
            st.session_state.user = None
            st.rerun()
    return choice

# ══════════════════════════════════════════════════════════════════════════════
# SHARED PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_notifications():
    user = st.session_state.user
    header("🔔 Notifications")
    notifs = db.get_notifications(user["id"])
    if not notifs:
        st.info("No notifications yet."); return
    c1, c2 = st.columns([6,1])
    c1.write(f"**{sum(1 for n in notifs if not n['read'])} unread** of {len(notifs)} total")
    if c2.button("Mark all read"):
        db.mark_all_read(user["id"]); st.rerun()
    for n in notifs:
        css = {"info":"nb-info","success":"nb-success",
               "warning":"nb-warning","error":"nb-error"}.get(n["type"],"nb-info")
        dot = "" if n["read"] else "🔵 "
        st.markdown(f'<div class="nb {css}">{dot}{n["message"]}<br>'
                    f'<small style="opacity:.7">{str(n["created_at"])[:16]}</small></div>',
                    unsafe_allow_html=True)
        if not n["read"]:
            if st.button("Mark read", key=f"mr_{n['id']}"):
                db.mark_notification_read(n["id"]); st.rerun()


def page_profile():
    user = st.session_state.user
    header("⚙️ Profile & Settings")
    tab1, tab2 = st.tabs(["👤 My Profile", "🔑 Change Password"])
    with tab1:
        with st.form("pf"):
            c1, c2 = st.columns(2)
            new_name  = c1.text_input("Full Name",  value=user["name"])
            new_email = c2.text_input("Email",       value=user.get("email",""))
            sq_index  = SECURITY_QUESTIONS.index(user["security_q"]) \
                        if user.get("security_q") in SECURITY_QUESTIONS else 0
            new_sec_q = c1.selectbox("Security Question", SECURITY_QUESTIONS, index=sq_index)
            new_sec_a = c2.text_input("New Security Answer (blank = keep current)")
            if st.form_submit_button("Save Changes", use_container_width=True):
                sec_a_hash = hash_pw(new_sec_a.lower().strip()) if new_sec_a.strip() else None
                db.update_user(user["id"], new_name, new_email, new_sec_q, sec_a_hash)
                db.add_audit(user["id"], "PROFILE_UPDATE")
                st.session_state.user = db.get_user(user["id"])
                st.success("Profile updated!")
    with tab2:
        with st.form("cpf"):
            old_pw  = st.text_input("Current Password *", type="password")
            new_pw  = st.text_input("New Password *",      type="password")
            new_pw2 = st.text_input("Confirm Password *",  type="password")
            if st.form_submit_button("Change Password", use_container_width=True):
                u = db.get_user(user["id"])
                if u["password"] != hash_pw(old_pw):
                    st.error("Current password is incorrect.")
                elif new_pw != new_pw2:
                    st.error("Passwords do not match.")
                elif len(new_pw) < 6:
                    st.error("Must be at least 6 characters.")
                else:
                    db.update_password(user["id"], hash_pw(new_pw))
                    db.add_audit(user["id"], "PASSWORD_CHANGE")
                    st.success("Password changed!")

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN / LECTURER PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_admin_dashboard():
    db.auto_reject_expired()
    header("📊 Dashboard", "Overview of lab activity")
    today   = str(date.today())
    students = db.get_all_users("student")
    t_sess   = db.get_sessions_on_date(today)
    pending  = [b for b in db.get_all_bookings() if b["status"]=="pending"]
    t_att    = db.get_active_checkins(today)
    all_att  = [a for a in db.get_all_attendance() if a["date"]==today]

    c1,c2,c3,c4,c5 = st.columns(5)
    metric(c1, len(students),    "Registered Students")
    metric(c2, len(t_sess),      "Today's Sessions")
    metric(c3, len(pending),     "Pending Bookings")
    metric(c4, len(all_att),     "Check-ins Today")
    metric(c5, len(t_att),       "Currently In Lab")

    st.markdown("---")
    cl, cr = st.columns(2)
    with cl:
        st.subheader("📅 Today's Sessions")
        if t_sess:
            att_all = db.get_all_attendance()
            for s in t_sess:
                cnt = len([a for a in att_all if a["reference_id"]==s["id"]])
                st.info(f"**{s['course']}** | {s['start_time']}–{s['end_time']} "
                        f"| {cnt}/{s['max_students']} checked in")
        else:
            st.write("No sessions today.")
    with cr:
        st.subheader("🔔 Pending Booking Requests")
        if pending:
            for b in pending[:6]:
                cols = st.columns([4,1,1])
                cols[0].write(f"**{b['student_name']}** — {b['date']} {b['time_slot']}")
                if cols[1].button("✅", key=f"da_{b['id']}"):
                    db.update_booking_status(b["id"], "approved")
                    db.add_notification(b["student_id"],
                        f"Your booking for {b['date']} {b['time_slot']} is approved! ✅","success")
                    st.rerun()
                if cols[2].button("❌", key=f"dr_{b['id']}"):
                    db.update_booking_status(b["id"], "rejected")
                    db.add_notification(b["student_id"],
                        f"Your booking for {b['date']} {b['time_slot']} was not approved.","error")
                    st.rerun()
        else:
            st.write("No pending requests.")


def page_students():
    header("🎓 Student Management")
    tab1, tab2 = st.tabs(["📋 All Students", "➕ Register Student"])
    with tab1:
        q     = search_box("Search by name or ID...")
        studs = db.get_all_users("student")
        if q:
            studs = [s for s in studs if q.lower() in s["name"].lower()
                     or q.lower() in s["id"].lower()]
        if studs:
            df = pd.DataFrame([{"ID":s["id"],"Name":s["name"],"Email":s.get("email","")}
                                for s in studs])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"{len(studs)} student(s)")
        else:
            st.info("No students found.")
    with tab2:
        with st.form("rs"):
            c1,c2 = st.columns(2)
            sid   = c1.text_input("Student ID *")
            name  = c2.text_input("Full Name *")
            email = c1.text_input("Email")
            pw    = c2.text_input("Password *", type="password")
            if st.form_submit_button("Register", use_container_width=True):
                if sid and name and pw:
                    ok = db.create_user(sid, name, email, hash_pw(pw), "student",
                                        SECURITY_QUESTIONS[0], hash_pw("changeme"))
                    if ok:
                        db.add_audit(st.session_state.user["id"], "REGISTER_STUDENT", sid)
                        st.success(f"Student {name} ({sid}) registered!"); st.rerun()
                    else:
                        st.error("ID already exists.")
                else:
                    st.error("Fill all required fields.")


def page_lab_sessions():
    header("📅 Lab Sessions")
    tab1, tab2 = st.tabs(["📋 All Sessions", "➕ Create Session"])
    with tab1:
        q        = search_box("Search by course or lecturer...")
        sessions = db.get_all_sessions()
        if q:
            sessions = [s for s in sessions if q.lower() in s["course"].lower()
                        or q.lower() in s["lecturer"].lower()]
        if sessions:
            att_all = db.get_all_attendance()
            rows = []
            for s in sessions:
                cnt = len([a for a in att_all if a["reference_id"]==s["id"]])
                rows.append({"ID":s["id"],"Course":s["course"],"Date":s["date"],
                             "Time":f"{s['start_time']}–{s['end_time']}",
                             "Lecturer":s["lecturer"],
                             "Attendance":f"{cnt}/{s['max_students']}",
                             "Recurring":bool(s["recurring"])})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No sessions yet.")

    with tab2:
        users     = db.get_all_users()
        lecs      = [u for u in users if u["role"] in ("lecturer","admin")]
        lec_names = [l["name"] for l in lecs]
        sub1, sub2 = st.tabs(["Single Session", "Recurring (Weekly)"])

        with sub1:
            with st.form("ss"):
                c1,c2  = st.columns(2)
                course  = c1.text_input("Course Name / Code *")
                lec     = c2.selectbox("Lecturer", lec_names)
                s_date  = c1.date_input("Date", min_value=date.today())
                s_start = c2.text_input("Start Time *", placeholder="08:00")
                s_end   = c1.text_input("End Time *",   placeholder="10:00")
                max_stu = c2.number_input("Max Students", 1, 20, 15)
                notes   = st.text_area("Notes")
                if st.form_submit_button("Create Session", use_container_width=True):
                    if course and s_start and s_end:
                        conflict = db.sessions_overlap(str(s_date), s_start, s_end)
                        if conflict:
                            st.error(f"Time conflict with **{conflict['course']}** "
                                     f"({conflict['start_time']}–{conflict['end_time']})")
                        else:
                            sid = next_session_id()
                            db.create_session(sid, course, lec, str(s_date),
                                              s_start, s_end, int(max_stu),
                                              notes, st.session_state.user["id"])
                            db.add_audit(st.session_state.user["id"], "CREATE_SESSION", sid)
                            st.success(f"Session **{sid}** created!"); st.rerun()
                    else:
                        st.error("Fill all required fields.")

        with sub2:
            with st.form("rs2"):
                c1,c2    = st.columns(2)
                r_course = c1.text_input("Course *", key="rc")
                r_lec    = c2.selectbox("Lecturer", lec_names, key="rl")
                r_date   = c1.date_input("First Date", min_value=date.today(), key="rd")
                r_start  = c2.text_input("Start Time *", placeholder="08:00", key="rs_t")
                r_end    = c1.text_input("End Time *",   placeholder="10:00", key="re_t")
                r_weeks  = c2.number_input("Weeks", 1, 24, 12)
                r_max    = c1.number_input("Max Students", 1, 20, 15, key="rm")
                r_notes  = st.text_area("Notes", key="rn")
                if st.form_submit_button("Create Recurring Sessions", use_container_width=True):
                    if r_course and r_start and r_end:
                        added = 0
                        for w in range(int(r_weeks)):
                            d_w      = r_date + timedelta(weeks=w)
                            conflict = db.sessions_overlap(str(d_w), r_start, r_end)
                            if conflict:
                                st.warning(f"Week {w+1} ({d_w}) skipped — conflicts with '{conflict['course']}'")
                                continue
                            db.create_session(next_session_id(), r_course, r_lec, str(d_w),
                                              r_start, r_end, int(r_max),
                                              r_notes, st.session_state.user["id"], recurring=True)
                            added += 1
                        st.success(f"{added} session(s) created."); st.rerun()
                    else:
                        st.error("Fill all required fields.")


def page_bookings():
    db.auto_reject_expired()
    header("🗓️ Open-Access Bookings")
    tab1, tab2 = st.tabs(["🔔 Requests", "📅 Slot Overview"])

    with tab1:
        q        = search_box("Search by student name or ID...")
        c1, c2   = st.columns(2)
        f_status = c1.selectbox("Status", ["all","pending","approved","rejected"])
        f_date   = c2.date_input("Date filter", value=None)

        bks = db.get_all_bookings()
        if f_status != "all": bks = [b for b in bks if b["status"]==f_status]
        if f_date:            bks = [b for b in bks if b["date"]==str(f_date)]
        if q:
            bks = [b for b in bks if q.lower() in b["student_name"].lower()
                   or q.lower() in b["student_id"].lower()]

        if bks:
            for b in bks:
                icon = {"pending":"🟡","approved":"🟢","rejected":"🔴"}.get(b["status"],"⚪")
                cols = st.columns([4,2,1,1])
                cols[0].write(f"**{b['student_name']}** (`{b['student_id']}`) — "
                              f"{b['date']} @ {b['time_slot']}")
                cols[1].write(f"{icon} {b['status'].title()}")
                if b["status"] == "pending":
                    if cols[2].button("✅", key=f"ap_{b['id']}"):
                        db.update_booking_status(b["id"], "approved")
                        db.add_notification(b["student_id"],
                            f"Your booking for {b['date']} {b['time_slot']} is approved! ✅","success")
                        st.rerun()
                    if cols[3].button("❌", key=f"rj_{b['id']}"):
                        db.update_booking_status(b["id"], "rejected")
                        db.add_notification(b["student_id"],
                            f"Your booking for {b['date']} {b['time_slot']} was not approved.","error")
                        st.rerun()
        else:
            st.info("No bookings match your filters.")

    with tab2:
        st.subheader("Slot Availability — Next 3 Days")
        for offset in range(3):
            chk = date.today() + timedelta(days=offset)
            st.markdown(f"**📅 {chk.strftime('%A, %d %B %Y')}**")
            cols = st.columns(len(TIME_SLOTS))
            for i, slot in enumerate(TIME_SLOTS):
                cnt   = db.slot_booking_count(str(chk), slot)
                avail = MAX_PER_SLOT - cnt
                cols[i].markdown(
                    f"<div style='text-align:center;font-size:.73rem'>{slot}<br>"
                    f"<b style='color:{'#155724' if avail>0 else '#721c24'}'>"
                    f"{'✅' if avail>0 else '🔴'} {avail} left</b></div>",
                    unsafe_allow_html=True)
            st.markdown("")


def page_workstations():
    header("🖥️ Workstation Management")
    ws_list = db.get_all_workstations()
    tab1, tab2 = st.tabs(["📋 Status Board", "📜 Usage History"])

    with tab1:
        avail  = sum(1 for w in ws_list if w["status"]=="available")
        in_use = sum(1 for w in ws_list if w["status"]=="in-use")
        maint  = sum(1 for w in ws_list if w["status"]=="maintenance")
        c1,c2,c3 = st.columns(3)
        c1.success(f"Available: {avail}")
        c2.warning(f"In Use: {in_use}")
        c3.error(f"Maintenance: {maint}")
        st.markdown("---")
        cols = st.columns(4)
        for i, ws in enumerate(ws_list):
            with cols[i % 4]:
                icon = {"available":"🟢","in-use":"🟡","maintenance":"🔴"}.get(ws["status"],"⚪")
                st.markdown(f"**{icon} {ws['label']}**")
                new_status = st.selectbox("", ["available","in-use","maintenance"],
                    index=["available","in-use","maintenance"].index(ws["status"]),
                    key=f"ws_{ws['id']}", label_visibility="collapsed")
                note = st.text_input("Note", value=ws.get("notes",""),
                                     key=f"wn_{ws['id']}", placeholder="e.g. Screen broken") \
                       if new_status=="maintenance" or ws["status"]=="maintenance" \
                       else ws.get("notes","")
                if new_status!=ws["status"] or note!=ws.get("notes",""):
                    db.update_workstation(ws["id"], new_status, note)
                    st.rerun()

    with tab2:
        q   = search_box("Filter by workstation (e.g. PC-01)...")
        att = db.get_all_attendance()
        if q:
            att = [a for a in att if q.lower() in a.get("workstation","").lower()]
        if att:
            df = pd.DataFrame(att)[["workstation","student_id","student_name","date","time","type"]]
            df.columns = ["Workstation","Stu. ID","Name","Date","Time","Type"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No usage records yet.")


def page_attendance():
    header("📋 Attendance & Check-In / Out")
    tab1, tab2, tab3 = st.tabs(["✅ Check-In", "🚪 Check-Out", "📋 Records & Export"])

    with tab1:
        sessions = db.get_all_sessions()
        bookings = [b for b in db.get_all_bookings() if b["status"]=="approved"]
        avail_ws = [w["label"] for w in db.get_available_workstations()]
        check_type = st.radio("Type", ["Scheduled Session","Open-Access Booking"], horizontal=True)
        with st.form("ci"):
            stu_id = st.text_input("Student ID *")
            if check_type == "Scheduled Session":
                opts = [f"{s['id']} | {s['course']} | {s['date']} {s['start_time']}–{s['end_time']}"
                        for s in sessions]
                sel  = st.selectbox("Session", opts) if opts else st.text_input("No sessions")
            else:
                opts = [f"{b['id']} | {b['student_id']} | {b['date']} {b['time_slot']}"
                        for b in bookings]
                sel  = st.selectbox("Booking", opts) if opts else st.text_input("No approved bookings")
            ws = st.selectbox("Assign Workstation", avail_ws) if avail_ws else st.text_input("None available")
            if st.form_submit_button("Check In", use_container_width=True):
                student = db.get_user(stu_id)
                if not student:
                    st.error("Student ID not found.")
                elif db.student_already_checked_in(stu_id, str(date.today())):
                    st.warning("Student already checked in.")
                else:
                    ref_id = sel.split(" | ")[0] if sel else ""
                    db.create_attendance(next_att_id(), stu_id, student["name"],
                                         check_type, ref_id, ws,
                                         str(date.today()), datetime.now().strftime("%H:%M"))
                    db.set_workstation_status(ws, "in-use")
                    db.add_notification(stu_id,
                        f"Checked in at {ws} · {date.today()} {datetime.now().strftime('%H:%M')}","info")
                    db.add_audit(st.session_state.user["id"], "CHECKIN", stu_id)
                    st.success(f"{student['name']} checked in at {ws}")

    with tab2:
        active = db.get_active_checkins(str(date.today()))
        if not active:
            st.info("No students currently in lab.")
        else:
            st.write(f"**{len(active)} student(s) currently in lab:**")
            for a in active:
                c1, c2 = st.columns([5,1])
                c1.write(f"**{a['student_name']}** (`{a['student_id']}`) — {a['workstation']} since {a['time']}")
                if c2.button("Check Out", key=f"co_{a['id']}"):
                    t_out = datetime.now().strftime("%H:%M")
                    db.checkout_attendance(a["id"], t_out)
                    db.set_workstation_status(a["workstation"], "available")
                    db.add_notification(a["student_id"],
                        f"Checked out of {a['workstation']} at {t_out}","info")
                    db.add_audit(st.session_state.user["id"], "CHECKOUT", a["student_id"])
                    st.rerun()

    with tab3:
        att = db.get_all_attendance()
        if not att:
            st.info("No records yet."); return
        df = pd.DataFrame(att)
        c1,c2,c3 = st.columns(3)
        d_f = c1.date_input("Date",  value=None)
        t_f = c2.selectbox("Type",   ["All","Scheduled Session","Open-Access Booking"])
        s_f = c3.text_input("Student ID / Name")
        if d_f: df = df[df["date"]==str(d_f)]
        if t_f!="All": df = df[df["type"]==t_f]
        if s_f:
            df = df[df["student_id"].str.contains(s_f,case=False) |
                    df["student_name"].str.contains(s_f,case=False)]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("📥 Download CSV", df.to_csv(index=False),
                           "attendance.csv","text/csv", use_container_width=True)


def page_reports():
    header("📈 Reports & Analytics")
    att = db.get_all_attendance()
    if not att:
        st.info("No data yet."); return
    df = pd.DataFrame(att)
    df["date"] = pd.to_datetime(df["date"])

    c1,c2 = st.columns(2)
    with c1:
        st.subheader("Daily Check-ins (Last 14 Days)")
        daily = df.groupby("date").size().reset_index(name="count").tail(14)
        fig = px.bar(daily, x="date", y="count", color_discrete_sequence=["#2d6a9f"])
        fig.update_layout(margin=dict(t=10), height=280)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.subheader("Session vs Open-Access Split")
        tc = df["type"].value_counts().reset_index()
        tc.columns = ["Type","Count"]
        fig2 = px.pie(tc, values="Count", names="Type",
                      color_discrete_sequence=["#1e3a5f","#2d9fd6"])
        fig2.update_layout(margin=dict(t=10), height=280)
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Workstation Usage Frequency")
    wc = df["workstation"].value_counts().reset_index()
    wc.columns = ["Workstation","Uses"]
    fig3 = px.bar(wc, x="Workstation", y="Uses", color_discrete_sequence=["#1e3a5f"])
    fig3.update_layout(margin=dict(t=10), height=260)
    st.plotly_chart(fig3, use_container_width=True)

    st.subheader("Most Active Students")
    top = df.groupby(["student_id","student_name"]).size().reset_index(name="Visits")
    top = top.sort_values("Visits",ascending=False).head(10)
    st.dataframe(top, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# STUDENT PAGES
# ══════════════════════════════════════════════════════════════════════════════
def page_student_dashboard():
    user = st.session_state.user
    header(f"👋 Welcome, {user['name']}", "Your lab activity overview")
    my_att   = db.get_attendance_for_student(user["id"])
    my_bk    = db.get_bookings_for_student(user["id"])
    pending  = [b for b in my_bk if b["status"]=="pending"]
    approved = [b for b in my_bk if b["status"]=="approved" and b["date"]>=str(date.today())]
    active   = [a for a in my_att if not a["checked_out"] and a["date"]==str(date.today())]

    c1,c2,c3,c4 = st.columns(4)
    metric(c1, len(my_att),   "Total Visits")
    metric(c2, len(my_bk),    "My Bookings")
    metric(c3, len(pending),  "Pending Requests")
    metric(c4, len(approved), "Upcoming Approved")

    if active:
        st.success(f"🟢 Currently checked in at **{active[0]['workstation']}** since {active[0]['time']}")

    st.markdown("---")
    st.subheader("Recent Activity")
    if my_att:
        df = pd.DataFrame(my_att)
        wanted = [c for c in ["date","time","type","workstation","status","checkout_time"] if c in df.columns]
        st.dataframe(df[wanted].head(10), use_container_width=True, hide_index=True)
    else:
        st.info("No visits yet.")

    st.subheader("Upcoming Approved Bookings")
    if approved:
        df2 = pd.DataFrame(approved)[["id","date","time_slot"]]
        df2.columns = ["Booking ID","Date","Time Slot"]
        st.dataframe(df2, use_container_width=True, hide_index=True)
    else:
        st.info("No upcoming bookings.")


def page_book_slot():
    user = st.session_state.user
    header("🗓️ Book a Lab Slot", f"Open-access bookings — up to {MAX_BOOK_DAYS} days ahead")

    st.subheader("📊 Real-time Slot Availability")
    for offset in range(MAX_BOOK_DAYS+1):
        chk = date.today() + timedelta(days=offset)
        st.markdown(f"**{chk.strftime('%A, %d %b')}**")
        cols = st.columns(len(TIME_SLOTS))
        for i, slot in enumerate(TIME_SLOTS):
            cnt   = db.slot_booking_count(str(chk), slot)
            avail = MAX_PER_SLOT - cnt
            cols[i].markdown(
                f"<div style='text-align:center;font-size:.72rem'>{slot}<br>"
                f"<b style='color:{'#155724' if avail>0 else '#721c24'}'>"
                f"{'✅' if avail>0 else '🔴'} {avail} left</b></div>",
                unsafe_allow_html=True)
        st.markdown("")

    st.markdown("---")
    with st.form("bkf"):
        c1,c2   = st.columns(2)
        bk_date = c1.date_input("Date", min_value=date.today(),
                                 max_value=date.today()+timedelta(days=MAX_BOOK_DAYS))
        bk_slot = c2.selectbox("Time Slot", TIME_SLOTS)
        purpose = st.text_area("Purpose / Reason (max 300 chars)", max_chars=300)
        if st.form_submit_button("📩 Submit Request", use_container_width=True):
            if db.slot_booking_count(str(bk_date), bk_slot) >= MAX_PER_SLOT:
                st.error("That slot is full. Please choose another.")
            else:
                bks      = db.get_bookings_for_student(user["id"])
                conflict = any(b["date"]==str(bk_date) and b["time_slot"]==bk_slot
                               and b["status"]!="rejected" for b in bks)
                if conflict:
                    st.error("You already have a request for that slot.")
                else:
                    db.create_booking(next_booking_id(), user["id"], user["name"],
                                      str(bk_date), bk_slot, purpose)
                    for admin in db.get_all_users("admin"):
                        db.add_notification(admin["id"],
                            f"New booking from {user['name']} — {bk_date} {bk_slot}","info")
                    st.success("Request submitted! You'll be notified when approved.")

    st.markdown("---")
    st.subheader("My Booking History")
    bks = db.get_bookings_for_student(user["id"])
    if bks:
        df = pd.DataFrame(bks)[["id","date","time_slot","status","purpose"]]
        df.columns = ["ID","Date","Time Slot","Status","Purpose"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No bookings yet.")


def page_my_history():
    user = st.session_state.user
    header("📋 My Visit History")
    att = db.get_attendance_for_student(user["id"])
    if att:
        df = pd.DataFrame(att)
        wanted = [c for c in ["date","time","type","workstation","status","checkout_time"] if c in df.columns]
        st.dataframe(df[wanted], use_container_width=True, hide_index=True)
        st.download_button("📥 Download CSV", df.to_csv(index=False),
                           "my_visits.csv","text/csv")
    else:
        st.info("No visit records yet.")

# ══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state.logged_in:
    auth_pages()
else:
    db.auto_reject_expired()
    page = sidebar_nav()
    role = st.session_state.user["role"]

    routes = {
        "admin": {
            "📊 Dashboard":          page_admin_dashboard,
            "🔔 Notifications":      page_notifications,
            "🎓 Students":           page_students,
            "📅 Lab Sessions":       page_lab_sessions,
            "🗓️ Bookings":           page_bookings,
            "🖥️ Workstations":       page_workstations,
            "📋 Attendance":         page_attendance,
            "📈 Reports":            page_reports,
            "⚙️ Profile & Settings": page_profile,
        },
        "lecturer": {
            "📊 Dashboard":          page_admin_dashboard,
            "🔔 Notifications":      page_notifications,
            "📅 Lab Sessions":       page_lab_sessions,
            "📋 Attendance":         page_attendance,
            "⚙️ Profile & Settings": page_profile,
        },
        "student": {
            "📊 My Dashboard":       page_student_dashboard,
            "🔔 Notifications":      page_notifications,
            "🗓️ Book a Slot":        page_book_slot,
            "📋 My History":         page_my_history,
            "⚙️ Profile & Settings": page_profile,
        },
    }

    fn = routes.get(role, {}).get(page)
    if fn: fn()
