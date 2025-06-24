import sqlite3, io
from flask import (
    Flask, render_template, redirect, url_for, request,
    session, send_file, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "your_secret_key"
DB_NAME = "journal.db"

# ────────────── DB helper ──────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_NAME)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()

# ────────────── Auth decorators ──────────────
def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap

def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap

# ────────────── Routes ──────────────
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form

        # Confirm passwords match
        if f.get("password") != f.get("confirm_password"):
            flash("❌ Passwords do not match.")
            return redirect(url_for("register"))

        email = f.get("email")
        db = get_db()

        # Check if email already exists
        existing = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            flash("📧 Email already registered. Please log in.")
            return redirect(url_for("login"))

        existing_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        role = "admin" if existing_users == 0 else "user"

        db.execute("""
            INSERT INTO users (email, username, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, (
            email,
            f.get("username"),
            generate_password_hash(f.get("password")),
            role
        ))
        db.commit()
        flash("✅ Registration successful. You can now log in.")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        f = request.form
        user = get_db().execute("SELECT * FROM users WHERE username=?", (f["username"],)).fetchone()
        if user and check_password_hash(user["password_hash"], f["password"]):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(url_for("select_project"))
        flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ────────────── Admin Panel ──────────────
@app.route("/admin")
@admin_required
def admin_panel():
    users = get_db().execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    return render_template("admin_panel.html", users=users)

@app.route("/admin/toggle/<int:uid>", methods=["POST"])
@admin_required
def toggle_role(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if user:
        new_role = "user" if user["role"] == "admin" else "admin"
        db.execute("UPDATE users SET role=? WHERE id=?", (new_role, uid))
        db.commit()
        flash(f"{user['username']} updated to {new_role}")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete/<int:uid>", methods=["POST"])
@admin_required
def delete_user(uid):
    if uid == session.get("user_id"):
        flash("You can't delete yourself.")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    flash("User deleted.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_activity(user_id):
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin_panel"))

    trades = db.execute("""
        SELECT t.*, p.name AS project_name
        FROM trades t
        JOIN projects p ON t.project_id = p.id
        WHERE p.user_id = ?
        ORDER BY t.date DESC
    """, (user_id,)).fetchall()

    setups = db.execute("""SELECT * FROM backtest_setups WHERE user_id = ? ORDER BY date DESC""", (user_id,)).fetchall()

    return render_template("admin_user_activity.html", user=user, trades=trades, setups=setups)

# ────────────── Projects ──────────────
@app.route("/select_project")
@login_required
def select_project():
    projects = get_db().execute("SELECT * FROM projects WHERE user_id=?", (session["user_id"],)).fetchall()
    return render_template("select_project.html", projects=projects)

@app.route("/open_project/<int:pid>")
@login_required
def open_project(pid):
    session["project_id"] = pid
    return redirect(url_for("dashboard"))

@app.route("/add_project", methods=["GET", "POST"])
@login_required
def add_project():
    if request.method == "POST":
        f = request.form
        get_db().execute(
            "INSERT INTO projects (user_id,name,category) VALUES (?,?,?)",
            (session["user_id"], f["name"], f["category"])
        ).connection.commit()
        return redirect(url_for("select_project"))
    return render_template("add_project.html")

@app.route("/project/edit/<int:pid>", methods=["GET", "POST"])
@login_required
def edit_project(pid):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, session["user_id"])).fetchone()
    if not proj:
        flash("Project not found")
        return redirect(url_for("select_project"))

    if request.method == "POST":
        f = request.form
        db.execute("UPDATE projects SET name=?, category=? WHERE id=?", (f["name"], f["category"], pid))
        db.commit()
        flash("Project updated")
        return redirect(url_for("select_project"))

    return render_template("edit_project.html", project=proj)

@app.route("/project/delete/<int:pid>", methods=["POST"])
@login_required
def delete_project(pid):
    db = get_db()
    db.execute("DELETE FROM trades WHERE project_id=?", (pid,))
    db.execute("DELETE FROM projects WHERE id=? AND user_id=?", (pid, session["user_id"]))
    db.commit()
    if session.get("project_id") == pid:
        session.pop("project_id")
    flash("Project deleted")
    return redirect(url_for("select_project"))

# ────────────── Dashboard with Stats ──────────────
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    trades = db.execute(
        "SELECT * FROM trades WHERE project_id=? ORDER BY date DESC",
        (session.get("project_id"),)
    ).fetchall()

    total_trades = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]
    breakevens = [t for t in trades if t["result"] == "break-even"]

    total_profit = sum(t["profit"] for t in trades if t["profit"] is not None)
    avg_rr = round(
        sum(float(t["rr"]) for t in trades if t["rr"] and "." in t["rr"] and t["rr"].count(".") == 1) / total_trades,
        2
    ) if total_trades else 0

    avg_profit = round(total_profit / total_trades, 2) if total_trades else 0

    win_rate = round(len(wins) / total_trades * 100, 2) if total_trades else 0
    best_trade = max([t["profit"] for t in trades if t["profit"] is not None], default=0)
    worst_trade = min([t["profit"] for t in trades if t["profit"] is not None], default=0)

    win_sum = sum(t["profit"] for t in wins if t["profit"] is not None)
    loss_sum = sum(t["profit"] for t in losses if t["profit"] is not None)

    profit_factor = round(win_sum / abs(loss_sum), 2) if loss_sum else "N/A"

    return render_template("dashboard.html",
        trades=trades,
        total_trades=total_trades,
        win_rate=win_rate,
        total_profit=total_profit,
        avg_rr=avg_rr,
        avg_profit=avg_profit,
        win_count=len(wins),
        loss_count=len(losses),
        be_count=len(breakevens),
        best_trade=best_trade,
        worst_trade=worst_trade,
        profit_factor=profit_factor
    )

# ────────────── Trade Routes ──────────────
@app.route("/trade/add", methods=["GET", "POST"])
@login_required
def add_trade():
    if request.method == "POST":
        f = request.form
        file = request.files.get("screenshot")
        get_db().execute("""INSERT INTO trades (
            project_id,date,symbol,direction,entry,exit,lot_size,rr,session_name,
            result,profit,notes,screenshot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session["project_id"], f["date"], f["symbol"], f["direction"], f["entry"], f["exit"],
             f.get("lot_size"), f.get("rr"), f.get("session"), f.get("result"),
             f.get("profit"), f.get("notes"), file.read() if file and file.filename else None)
        ).connection.commit()
        return redirect(url_for("dashboard"))
    return render_template("add_trade.html")

@app.route("/trade/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def edit_trade(tid):
    db = get_db()
    trade = db.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
    if not trade:
        flash("Trade not found")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        f = request.form
        file = request.files.get("screenshot")
        shot = file.read() if file and file.filename else trade["screenshot"]
        db.execute("""UPDATE trades SET
            date=?,symbol=?,direction=?,entry=?,exit=?,lot_size=?,rr=?,
            session_name=?,result=?,profit=?,notes=?,screenshot=? WHERE id=?""",
            (f["date"], f["symbol"], f["direction"], f["entry"], f["exit"],
             f["lot_size"], f["rr"], f["session"], f["result"],
             f["profit"], f["notes"], shot, tid))
        db.commit()
        flash("Trade updated")
        return redirect(url_for("dashboard"))

    return render_template("edit_trade.html", trade=trade)

@app.route("/trade/delete/<int:tid>", methods=["POST"])
@login_required
def delete_trade(tid):
    get_db().execute("DELETE FROM trades WHERE id=?", (tid,)).connection.commit()
    flash("Trade deleted")
    return redirect(url_for("dashboard"))

@app.route("/screenshot/<int:trade_id>")
@login_required
def screenshot(trade_id):
    row = get_db().execute("SELECT screenshot FROM trades WHERE id=?", (trade_id,)).fetchone()
    if row and row["screenshot"]:
        return send_file(io.BytesIO(row["screenshot"]), mimetype="image/png")
    return "No screenshot", 404

# ────────────── Setups ──────────────
@app.route("/setups")
@login_required
def setups():
    db = get_db()
    raw = db.execute("SELECT * FROM backtest_setups WHERE user_id = ? ORDER BY date DESC", (session["user_id"],)).fetchall()
    setups = []
    for s in raw:
        pics = db.execute("SELECT id FROM backtest_screenshots WHERE setup_id=?", (s["id"],)).fetchall()
        setups.append({**dict(s), "screenshots": pics})

    trades = db.execute("""SELECT t.*, p.name AS project_name
                           FROM trades t JOIN projects p ON p.id = t.project_id
                           WHERE p.user_id = ? ORDER BY t.date DESC""", (session["user_id"],)).fetchall()

    return render_template("setups.html", trades=trades, setups=setups)

@app.route("/setups/add", methods=["GET", "POST"])
@login_required
def add_backtest_setup():
    if request.method == "POST":
        f = request.form
        db = get_db()
        cur = db.cursor()

        cur.execute("""INSERT INTO backtest_setups
            (user_id, date, title, entry_notes, result, review_notes, session_name,
             timeframe, market, entry_criteria, exit_criteria, r_multiple, profit)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session["user_id"], f.get("date"), f.get("title"), f.get("entry_notes"),
             f.get("result"), f.get("review_notes"), f.get("session_name"),
             f.get("timeframe"), f.get("market"), f.get("entry_criteria"),
             f.get("exit_criteria"), f.get("r_multiple"), f.get("profit"))
        )
        sid = cur.lastrowid

        for file in request.files.getlist("screenshots"):
            if file and file.filename:
                cur.execute("INSERT INTO backtest_screenshots (setup_id, image, filename) VALUES (?, ?, ?)",
                            (sid, file.read(), file.filename))

        db.commit()
        return redirect(url_for("setups"))
    return render_template("add_backtest_setup.html")

@app.route("/setup/<int:setup_id>")
@login_required
def view_setup(setup_id):
    db = get_db()
    setup = db.execute("SELECT * FROM backtest_setups WHERE id=? AND user_id=?", (setup_id, session["user_id"])).fetchone()
    if not setup:
        flash("Setup not found.")
        return redirect(url_for("setups"))
    screenshots = db.execute("SELECT id FROM backtest_screenshots WHERE setup_id=?", (setup_id,)).fetchall()
    return render_template("view_setup.html", setup=setup, screenshots=screenshots)

@app.route("/setup/edit/<int:setup_id>", methods=["GET", "POST"])
@login_required
def edit_setup(setup_id):
    db = get_db()
    setup = db.execute("SELECT * FROM backtest_setups WHERE id=? AND user_id=?", (setup_id, session["user_id"])).fetchone()
    if not setup:
        flash("Setup not found.")
        return redirect(url_for("setups"))
    if request.method == "POST":
        f = request.form
        db.execute("""UPDATE backtest_setups SET
            date=?, title=?, entry_notes=?, result=?, review_notes=?, session_name=?,
            timeframe=?, market=?, entry_criteria=?, exit_criteria=?, r_multiple=?, profit=?
            WHERE id=?""",
            (f.get("date"), f.get("title"), f.get("entry_notes"), f.get("result"), f.get("review_notes"),
             f.get("session_name"), f.get("timeframe"), f.get("market"),
             f.get("entry_criteria"), f.get("exit_criteria"), f.get("r_multiple"), f.get("profit"),
             setup_id))
        db.commit()
        flash("Setup updated.")
        return redirect(url_for("view_setup", setup_id=setup_id))
    return render_template("edit_setup.html", setup=setup)

@app.route("/setup_screenshot/<int:id>")
@login_required
def setup_screenshot(id):
    row = get_db().execute("SELECT image FROM backtest_screenshots WHERE id=?", (id,)).fetchone()
    if row:
        return send_file(io.BytesIO(row["image"]), mimetype="image/png")
    return "Image not found", 404

@app.route("/live_trade/<int:trade_id>")
@login_required
def view_live_trade(trade_id):
    db = get_db()
    trade = db.execute("""SELECT t.*, p.name AS project_name
                          FROM trades t JOIN projects p ON t.project_id = p.id
                          WHERE t.id = ? AND p.user_id = ?""", (trade_id, session["user_id"])).fetchone()
    if not trade:
        flash("Trade not found.")
        return redirect(url_for("setups"))
    return render_template("view_live_trade.html", trade=trade)

# ────────────── Init DB ──────────────
def init_db():
    db = sqlite3.connect(DB_NAME)
    cur = db.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'user')""")

    cur.execute("""CREATE TABLE IF NOT EXISTS projects(
        id INTEGER PRIMARY KEY, user_id INT, name TEXT, category TEXT)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY, project_id INT, date TEXT, symbol TEXT, direction TEXT,
        entry REAL, exit REAL, lot_size REAL, rr TEXT, session_name TEXT,
        result TEXT, profit REAL, notes TEXT, screenshot BLOB)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS backtest_setups(
        id INTEGER PRIMARY KEY, user_id INT, date TEXT, title TEXT,
        entry_notes TEXT, result TEXT, review_notes TEXT)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS backtest_screenshots(
        id INTEGER PRIMARY KEY, setup_id INT, image BLOB, filename TEXT)""")

    def add_col(table, col_def):
        name = col_def.split()[0]
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({table})")]
        if name not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

    add_col("users", "role TEXT DEFAULT 'user'")
    add_col("backtest_setups", "session_name TEXT")
    add_col("backtest_setups", "timeframe TEXT")
    add_col("backtest_setups", "market TEXT")
    add_col("backtest_setups", "entry_criteria TEXT")
    add_col("backtest_setups", "exit_criteria TEXT")
    add_col("backtest_setups", "r_multiple REAL")
    add_col("backtest_setups", "profit REAL")

    db.commit()
    db.close()

# ────────────── Optional DB Tweaks ──────────────
def add_email_column():
    db = sqlite3.connect(DB_NAME)
    cur = db.cursor()
    columns = [c[1] for c in cur.execute("PRAGMA table_info(users)")]
    if "email" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
        print("✅ 'email' column added to users table.")
    else:
        print("ℹ️ 'email' column already exists.")
    db.commit()
    db.close()

def enforce_unique_email():
    db = sqlite3.connect(DB_NAME)
    cur = db.cursor()
    cur.execute("PRAGMA index_list(users)")
    indexes = cur.fetchall()
    email_unique = any("email" in i[1].lower() and i[2] for i in indexes)

    if not email_unique:
        try:
            cur.execute("CREATE UNIQUE INDEX idx_unique_email ON users(email)")
            print("✅ Email uniqueness enforced.")
        except sqlite3.OperationalError as e:
            print("⚠️ Could not enforce email uniqueness:", e)
    else:
        print("ℹ️ Email already unique.")
    db.commit()
    db.close()

        # Add other tables here if needed (projects, trades, etc.)
        conn.commit()

# ────────────── Run ──────────────
if __name__ == "__main__":
    app.run(debug=True)
