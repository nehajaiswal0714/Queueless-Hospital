-- Run this ONLY if queueless_hospital was created from the earlier
-- QueueLess Hospital version and you do not want to recreate the DB.

ALTER TABLE doctors
    ADD COLUMN IF NOT EXISTS consultation_fee NUMERIC(12,2) NOT NULL DEFAULT 0;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS doctor_fee NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS other_charges NUMERIC(12,2) NOT NULL DEFAULT 0;

UPDATE payments
SET doctor_fee = COALESCE(total_amount,0),
    other_charges = 0
WHERE doctor_fee = 0 AND COALESCE(total_amount,0) > 0;

UPDATE doctors
SET consultation_fee = 0
WHERE consultation_fee IS NULL;
