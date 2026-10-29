
from flask import Flask, render_template, request, jsonify, session, redirect
import psycopg
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time
from decimal import Decimal

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY"

# ============================================================
# DATABASE CONFIGURATION
# This project uses a NEW database so your existing "queueless"
# database is not touched.
# ============================================================
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "queueless_hospital"
DB_USER = "postgres"
DB_PASSWORD = "root"

ADMIN_SIGNUP_KEY = "ADMIN123"


def db():
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        row_factory=dict_row
    )


def ensure_schema():
    """Synchronize an existing QueueLess Hospital database with this app.

    This migration is non-destructive: it only adds missing columns/tables and
    initializes new fee fields. Existing patients, doctors, departments and
    queue records are preserved.
    """
    with db() as conn:
        # Doctors
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS shift_start TIME NOT NULL DEFAULT '09:00'")
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS shift_end TIME NOT NULL DEFAULT '17:00'")
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS consultation_fee NUMERIC(12,2) NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS available BOOLEAN NOT NULL DEFAULT TRUE")
        conn.execute("ALTER TABLE doctors ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE")

        # Breaks
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doctor_breaks (
                id SERIAL PRIMARY KEY,
                doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
                break_date DATE NOT NULL DEFAULT CURRENT_DATE,
                break_start TIME NOT NULL,
                break_end TIME NOT NULL
            )
        """)

        # Tokens: persist estimated appointment times and cancellation timestamps.
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP")
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP")
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS doctor_id INTEGER REFERENCES doctors(id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS priority VARCHAR(20) NOT NULL DEFAULT 'normal'")
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS priority_set_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS priority_set_at TIMESTAMP")

        # Payments -- these are the columns causing the UndefinedColumn errors.
        conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS doctor_fee NUMERIC(12,2) NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS other_charges NUMERIC(12,2) NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")

        # Give old payment rows a sensible starting doctor fee without changing
        # an already-entered fee.
        conn.execute("""
            UPDATE payments
            SET doctor_fee = COALESCE(total_amount, 0),
                other_charges = COALESCE(other_charges, 0)
            WHERE COALESCE(doctor_fee, 0) = 0
              AND COALESCE(total_amount, 0) > 0
        """)

        conn.execute("UPDATE doctors SET consultation_fee=0 WHERE consultation_fee IS NULL")
        conn.execute("UPDATE payments SET other_charges=0 WHERE other_charges IS NULL")
        conn.commit()

def json_safe(value):
    """Convert PostgreSQL values to JSON-safe values before sending them to JS."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def ok(**data):
    return jsonify(json_safe({"success": True, **data}))


def fail(message, status=400):
    return jsonify({"success": False, "message": message}), status


def logged():
    return "user_id" in session


def role(*roles):
    return logged() and session.get("user_role") in roles


def parse_time(value):
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def reschedule_doctor_queue(conn, doctor_id):
    """Recalculate and save estimated times for today's active doctor queue.

    Estimates use a five-minute service slot. The calculation respects the
    doctor's current shift and today's configured breaks. Existing records are
    updated in place; no patient or token records are deleted.
    """
    doctor = conn.execute("""
        SELECT id, shift_start, shift_end, available
        FROM doctors WHERE id=%s AND active=TRUE
    """, (doctor_id,)).fetchone()
    if not doctor:
        return

    tokens = conn.execute("""
        SELECT id, status, priority, booked_at
        FROM tokens
        WHERE doctor_id=%s
          AND booked_at::date=CURRENT_DATE
          AND status IN ('waiting','called')
        ORDER BY CASE WHEN status='called' THEN 0 ELSE 1 END,
                 CASE priority WHEN 'emergency' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END,
                 booked_at, id
        FOR UPDATE
    """, (doctor_id,)).fetchall()

    if not tokens:
        return

    now = datetime.now()
    today = date.today()
    cursor = max(now, datetime.combine(today, doctor['shift_start']))
    if not doctor['available'] or now.time() >= doctor['shift_end']:
        cursor = datetime.combine(today + __import__('datetime').timedelta(days=1), doctor['shift_start'])
    elif now.time() < doctor['shift_start']:
        cursor = datetime.combine(today, doctor['shift_start'])

    for token in tokens:
        # The patient already being called is considered in service now.
        if token['status'] == 'called':
            estimated = now
            cursor = max(cursor, now + __import__('datetime').timedelta(minutes=5))
        else:
            # Move appointments beyond shift end to the next working shift.
            if cursor.time() >= doctor['shift_end']:
                cursor = datetime.combine(cursor.date() + __import__('datetime').timedelta(days=1), doctor['shift_start'])
            # If the estimate falls in a scheduled break, resume at break end.
            breaks = conn.execute("""
                SELECT break_date, break_start, break_end
                FROM doctor_breaks
                WHERE doctor_id=%s AND break_date=%s
                ORDER BY break_start
            """, (doctor_id, cursor.date())).fetchall()
            moved = True
            while moved:
                moved = False
                for br in breaks:
                    bstart = datetime.combine(br['break_date'], br['break_start'])
                    bend = datetime.combine(br['break_date'], br['break_end'])
                    if bstart <= cursor < bend:
                        cursor = bend
                        moved = True
                if cursor.time() >= doctor['shift_end']:
                    cursor = datetime.combine(cursor.date() + __import__('datetime').timedelta(days=1), doctor['shift_start'])
                    breaks = conn.execute("""
                        SELECT break_date, break_start, break_end FROM doctor_breaks
                        WHERE doctor_id=%s AND break_date=%s ORDER BY break_start
                    """, (doctor_id, cursor.date())).fetchall()
            estimated = cursor
            cursor += __import__('datetime').timedelta(minutes=5)
        conn.execute("UPDATE tokens SET scheduled_at=%s WHERE id=%s", (estimated, token['id']))


