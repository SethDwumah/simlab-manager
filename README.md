# 🖥️ SimLab Manager

Simulation Laboratory Management System for UENR.
Manages scheduled lab sessions, open-access bookings, workstation tracking, and attendance.

---

## 🚀 Deploy to Streamlit Community Cloud (Step-by-Step)

### Step 1 — Push to GitHub

```bash
# In your project folder
git init
git add .
git commit -m "Initial SimLab Manager"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/simlab-manager.git
git push -u origin main
```

### Step 2 — Deploy on Streamlit Cloud

1. Go to **https://share.streamlit.io**
2. Sign in with your GitHub account
3. Click **"New app"**
4. Select your repo: `simlab-manager`
5. Branch: `main`
6. Main file: `app.py`
7. Click **"Deploy"**

### Step 3 — Set your Admin Invite Code (IMPORTANT)

In Streamlit Cloud, after deploying:
1. Go to your app → **⋮ Menu → Settings → Secrets**
2. Add this:
```toml
SIMLAB_ADMIN_CODE = "YOUR_SECRET_CODE_HERE"
```
3. Save and reboot the app

> ⚠️ Without this, the default code `SIMLAB2024` will be used — change it before sharing the URL!

---

## 🏃 Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## 👤 Default Demo Accounts

| Role     | ID       | Password  |
|----------|----------|-----------|
| Admin    | ADMIN001 | admin123  |
| Lecturer | LEC001   | lec123    |
| Student  | STU001   | stu123    |

> Delete or change these after going live.

---

## 📁 File Structure

```
simlab-manager/
├── app.py              # Main Streamlit app
├── database.py         # SQLite database layer
├── requirements.txt    # Python dependencies
├── .gitignore          # Excludes DB and secrets from git
└── .streamlit/
    ├── config.toml     # Theme and server config
    └── secrets.toml    # Local secrets (NOT committed to git)
```

---

## ⚙️ Configuration

| Variable            | Where to set         | Description                    |
|---------------------|----------------------|--------------------------------|
| `SIMLAB_ADMIN_CODE` | Streamlit Secrets    | Invite code for admin sign-up  |
| `SIMLAB_DB_PATH`    | Environment variable | Custom path for SQLite DB file |

---

## 🔑 Features

- Role-based access: Admin, Lecturer, Student
- Student/Lecturer/Admin registration with invite code protection
- Password reset via security question
- Scheduled lab session management with conflict detection
- Recurring weekly sessions
- Open-access bookings with real-time slot availability
- Auto-rejection of unapproved bookings 1hr before slot
- Workstation assignment, status board & usage history
- Attendance check-in / check-out
- In-app notifications
- Reports & analytics with charts
- CSV export for attendance records
