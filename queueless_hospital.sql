-- ============================================================
-- QueueLess Hospital
-- NEW DATABASE NAME: queueless_hospital
--
-- IMPORTANT:
-- Your existing "queueless" database is NOT used by this project.
--
-- This file is written for PostgreSQL psql.
-- Run:
--     psql -U postgres
-- Then paste this complete file.
--
-- The \connect command switches into the new database automatically.
-- If the database already exists, the CREATE DATABASE command will
-- fail but the rest can still be run after connecting manually.
-- ============================================================

CREATE DATABASE queueless_hospital;
\connect queueless_hospital

DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS test_bookings CASCADE;
DROP TABLE IF EXISTS surgery_schemes CASCADE;
DROP TABLE IF EXISTS surgeries CASCADE;
DROP TABLE IF EXISTS tests CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS tokens CASCADE;
DROP TABLE IF EXISTS doctor_breaks CASCADE;
DROP TABLE IF EXISTS doctors CASCADE;
DROP TABLE IF EXISTS departments CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(160) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK
        (role IN ('admin','receptionist','doctor','patient')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE departments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE doctors (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    specialization VARCHAR(120) NOT NULL DEFAULT 'General Medicine',
    department_id INTEGER
        REFERENCES departments(id) ON DELETE SET NULL,
    shift_start TIME NOT NULL DEFAULT '09:00',
    shift_end TIME NOT NULL DEFAULT '17:00',
    consultation_fee NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (consultation_fee >= 0),
    available BOOLEAN NOT NULL DEFAULT TRUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (shift_end > shift_start)
);

CREATE TABLE doctor_breaks (
    id SERIAL PRIMARY KEY,
    doctor_id INTEGER NOT NULL
        REFERENCES doctors(id) ON DELETE CASCADE,
    break_date DATE NOT NULL DEFAULT CURRENT_DATE,
    break_start TIME NOT NULL,
    break_end TIME NOT NULL,
    CHECK (break_end > break_start)
);

CREATE TABLE tokens (
    id SERIAL PRIMARY KEY,
    token_number INTEGER NOT NULL,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL
        REFERENCES departments(id) ON DELETE RESTRICT,
    doctor_id INTEGER
        REFERENCES doctors(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'waiting'
        CHECK (status IN
            ('waiting','called','skipped','completed','cancelled')),
    priority VARCHAR(20) NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('normal','urgent','emergency')),
    priority_set_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    priority_set_at TIMESTAMP,
    booked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    called_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE UNIQUE INDEX uq_token_number_per_department_day
ON tokens(department_id,token_number,(booked_at::date));

CREATE TABLE tests (
    id SERIAL PRIMARY KEY,
    name VARCHAR(160) UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    price NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE test_bookings (
    id SERIAL PRIMARY KEY,
    patient_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    test_id INTEGER NOT NULL
        REFERENCES tests(id) ON DELETE RESTRICT,
    price_at_booking NUMERIC(12,2) NOT NULL CHECK (price_at_booking >= 0),
    booked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'booked'
        CHECK (status IN ('booked','completed','cancelled')),
    booked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE surgeries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(180) UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    base_price NUMERIC(12,2) NOT NULL CHECK (base_price >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE surgery_schemes (
    id SERIAL PRIMARY KEY,
    surgery_id INTEGER NOT NULL
        REFERENCES surgeries(id) ON DELETE CASCADE,
    name VARCHAR(160) NOT NULL,
    discount_percent NUMERIC(5,2) NOT NULL
        CHECK (discount_percent >= 0 AND discount_percent <= 100),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    token_id INTEGER UNIQUE NOT NULL
        REFERENCES tokens(id) ON DELETE CASCADE,
    doctor_fee NUMERIC(12,2) NOT NULL DEFAULT 0
        CHECK (doctor_fee >= 0),
    other_charges NUMERIC(12,2) NOT NULL DEFAULT 0
        CHECK (other_charges >= 0),
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0
        CHECK (total_amount >= 0),
    paid_amount NUMERIC(12,2) NOT NULL DEFAULT 0
        CHECK (paid_amount >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','partial','paid')),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tokens_department_date
ON tokens(department_id,booked_at);

CREATE INDEX idx_tokens_doctor_status
ON tokens(doctor_id,status);

CREATE INDEX idx_breaks_doctor_date
ON doctor_breaks(doctor_id,break_date);

CREATE INDEX idx_test_bookings_patient
ON test_bookings(patient_id);

-- ============================================================
-- DEMO USERS
-- ============================================================

INSERT INTO users(name,email,password_hash,role) VALUES
('QueueLess Admin','admin@hospital.com','admin123','admin'),
('Hospital Receptionist','reception@hospital.com','reception123','receptionist'),
('Dr. Amit Sharma','doctor@hospital.com','doctor123','doctor'),
('Neha Jaiswal','patient@hospital.com','patient123','patient');

-- ============================================================
-- DEPARTMENTS
-- ============================================================

INSERT INTO departments(name,description) VALUES
('Cardiology','Heart and cardiovascular care'),
('Dentistry','Dental consultation and treatment'),
('General Medicine','General consultation'),
('Orthopedics','Bone, joint and muscle care'),
('Pediatrics','Medical care for children'),
('Neurology','Brain and nervous system care');

-- ============================================================
-- DEMO DOCTORS
-- ============================================================

INSERT INTO doctors(user_id,name,specialization,department_id,shift_start,shift_end,consultation_fee)
SELECT u.id,u.name,'Cardiologist',d.id,'09:00','17:00',800
FROM users u CROSS JOIN departments d
WHERE u.email='doctor@hospital.com' AND d.name='Cardiology';

INSERT INTO users(name,email,password_hash,role) VALUES
('Dr. Priya Mehta','dentist@hospital.com','dentist123','doctor'),
('Dr. Rahul Verma','ortho@hospital.com','ortho123','doctor'),
('Dr. Sneha Kapoor','medicine@hospital.com','medicine123','doctor');

INSERT INTO doctors(user_id,name,specialization,department_id,shift_start,shift_end,consultation_fee)
SELECT u.id,u.name,'Dentist',d.id,'10:00','18:00',600
FROM users u CROSS JOIN departments d
WHERE u.email='dentist@hospital.com' AND d.name='Dentistry';

INSERT INTO doctors(user_id,name,specialization,department_id,shift_start,shift_end,consultation_fee)
SELECT u.id,u.name,'Orthopedic Surgeon',d.id,'09:00','16:00',1000
FROM users u CROSS JOIN departments d
WHERE u.email='ortho@hospital.com' AND d.name='Orthopedics';

INSERT INTO doctors(user_id,name,specialization,department_id,shift_start,shift_end,consultation_fee)
SELECT u.id,u.name,'General Physician',d.id,'08:30','16:30',500
FROM users u CROSS JOIN departments d
WHERE u.email='medicine@hospital.com' AND d.name='General Medicine';

-- ============================================================
-- TESTS
-- ============================================================

INSERT INTO tests(name,description,price) VALUES
('Blood Test','Routine blood investigation',500),
('ECG','Electrocardiogram',800),
('X-Ray','Basic X-Ray imaging',700),
('MRI Scan','Magnetic resonance imaging',5000),
('CT Scan','Computed tomography scan',4000),
('Ultrasound','Ultrasound imaging',1500);

-- ============================================================
-- SURGERIES + SAMPLE SCHEMES
-- ============================================================

INSERT INTO surgeries(name,description,base_price)
VALUES
('Heart Bypass Surgery','Cardiac bypass surgery package',250000),
('Knee Replacement','Knee replacement procedure',180000),
('Dental Surgery','Major dental surgical procedure',50000);

INSERT INTO surgery_schemes(surgery_id,name,discount_percent)
VALUES
(
 (SELECT id FROM surgeries WHERE name='Heart Bypass Surgery'),
 'Hospital Support Scheme',15
),
(
 (SELECT id FROM surgeries WHERE name='Knee Replacement'),
 'Senior Support Scheme',10
),
(
 (SELECT id FROM surgeries WHERE name='Dental Surgery'),
 'General Scheme',5
);

-- ============================================================
-- DEMO PATIENTS + SAMPLE QUEUE
-- ============================================================

INSERT INTO users(name,email,password_hash,role) VALUES
('Rahul Sharma','rahul@hospital.com','rahul123','patient'),
('Priya Patel','priya@hospital.com','priya123','patient'),
('Aarav Shah','aarav@hospital.com','aarav123','patient'),
('Ananya Joshi','ananya@hospital.com','ananya123','patient')
ON CONFLICT (email) DO NOTHING;

INSERT INTO tokens(token_number,user_id,department_id,doctor_id,status,priority)
SELECT 1,u.id,d.id,doc.id,'waiting','emergency'
FROM users u, departments d, doctors doc
WHERE u.email='rahul@hospital.com' AND d.name='Cardiology' AND doc.name='Dr. Amit Sharma';

INSERT INTO tokens(token_number,user_id,department_id,doctor_id,status,priority)
SELECT 2,u.id,d.id,doc.id,'waiting','urgent'
FROM users u, departments d, doctors doc
WHERE u.email='priya@hospital.com' AND d.name='Cardiology' AND doc.name='Dr. Amit Sharma';

INSERT INTO tokens(token_number,user_id,department_id,doctor_id,status,priority)
SELECT 3,u.id,d.id,doc.id,'called','normal'
FROM users u, departments d, doctors doc
WHERE u.email='aarav@hospital.com' AND d.name='Cardiology' AND doc.name='Dr. Amit Sharma';

INSERT INTO tokens(token_number,user_id,department_id,doctor_id,status,priority)
SELECT 1,u.id,d.id,doc.id,'waiting','normal'
FROM users u, departments d, doctors doc
WHERE u.email='ananya@hospital.com' AND d.name='Dentistry' AND doc.name='Dr. Priya Mehta';

INSERT INTO payments(token_id,doctor_fee,other_charges,total_amount,paid_amount,status)
SELECT t.id,doc.consultation_fee,0,doc.consultation_fee,0,'pending'
FROM tokens t JOIN doctors doc ON doc.id=t.doctor_id
WHERE t.user_id IN (SELECT id FROM users WHERE email IN ('rahul@hospital.com','priya@hospital.com','aarav@hospital.com','ananya@hospital.com'));