def doctor_state(conn, doctor_id):
    d = conn.execute("""
        SELECT d.*, dep.name AS department_name
        FROM doctors d
        LEFT JOIN departments dep ON dep.id=d.department_id
        WHERE d.id=%s AND d.active=TRUE
    """, (doctor_id,)).fetchone()

    if not d:
        return None

    now = datetime.now().time()

    if not d["available"]:
        d["current_status"] = "Unavailable"
        d["return_minutes"] = None
        return d

    if now < d["shift_start"]:
        d["current_status"] = "Not started"
        d["return_minutes"] = max(
            0,
            int((datetime.combine(date.today(), d["shift_start"]) -
                 datetime.now()).total_seconds() / 60)
        )
        return d

    if now >= d["shift_end"]:
        d["current_status"] = "Off duty"
        d["return_minutes"] = None
        return d

    br = conn.execute("""
        SELECT *
        FROM doctor_breaks
        WHERE doctor_id=%s
          AND break_date=CURRENT_DATE
          AND break_start <= %s
          AND break_end > %s
        ORDER BY break_start
        LIMIT 1
    """, (doctor_id, now, now)).fetchone()

    if br:
        d["current_status"] = "On break"
        d["return_minutes"] = max(
            0,
            int((datetime.combine(date.today(), br["break_end"]) -
                 datetime.now()).total_seconds() / 60)
        )
        return d

    d["current_status"] = "Available"
    d["return_minutes"] = 0
    return d


def password_valid(stored, entered):
    try:
        return check_password_hash(stored, entered)
    except Exception:
        return stored == entered


# ============================================================
# PAGES
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login/<role_name>")
def login_page(role_name):
    labels = {
        "patient": ("Patient Login", "patient"),
        "receptionist": ("Receptionist Login", "receptionist"),
        "doctor": ("Doctor Login", "doctor"),
        "admin": ("Admin Login", "admin")
    }
    if role_name not in labels:
        return redirect("/")
    title, role_key = labels[role_name]
    return render_template("auth.html", mode="login",
                           title=title, role=role_key)


@app.route("/signup/<role_name>")
def signup_page(role_name):
    labels = {
        "patient": ("Patient Signup", "patient"),
        "receptionist": ("Receptionist Signup", "receptionist"),
        "doctor": ("Doctor Signup", "doctor"),
        "admin": ("Admin Signup", "admin")
    }
    if role_name not in labels:
        return redirect("/")
    title, role_key = labels[role_name]
    return render_template("auth.html", mode="signup",
                           title=title, role=role_key)


@app.route("/dashboard")
def dashboard():
    if not logged():
        return redirect("/")
    pages = {
        "patient": "patient.html",
        "receptionist": "receptionist.html",
        "doctor": "doctor.html",
        "admin": "admin.html"
    }
    return render_template(pages[session["user_role"]])


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ============================================================
# AUTH
# ============================================================

ROLE_MAP = {
    "patient": "patient",
    "receptionist": "receptionist",
    "doctor": "doctor",
    "admin": "admin"
}


@app.post("/api/signup/<role_name>")
def signup(role_name):
    if role_name not in ROLE_MAP:
        return fail("Invalid account type.")

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return fail("Name, email and password are required.")

    if role_name == "admin" and data.get("admin_key") != ADMIN_SIGNUP_KEY:
        return fail("Invalid admin signup key.", 403)

    with db() as conn:
        try:
            u = conn.execute("""
                INSERT INTO users(name,email,password_hash,role)
                VALUES(%s,%s,%s,%s)
                RETURNING id,name,email,role
            """, (
                name, email, generate_password_hash(password),
                ROLE_MAP[role_name]
            )).fetchone()

            if role_name == "doctor":
                conn.execute("""
                    INSERT INTO doctors(
                        user_id,name,specialization,department_id,
                        shift_start,shift_end,consultation_fee
                    )
                    VALUES(%s,%s,'General Medicine',NULL,'09:00','17:00',0)
                """, (u["id"], name))

            conn.commit()
            return ok(message="Account created successfully.", user=u)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return fail("Email is already registered.", 409)


@app.post("/api/login/<role_name>")
def login(role_name):
    if role_name not in ROLE_MAP:
        return fail("Invalid account type.")

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    with db() as conn:
        u = conn.execute("""
            SELECT * FROM users
            WHERE LOWER(email)=LOWER(%s)
        """, (email,)).fetchone()

        if not u or u["role"] != ROLE_MAP[role_name]:
            return fail("Invalid login details.", 401)

        if not password_valid(u["password_hash"], password):
            return fail("Invalid login details.", 401)

        session["user_id"] = u["id"]
        session["user_name"] = u["name"]
        session["user_role"] = u["role"]

        return ok(message="Login successful.", redirect="/dashboard")


@app.get("/api/me")
def me():
    if not logged():
        return fail("Login required.", 401)

    user = {
        "id": session["user_id"],
        "name": session["user_name"],
        "role": session["user_role"]
    }

    if session["user_role"] == "doctor":
        with db() as conn:
            doctor = conn.execute("""
                SELECT d.*, dep.name AS department_name
                FROM doctors d
                LEFT JOIN departments dep ON dep.id=d.department_id
                WHERE d.user_id=%s AND d.active=TRUE
            """, (session["user_id"],)).fetchone()
        user["doctor"] = doctor

    return ok(user=user, doctor=user.get("doctor"))


# ============================================================
# DASHBOARD SUMMARY
# ============================================================

