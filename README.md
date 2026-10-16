# QueueLess – Smart Hospital Queue Management System

QueueLess is a Flask + PostgreSQL hospital-only queue management website.

## Design
The interface keeps the earlier QueueLess visual style: clean white cards, blue accents, dashboard cards, queue tables, responsive layouts and a simple hospital-focused landing page.

## Technology
- Python
- Flask
- PostgreSQL
- psycopg
- HTML5
- CSS3
- Vanilla JavaScript
- Jinja2
- Flask sessions

No React, Node/npm, SQLite, SQLAlchemy, Firebase, Django or frontend framework is required.

## Database
This version uses a separate database:

`queueless_hospital`

Your existing `queueless` database is not used.

Run `queueless_hospital.sql` once in PostgreSQL/psql to create the complete database, tables and demo data.

If you already created `queueless_hospital` from an older QueueLess version, run `fee_update.sql` first, then use the updated project.

## Run
1. Open `app.py` and change only `DB_PASSWORD` if your PostgreSQL password is different.
2. Install packages:

```text
pip install -r requirements.txt
```

3. Start the website:

```text
python app.py
```

4. Open:

`http://127.0.0.1:5000`

## Demo accounts

Patient: `patient@hospital.com` / `patient123`  
Receptionist: `reception@hospital.com` / `reception123`  
Doctor: `doctor@hospital.com` / `doctor123`  
Dentist: `dentist@hospital.com` / `dentist123`  
Orthopedic doctor: `ortho@hospital.com` / `ortho123`  
General physician: `medicine@hospital.com` / `medicine123`  
Admin: `admin@hospital.com` / `admin123`

Admin signup key: `ADMIN123`

## Main features
- Hospital departments and doctors
- Doctor-specific timings and breaks
- Automatic Available / On break / Off duty status
- Virtual patient tokens
- Normal / Urgent / Emergency priority
- Doctor and receptionist queue controls
- Doctor default consultation fees
- Patient-specific consultation fees
- Receptionist and doctor can decide patient fees
- Additional charges and payment tracking
- Pending / Partial / Paid status
- Test catalogue and test booking
- Only doctors can add/edit/delete test details
- Surgery prices and discount schemes
- Patient, doctor, receptionist and admin dashboards