@app.get("/api/dashboard")
def dashboard_data():
    if not logged():
        return fail("Login required.", 401)

    with db() as conn:
        stats = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM departments WHERE active) AS departments,
              (SELECT COUNT(*) FROM doctors WHERE active) AS doctors,
              (SELECT COUNT(*) FROM users WHERE role='patient') AS patients,
              (SELECT COUNT(*) FROM tokens
                 WHERE booked_at::date=CURRENT_DATE) AS today_tokens,
              (SELECT COUNT(*) FROM tokens
                 WHERE booked_at::date=CURRENT_DATE
                   AND priority='emergency') AS emergencies,
              (SELECT COALESCE(SUM(paid_amount),0)
                 FROM payments
                 WHERE updated_at::date=CURRENT_DATE) AS paid_today,
              (SELECT COALESCE(SUM(total_amount-paid_amount),0)
                 FROM payments
                 WHERE status IN ('pending','partial')) AS pending_amount
        """).fetchone()
        return ok(stats=stats)


# ============================================================
# DEPARTMENTS
# Admin can add/deactivate departments.
# ============================================================

@app.get("/api/departments")
def get_departments():
    if not logged():
        return fail("Login required.", 401)

    with db() as conn:
        rows = conn.execute("""
            SELECT
                d.id,d.name,d.description,d.active,
                COUNT(DISTINCT doc.id) FILTER
                    (WHERE doc.active=TRUE) AS doctor_count,
                COUNT(t.id) FILTER
                    (WHERE t.booked_at::date=CURRENT_DATE
                     AND t.status IN ('waiting','called')) AS waiting_count
            FROM departments d
            LEFT JOIN doctors doc ON doc.department_id=d.id
            LEFT JOIN tokens t ON t.department_id=d.id
            GROUP BY d.id
            ORDER BY d.name
        """).fetchall()
        return ok(departments=rows)


@app.post("/api/departments")
def add_department():
    if not role("admin"):
        return fail("Admin access required.", 403)

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()

    if not name:
        return fail("Department name is required.")

    with db() as conn:
        try:
            row = conn.execute("""
                INSERT INTO departments(name,description)
                VALUES(%s,%s)
                RETURNING *
            """, (name, description)).fetchone()
            conn.commit()
            return ok(department=row)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return fail("Department already exists.", 409)


@app.delete("/api/departments/<int:department_id>")
def deactivate_department(department_id):
    if not role("admin"):
        return fail("Admin access required.", 403)

    with db() as conn:
        row = conn.execute("""
            UPDATE departments
            SET active=FALSE
            WHERE id=%s
            RETURNING id
        """, (department_id,)).fetchone()
        conn.commit()

        if not row:
            return fail("Department not found.", 404)

        return ok(message="Department deactivated.")


# ============================================================
# DOCTORS AND TIMINGS
# ============================================================

@app.get("/api/doctors")
def get_doctors():
    if not logged():
        return fail("Login required.", 401)

    department_id = request.args.get("department_id", type=int)

    with db() as conn:
        sql = """
            SELECT d.id,d.user_id,d.name,d.specialization,
                   d.department_id,d.shift_start,d.shift_end,
                   d.consultation_fee,d.available,d.active,dep.name AS department_name
            FROM doctors d
            LEFT JOIN departments dep ON dep.id=d.department_id
            WHERE d.active=TRUE
        """
        params = []

        if department_id:
            sql += " AND d.department_id=%s"
            params.append(department_id)

        sql += " ORDER BY d.name"

        doctors = conn.execute(sql, params).fetchall()

        for d in doctors:
            state = doctor_state(conn, d["id"])
            d["current_status"] = state["current_status"]
            d["return_minutes"] = state["return_minutes"]

        return ok(doctors=doctors)


@app.post("/api/doctors")
def create_doctor():
    if not role("admin"):
        return fail("Admin access required.", 403)

    data = request.get_json() or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    specialization = (data.get("specialization") or "General Medicine").strip()
    department_id = data.get("department_id")
    password = data.get("password") or "doctor123"
    try:
        consultation_fee = float(data.get("consultation_fee", 0) or 0)
    except (TypeError, ValueError):
        return fail("Invalid consultation fee.")
    if consultation_fee < 0:
        return fail("Consultation fee cannot be negative.")

    if not name or not email or not department_id:
        return fail("Name, email and department are required.")

    try:
        start = parse_time(data.get("shift_start")) or parse_time("09:00")
        end = parse_time(data.get("shift_end")) or parse_time("17:00")
    except ValueError:
        return fail("Invalid doctor timing.")

    if start >= end:
        return fail("Shift end must be after shift start.")

    with db() as conn:
        try:
            u = conn.execute("""
                INSERT INTO users(name,email,password_hash,role)
                VALUES(%s,%s,%s,'doctor')
                RETURNING id
            """, (name, email, generate_password_hash(password))).fetchone()

            d = conn.execute("""
                INSERT INTO doctors(
                    user_id,name,specialization,department_id,
                    shift_start,shift_end,consultation_fee
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (
                u["id"],name,specialization,department_id,start,end,consultation_fee
            )).fetchone()

            conn.commit()
            return ok(doctor=d)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return fail("Email already exists.", 409)


@app.put("/api/doctors/<int:doctor_id>")
@app.put("/api/doctors/<int:doctor_id>/schedule")
def change_schedule(doctor_id):
    if not role("doctor","receptionist","admin"):
        return fail("Access denied.", 403)

    data = request.get_json() or {}

    with db() as conn:
        d = conn.execute("""
            SELECT * FROM doctors WHERE id=%s AND active=TRUE
        """, (doctor_id,)).fetchone()

        if not d:
            return fail("Doctor not found.", 404)

        if session["user_role"] == "doctor" and d["user_id"] != session["user_id"]:
            return fail("Doctor can change only their own timing.", 403)

        start = data.get("shift_start")
        end = data.get("shift_end")

        try:
            start_time = parse_time(start) if start else d["shift_start"]
            end_time = parse_time(end) if end else d["shift_end"]
        except ValueError:
            return fail("Invalid time.")

        if start_time >= end_time:
            return fail("Shift end must be after shift start.")

        available = data.get("available")
        if available is None:
            available = d["available"]

        try:
            consultation_fee = float(data.get("consultation_fee", d["consultation_fee"]) or 0)
        except (TypeError, ValueError):
            return fail("Invalid consultation fee.")
        if consultation_fee < 0:
            return fail("Consultation fee cannot be negative.")

        department_id = data.get("department_id", d["department_id"])
        if department_id:
            department = conn.execute("SELECT id FROM departments WHERE id=%s AND active=TRUE", (department_id,)).fetchone()
            if not department:
                return fail("Department not found.")

        specialization = (data.get("specialization") or d["specialization"]).strip()
        if not specialization:
            specialization = d["specialization"]

        row = conn.execute("""
            UPDATE doctors
            SET department_id=%s,specialization=%s,shift_start=%s,shift_end=%s,
                available=%s,consultation_fee=%s
            WHERE id=%s
            RETURNING *
        """, (department_id,specialization,start_time,end_time,bool(available),consultation_fee,doctor_id)).fetchone()

        reschedule_doctor_queue(conn, doctor_id)
        conn.commit()
        return ok(doctor=row)


@app.get("/api/doctors/<int:doctor_id>/breaks")
def get_breaks(doctor_id):
    if not logged():
        return fail("Login required.", 401)

    with db() as conn:
        rows = conn.execute("""
            SELECT * FROM doctor_breaks
            WHERE doctor_id=%s
              AND break_date>=CURRENT_DATE
            ORDER BY break_date,break_start
        """, (doctor_id,)).fetchall()
        return ok(breaks=rows)


@app.post("/api/doctors/<int:doctor_id>/breaks")
def add_break(doctor_id):
    if not role("doctor","receptionist","admin"):
        return fail("Access denied.", 403)

    data = request.get_json() or {}
    break_date = data.get("break_date") or date.today().isoformat()
    start = data.get("break_start")
    end = data.get("break_end")

    if not start or not end:
        return fail("Break start and end are required.")

    try:
        start_time = parse_time(start)
        end_time = parse_time(end)
    except ValueError:
        return fail("Invalid break time.")

    if start_time >= end_time:
        return fail("Break end must be after break start.")

    with db() as conn:
        d = conn.execute("""
            SELECT * FROM doctors
            WHERE id=%s AND active=TRUE
        """, (doctor_id,)).fetchone()

        if not d:
            return fail("Doctor not found.", 404)

        if session["user_role"] == "doctor" and d["user_id"] != session["user_id"]:
            return fail("Doctor can change only their own breaks.", 403)

        if start_time < d["shift_start"] or end_time > d["shift_end"]:
            return fail("Break must be inside the doctor's shift.")

        overlap = conn.execute("""
            SELECT id FROM doctor_breaks
            WHERE doctor_id=%s AND break_date=%s
              AND break_start < %s AND break_end > %s
        """, (doctor_id,break_date,end_time,start_time)).fetchone()

        if overlap:
            return fail("This break overlaps another break.")

        row = conn.execute("""
            INSERT INTO doctor_breaks(
                doctor_id,break_date,break_start,break_end
            )
            VALUES(%s,%s,%s,%s)
            RETURNING *
        """, (doctor_id,break_date,start_time,end_time)).fetchone()

        reschedule_doctor_queue(conn, doctor_id)
        conn.commit()
        return ok(break_item=row)


@app.delete("/api/doctor-breaks/<int:break_id>")
def delete_break(break_id):
    if not role("doctor","receptionist","admin"):
        return fail("Access denied.", 403)

    with db() as conn:
        b = conn.execute("""
            SELECT b.*,d.user_id
            FROM doctor_breaks b
            JOIN doctors d ON d.id=b.doctor_id
            WHERE b.id=%s
        """, (break_id,)).fetchone()

        if not b:
            return fail("Break not found.", 404)

        if session["user_role"] == "doctor" and b["user_id"] != session["user_id"]:
            return fail("Access denied.", 403)

        conn.execute("DELETE FROM doctor_breaks WHERE id=%s", (break_id,))
        reschedule_doctor_queue(conn, b["doctor_id"])
        conn.commit()
        return ok(message="Break deleted.")


# ============================================================
# QUEUE BOOKING
# ============================================================

@app.post("/api/tokens")
def create_token():
    if not role("patient","receptionist","doctor"):
        return fail("Access denied.", 403)

    data = request.get_json() or {}
    department_id = data.get("department_id")
    doctor_id = data.get("doctor_id")

    patient_id = session["user_id"]
    if session["user_role"] in ("receptionist","doctor"):
        patient_id = data.get("patient_id")

    if not patient_id or not department_id:
        return fail("Patient and department are required.")

    with db() as conn:
        dep = conn.execute("""
            SELECT id FROM departments
            WHERE id=%s AND active=TRUE
        """, (department_id,)).fetchone()

        if not dep:
            return fail("Department not found.", 404)

        if doctor_id:
            doc = conn.execute("""
                SELECT id FROM doctors
                WHERE id=%s AND department_id=%s AND active=TRUE
            """, (doctor_id,department_id)).fetchone()

            if not doc:
                return fail("Doctor does not belong to this department.", 400)

        # Lock the department row. This makes token numbering safe
        # when two patients book at almost the same time.
        conn.execute("""
            SELECT id FROM departments
            WHERE id=%s
            FOR UPDATE
        """, (department_id,))

        token_number = conn.execute("""
            SELECT COALESCE(MAX(token_number),0)+1 AS next_number
            FROM tokens
            WHERE department_id=%s
              AND booked_at::date=CURRENT_DATE
        """, (department_id,)).fetchone()["next_number"]

        token = conn.execute("""
            INSERT INTO tokens(
                token_number,user_id,department_id,doctor_id,
                status,priority
            )
            VALUES(%s,%s,%s,%s,'waiting','normal')
            RETURNING *
        """, (
            token_number,patient_id,department_id,doctor_id
        )).fetchone()

        doctor_fee = 0
        if doctor_id:
            doctor_row = conn.execute(
                "SELECT consultation_fee FROM doctors WHERE id=%s", (doctor_id,)
            ).fetchone()
            doctor_fee = doctor_row["consultation_fee"] if doctor_row else 0

        conn.execute("""
            INSERT INTO payments(token_id,doctor_fee,other_charges,total_amount,paid_amount,status)
            VALUES(%s,%s,0,%s,0,%s)
        """, (token["id"],doctor_fee,doctor_fee,'pending'))

        if doctor_id:
            reschedule_doctor_queue(conn, doctor_id)
        conn.commit()
        return ok(token=token)


@app.get("/api/my-tokens")
def my_tokens():
    if not role("patient"):
        return fail("Patient login required.", 403)

    with db() as conn:
        assigned = conn.execute("""
            SELECT DISTINCT doctor_id FROM tokens
            WHERE user_id=%s AND doctor_id IS NOT NULL
              AND booked_at::date=CURRENT_DATE
              AND status IN ('waiting','called')
        """, (session["user_id"],)).fetchall()
        for item in assigned:
            reschedule_doctor_queue(conn, item["doctor_id"])

        rows = conn.execute("""
            SELECT t.*,dep.name AS department_name,
                   d.name AS doctor_name,
                   p.doctor_fee,p.other_charges,p.total_amount,p.paid_amount,p.status AS payment_status
            FROM tokens t
            JOIN departments dep ON dep.id=t.department_id
            LEFT JOIN doctors d ON d.id=t.doctor_id
            LEFT JOIN payments p ON p.token_id=t.id
            WHERE t.user_id=%s
            ORDER BY t.booked_at DESC
        """, (session["user_id"],)).fetchall()

        for row in rows:
            row["queue_note"] = ""
            if row["doctor_id"] and row["status"] in ("waiting","called"):
                state = doctor_state(conn,row["doctor_id"])
                if state["current_status"] == "On break":
                    row["queue_note"] = (
                        "Doctor is on break. Returns in "
                        f"{state['return_minutes']} minutes."
                    )
                elif state["current_status"] == "Off duty":
                    row["queue_note"] = "Doctor is currently off duty."

        return ok(tokens=rows)


@app.get("/api/queue")
def queue():
    if not role("receptionist","doctor","admin"):
        return fail("Staff login required.", 403)

    department_id = request.args.get("department_id",type=int)
    doctor_id = request.args.get("doctor_id",type=int)

    with db() as conn:
        sql = """
            SELECT t.id,t.token_number,t.status,t.priority,
                   t.booked_at,t.called_at,
                   u.name AS patient_name,
                   d.name AS doctor_name,
                   dep.name AS department_name,
                   p.id AS payment_id,p.doctor_fee,p.other_charges,p.total_amount,
                   p.paid_amount,p.status AS payment_status
            FROM tokens t
            JOIN users u ON u.id=t.user_id
            JOIN departments dep ON dep.id=t.department_id
            LEFT JOIN doctors d ON d.id=t.doctor_id
            LEFT JOIN payments p ON p.token_id=t.id
            WHERE t.booked_at::date=CURRENT_DATE
              AND t.status IN ('waiting','called')
        """
        params=[]

        if department_id:
            sql += " AND t.department_id=%s"
            params.append(department_id)

        if doctor_id:
            sql += " AND t.doctor_id=%s"
            params.append(doctor_id)

        if session["user_role"] == "doctor":
            own = conn.execute("SELECT id FROM doctors WHERE user_id=%s AND active=TRUE", (session["user_id"],)).fetchone()
            if not own:
                return fail("Doctor profile not found.",404)
            sql += " AND t.doctor_id=%s"
            params.append(own["id"])

        sql += """
            ORDER BY
              CASE t.priority
                WHEN 'emergency' THEN 0
                WHEN 'urgent' THEN 1
                ELSE 2
              END,
              t.booked_at
        """

        return ok(queue=conn.execute(sql,params).fetchall())


@app.put("/api/my-tokens/<int:token_id>/cancel")
def cancel_my_token(token_id):
    """Allow a patient to cancel their own token while it is still waiting."""
    if not role("patient"):
        return fail("Patient login required.", 403)

    with db() as conn:
        token = conn.execute("""
            SELECT id, user_id, doctor_id, status
            FROM tokens WHERE id=%s FOR UPDATE
        """, (token_id,)).fetchone()
        if not token or token["user_id"] != session["user_id"]:
            return fail("Token not found.", 404)
        if token["status"] != "waiting":
            return fail("Only waiting tokens can be cancelled.")

        conn.execute("""
            UPDATE tokens SET status='cancelled', cancelled_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (token_id,))
        if token["doctor_id"]:
            reschedule_doctor_queue(conn, token["doctor_id"])
        conn.commit()
        return ok(message="Token cancelled. The remaining queue times have been updated.")


# ============================================================
# EMERGENCY PRIORITY
# Only doctor/receptionist can decide priority.
# ============================================================

@app.put("/api/tokens/<int:token_id>/priority")
def set_priority(token_id):
    if not role("doctor","receptionist"):
        return fail("Only doctor or receptionist can set priority.",403)

    priority = (request.get_json() or {}).get("priority")

    if priority not in ("normal","urgent","emergency"):
        return fail("Invalid priority.")

    with db() as conn:
        if session["user_role"] == "doctor":
            own = conn.execute("SELECT id FROM doctors WHERE user_id=%s AND active=TRUE", (session["user_id"],)).fetchone()
            if not own:
                return fail("Doctor profile not found.",404)
            token = conn.execute("SELECT doctor_id FROM tokens WHERE id=%s", (token_id,)).fetchone()
            if not token or token["doctor_id"] != own["id"]:
                return fail("This patient is not assigned to you.",403)
        row = conn.execute("""
            UPDATE tokens
            SET priority=%s,
                priority_set_by=%s,
                priority_set_at=CURRENT_TIMESTAMP
            WHERE id=%s
            RETURNING *
        """, (priority,session["user_id"],token_id)).fetchone()

        conn.commit()

        if not row:
            return fail("Token not found.",404)

        return ok(token=row)


# ============================================================
# QUEUE ACTIONS
# ============================================================

@app.put("/api/tokens/<int:token_id>/status")
def token_status(token_id):
    if not role("doctor","receptionist"):
        return fail("Only doctor or receptionist can manage queue.",403)

    new_status = (request.get_json() or {}).get("status")

    if new_status not in ("called","completed","skipped","cancelled"):
        return fail("Invalid queue status.")

    with db() as conn:
        token = conn.execute("""
            SELECT * FROM tokens
            WHERE id=%s
            FOR UPDATE
        """, (token_id,)).fetchone()

        if not token:
            return fail("Token not found.",404)

        if session["user_role"] == "doctor":
            own = conn.execute("SELECT id FROM doctors WHERE user_id=%s AND active=TRUE", (session["user_id"],)).fetchone()
            if not own or token["doctor_id"] != own["id"]:
                return fail("This patient is not assigned to you.",403)

        if new_status == "called":
            # A doctor/receptionist cannot call a second patient for
            # the same doctor while another patient is already called.
            if token["doctor_id"]:
                active = conn.execute("""
                    SELECT id FROM tokens
                    WHERE doctor_id=%s
                      AND status='called'
                      AND id<>%s
                    LIMIT 1
                """, (token["doctor_id"],token_id)).fetchone()

                if active:
                    return fail("This doctor already has a called patient.")

        row = conn.execute("""
            UPDATE tokens
            SET status=%s,
                called_at=CASE
                    WHEN %s='called' THEN CURRENT_TIMESTAMP
                    ELSE called_at END,
                completed_at=CASE
                    WHEN %s='completed' THEN CURRENT_TIMESTAMP
                    ELSE completed_at END
            WHERE id=%s
            RETURNING *
        """, (new_status,new_status,new_status,token_id)).fetchone()

        if token["doctor_id"]:
            reschedule_doctor_queue(conn, token["doctor_id"])
        conn.commit()
        return ok(token=row)


# ============================================================
# TESTS
# ONLY DOCTORS CAN ADD / EDIT / DELETE TEST DETAILS.
# ============================================================

@app.get("/api/tests")
def tests():
    if not logged():
        return fail("Login required.",401)

    with db() as conn:
        rows = conn.execute("""
            SELECT t.*,u.name AS updated_by_name
            FROM tests t
            LEFT JOIN users u ON u.id=t.updated_by
            WHERE t.active=TRUE
            ORDER BY t.name
        """).fetchall()
        return ok(tests=rows)


@app.post("/api/tests")
def create_test():
    if not role("doctor"):
        return fail("Only doctors can edit test details.",403)

    data=request.get_json() or {}
    name=(data.get("name") or "").strip()
    description=(data.get("description") or "").strip()
    price=data.get("price")

    if not name or price is None:
        return fail("Test name and price are required.")

    with db() as conn:
        try:
            row=conn.execute("""
                INSERT INTO tests(name,description,price,updated_by)
                VALUES(%s,%s,%s,%s)
                RETURNING *
            """,(name,description,price,session["user_id"])).fetchone()
            conn.commit()
            return ok(test=row)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return fail("Test already exists.",409)


@app.put("/api/tests/<int:test_id>")
def update_test(test_id):
    if not role("doctor"):
        return fail("Only doctors can edit test details.",403)

    data=request.get_json() or {}

    with db() as conn:
        row=conn.execute("""
            UPDATE tests
            SET name=COALESCE(NULLIF(%s,''),name),
                description=COALESCE(%s,description),
                price=COALESCE(%s,price),
                updated_by=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND active=TRUE
            RETURNING *
        """,(
            data.get("name",""),
            data.get("description"),
            data.get("price"),
            session["user_id"],
            test_id
        )).fetchone()

        conn.commit()

        if not row:
            return fail("Test not found.",404)

        return ok(test=row)


@app.delete("/api/tests/<int:test_id>")
def delete_test(test_id):
    if not role("doctor"):
        return fail("Only doctors can edit test details.",403)

    with db() as conn:
        row=conn.execute("""
            UPDATE tests
            SET active=FALSE,
                updated_by=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            RETURNING id
        """,(session["user_id"],test_id)).fetchone()
        conn.commit()

        if not row:
            return fail("Test not found.",404)

        return ok(message="Test deactivated.")


@app.post("/api/test-bookings")
def book_test():
    if not role("patient","doctor","receptionist"):
        return fail("Access denied.",403)

    data=request.get_json() or {}
    patient_id=session["user_id"]

    if session["user_role"] in ("doctor","receptionist"):
        patient_id=data.get("patient_id")

    test_id=data.get("test_id")

    if not patient_id or not test_id:
        return fail("Patient and test are required.")

    with db() as conn:
        test=conn.execute("""
            SELECT * FROM tests
            WHERE id=%s AND active=TRUE
        """,(test_id,)).fetchone()

        if not test:
            return fail("Test not found.",404)

        booking=conn.execute("""
            INSERT INTO test_bookings(
                patient_id,test_id,price_at_booking,booked_by
            )
            VALUES(%s,%s,%s,%s)
            RETURNING *
        """,(
            patient_id,test_id,test["price"],session["user_id"]
        )).fetchone()

        conn.commit()
        return ok(booking=booking)


# ============================================================
# SURGERY / SCHEMES
# Doctors manage surgery prices and schemes.
# Patients can see the final calculated price.
# ============================================================

@app.get("/api/surgeries")
def surgeries():
    if not logged():
        return fail("Login required.",401)

    with db() as conn:
        rows=conn.execute("""
            SELECT s.*,
                   COALESCE(
                     json_agg(
                       json_build_object(
                         'id',sc.id,
                         'name',sc.name,
                         'discount_percent',sc.discount_percent
                       )
                     ) FILTER (WHERE sc.id IS NOT NULL),
                     '[]'
                   ) AS schemes
            FROM surgeries s
            LEFT JOIN surgery_schemes sc ON sc.surgery_id=s.id AND sc.active=TRUE
            WHERE s.active=TRUE
            GROUP BY s.id
            ORDER BY s.name
        """).fetchall()
        return ok(surgeries=rows)


@app.post("/api/surgeries")
def add_surgery():
    if not role("doctor"):
        return fail("Only doctors can manage surgery details.",403)

    data=request.get_json() or {}
    name=(data.get("name") or "").strip()
    price=data.get("price")
    description=(data.get("description") or "").strip()

    if not name or price is None:
        return fail("Surgery name and price are required.")

    with db() as conn:
        row=conn.execute("""
            INSERT INTO surgeries(name,description,base_price,updated_by)
            VALUES(%s,%s,%s,%s)
            RETURNING *
        """,(name,description,price,session["user_id"])).fetchone()
        conn.commit()
        return ok(surgery=row)


@app.post("/api/surgery-schemes")
def add_scheme():
    if not role("doctor"):
        return fail("Only doctors can manage surgery schemes.",403)

    data=request.get_json() or {}
    surgery_id=data.get("surgery_id")
    name=(data.get("name") or "").strip()
    discount=data.get("discount_percent")

    if not surgery_id or not name or discount is None:
        return fail("Surgery, scheme name and discount are required.")

    if float(discount) < 0 or float(discount) > 100:
        return fail("Discount must be between 0 and 100.")

    with db() as conn:
        row=conn.execute("""
            INSERT INTO surgery_schemes(
                surgery_id,name,discount_percent,updated_by
            )
            VALUES(%s,%s,%s,%s)
            RETURNING *
        """,(surgery_id,name,discount,session["user_id"])).fetchone()
        conn.commit()
        return ok(scheme=row)


# ============================================================
# PAYMENTS / PATIENT FEES
# Doctor and receptionist can decide the fee for an individual patient visit.
# The doctor's consultation_fee is the default; payments.doctor_fee is the
# actual fee agreed for that patient/token.
# ============================================================

def payment_staff_access(conn, payment_id):
    payment = conn.execute("""
        SELECT p.*,t.doctor_id,t.user_id AS patient_id
        FROM payments p
        JOIN tokens t ON t.id=p.token_id
        WHERE p.id=%s
    """, (payment_id,)).fetchone()
    if not payment:
        return None, "Payment not found."
    if session["user_role"] == "doctor":
        doctor = conn.execute("SELECT id FROM doctors WHERE user_id=%s AND active=TRUE", (session["user_id"],)).fetchone()
        if not doctor or payment["doctor_id"] != doctor["id"]:
            return None, "A doctor can edit fees only for patients assigned to that doctor."
    return payment, None


@app.get("/api/payments")
def get_payments():
    if not logged():
        return fail("Login required.", 401)

    with db() as conn:
        if session["user_role"] == "patient":
            rows = conn.execute("""
                SELECT p.*,t.token_number,d.name AS doctor_name,
                       dep.name AS department_name
                FROM payments p
                JOIN tokens t ON t.id=p.token_id
                LEFT JOIN doctors d ON d.id=t.doctor_id
                JOIN departments dep ON dep.id=t.department_id
                WHERE t.user_id=%s
                ORDER BY p.updated_at DESC
            """, (session["user_id"],)).fetchall()
        elif session["user_role"] == "doctor":
            rows = conn.execute("""
                SELECT p.*,t.token_number,u.name AS patient_name,
                       d.name AS doctor_name,dep.name AS department_name
                FROM payments p
                JOIN tokens t ON t.id=p.token_id
                JOIN users u ON u.id=t.user_id
                LEFT JOIN doctors d ON d.id=t.doctor_id
                JOIN departments dep ON dep.id=t.department_id
                JOIN doctors own ON own.id=t.doctor_id
                WHERE own.user_id=%s
                ORDER BY p.updated_at DESC
            """, (session["user_id"],)).fetchall()
        else:
            rows = conn.execute("""
                SELECT p.*,t.token_number,u.name AS patient_name,
                       d.name AS doctor_name,dep.name AS department_name
                FROM payments p
                JOIN tokens t ON t.id=p.token_id
                JOIN users u ON u.id=t.user_id
                LEFT JOIN doctors d ON d.id=t.doctor_id
                JOIN departments dep ON dep.id=t.department_id
                ORDER BY p.updated_at DESC
            """).fetchall()
        return ok(payments=rows)


@app.put("/api/payments/<int:payment_id>/fee")
def update_patient_fee(payment_id):
    if not role("doctor", "receptionist"):
        return fail("Only doctor or receptionist can decide patient fees.", 403)

    data = request.get_json() or {}
    try:
        doctor_fee = float(data.get("doctor_fee", 0))
        other_charges = float(data.get("other_charges", 0))
    except (TypeError, ValueError):
        return fail("Fee values must be valid numbers.")

    if doctor_fee < 0 or other_charges < 0:
        return fail("Fees cannot be negative.")

    with db() as conn:
        payment, error = payment_staff_access(conn, payment_id)
        if error:
            return fail(error, 404 if payment is None and error == "Payment not found." else 403)

        total = round(doctor_fee + other_charges, 2)
        if float(payment["paid_amount"]) > total:
            return fail("New total cannot be lower than the amount already paid.")

        paid = float(payment["paid_amount"])
        status = "paid" if total > 0 and paid == total else "partial" if paid > 0 else "pending"
        row = conn.execute("""
            UPDATE payments
            SET doctor_fee=%s,other_charges=%s,total_amount=%s,status=%s,
                updated_by=%s,updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            RETURNING *
        """, (doctor_fee, other_charges, total, status, session["user_id"], payment_id)).fetchone()
        conn.commit()
        return ok(payment=row)


@app.put("/api/payments/<int:payment_id>")
def update_payment(payment_id):
    if not role("doctor", "receptionist"):
        return fail("Only doctor or receptionist can update payments.", 403)

    data = request.get_json() or {}
    with db() as conn:
        payment, error = payment_staff_access(conn, payment_id)
        if error:
            return fail(error, 404 if payment is None and error == "Payment not found." else 403)

        try:
            paid_amount = float(data.get("paid_amount", payment["paid_amount"]))
        except (TypeError, ValueError):
            return fail("Paid amount must be a valid number.")

        total = float(payment["total_amount"])
        if paid_amount < 0 or paid_amount > total:
            return fail("Paid amount must be between 0 and total amount.")

        status = "paid" if total > 0 and paid_amount == total else "partial" if paid_amount > 0 else "pending"
        row = conn.execute("""
            UPDATE payments
            SET status=%s,paid_amount=%s,updated_by=%s,updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            RETURNING *
        """, (status, paid_amount, session["user_id"], payment_id)).fetchone()
        conn.commit()
        return ok(payment=row)


# Kept as a compatibility endpoint for the old "Bill total" button.
@app.put("/api/payments/<int:payment_id>/charge")
def charge_payment(payment_id):
    if not role("doctor", "receptionist"):
        return fail("Only doctor or receptionist can decide patient fees.", 403)
    data = request.get_json() or {}
    try:
        total = float(data.get("total_amount"))
    except (TypeError, ValueError):
        return fail("Total amount must be a valid number.")
    if total < 0:
        return fail("Total amount cannot be negative.")

    with db() as conn:
        payment, error = payment_staff_access(conn, payment_id)
        if error:
            return fail(error, 404 if payment is None and error == "Payment not found." else 403)
        if float(payment["paid_amount"]) > total:
            return fail("Total cannot be lower than the amount already paid.")
        doctor_fee = min(float(payment["doctor_fee"]), total)
        other = total - doctor_fee
        paid = float(payment["paid_amount"])
        status = "paid" if total > 0 and paid == total else "partial" if paid > 0 else "pending"
        row = conn.execute("""
            UPDATE payments
            SET doctor_fee=%s,other_charges=%s,total_amount=%s,status=%s,
                updated_by=%s,updated_at=CURRENT_TIMESTAMP
            WHERE id=%s RETURNING *
        """, (doctor_fee, other, total, status, session["user_id"], payment_id)).fetchone()
        conn.commit()
        return ok(payment=row)


@app.get("/api/patients")
def search_patients():
    if not role("receptionist", "doctor", "admin"):
        return fail("Staff login required.", 403)
    q=(request.args.get("q") or "").strip()
    with db() as conn:
        rows=conn.execute("""
            SELECT id,name,email,created_at
            FROM users
            WHERE role='patient'
              AND (%s='' OR name ILIKE %s OR email ILIKE %s)
            ORDER BY name
            LIMIT 20
        """,(q,f"%{q}%",f"%{q}%")).fetchall()
        return ok(patients=rows)


@app.post("/api/patients")
def create_patient():
    if not role("receptionist", "admin"):
        return fail("Receptionist or admin access required.", 403)
    data=request.get_json(silent=True) or {}
    name=(data.get("name") or "").strip()
    email=(data.get("email") or "").strip().lower()
    password=data.get("password") or "patient123"
    if not name or not email:
        return fail("Patient name and email are required.")
    with db() as conn:
        try:
            row=conn.execute("""
                INSERT INTO users(name,email,password_hash,role)
                VALUES(%s,%s,%s,'patient')
                RETURNING id,name,email,role,created_at
            """,(name,email,generate_password_hash(password))).fetchone()
            conn.commit()
            return ok(patient=row)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return fail("Email is already registered.",409)


@app.post("/api/tokens/<int:token_id>/assign-doctor")
def assign_token_doctor(token_id):
    if not role("receptionist", "admin"):
        return fail("Receptionist or admin access required.",403)
    doctor_id=(request.get_json(silent=True) or {}).get("doctor_id")
    if not doctor_id:
        return fail("Doctor is required.")
    with db() as conn:
        token=conn.execute("SELECT department_id FROM tokens WHERE id=%s",(token_id,)).fetchone()
        if not token:
            return fail("Token not found.",404)
        doctor=conn.execute("SELECT id,consultation_fee FROM doctors WHERE id=%s AND department_id=%s AND active=TRUE",(doctor_id,token["department_id"])).fetchone()
        if not doctor:
            return fail("Doctor does not belong to this department.")
        row=conn.execute("UPDATE tokens SET doctor_id=%s WHERE id=%s RETURNING *",(doctor_id,token_id)).fetchone()
        conn.execute("""UPDATE payments SET doctor_fee=%s,total_amount=doctor_fee+other_charges,updated_at=CURRENT_TIMESTAMP WHERE token_id=%s AND paid_amount=0""",(doctor["consultation_fee"],token_id))
        reschedule_doctor_queue(conn, doctor_id)
        conn.commit()
        return ok(token=row)


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.get("/api/notifications")
def notifications():
    if not role("patient"):
        return fail("Patient login required.",403)

    with db() as conn:
        rows=conn.execute("""
            SELECT * FROM notifications
            WHERE user_id=%s
            ORDER BY created_at DESC
        """,(session["user_id"],)).fetchall()
        return ok(notifications=rows)


if __name__=="__main__":
    try:
        ensure_schema()
        print("QueueLess database schema checked successfully.")
    except Exception as exc:
        print("Database schema check failed:", exc)
        print("Make sure PostgreSQL is running and DB_NAME/DB_USER/DB_PASSWORD are correct.")
        raise

    app.run(host="127.0.0.1",port=5000,debug=True)
