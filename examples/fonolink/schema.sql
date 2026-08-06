-- FonoLink reference schema (stress-test example, NOT part of nexolith itself).
-- Ten tables: three are pre-seeded master/reference data (subscribers, plus
-- partial sim_cards), the rest are either external-feed landing targets or
-- fully pipeline-derived. See examples/fonolink/README.md for exactly which
-- pipeline populates which table.

CREATE TABLE subscribers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL CHECK (plan IN ('basic', 'standard', 'premium', 'unlimited')),
    activated_at DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'cancelled'))
);
CREATE INDEX idx_subscribers_status ON subscribers(status);
CREATE INDEX idx_subscribers_plan ON subscribers(plan);

CREATE TABLE sim_cards (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    iccid TEXT NOT NULL UNIQUE,
    activation_status TEXT NOT NULL CHECK (activation_status IN ('pending', 'active', 'deactivated')),
    linked_at TIMESTAMP
);
CREATE INDEX idx_sim_cards_subscriber ON sim_cards(subscriber_id);
CREATE INDEX idx_sim_cards_status ON sim_cards(activation_status);

CREATE TABLE cdr_raw (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    call_type TEXT NOT NULL CHECK (call_type IN ('voice', 'data', 'sms')),
    duration_or_volume NUMERIC NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    cell_tower TEXT NOT NULL
);
CREATE INDEX idx_cdr_raw_subscriber ON cdr_raw(subscriber_id);
CREATE INDEX idx_cdr_raw_occurred_at ON cdr_raw(occurred_at);
CREATE INDEX idx_cdr_raw_subscriber_occurred ON cdr_raw(subscriber_id, occurred_at);

CREATE TABLE usage_daily (
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    date DATE NOT NULL,
    voice_seconds INTEGER NOT NULL DEFAULT 0,
    data_mb NUMERIC NOT NULL DEFAULT 0,
    sms_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subscriber_id, date)
);

CREATE TABLE billing_charges (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    period TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    plan_rate_applied NUMERIC NOT NULL,
    UNIQUE (subscriber_id, period)
);

CREATE TABLE invoices (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    period TEXT NOT NULL,
    total_amount NUMERIC NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('issued', 'paid', 'overdue', 'void')),
    UNIQUE (subscriber_id, period)
);
CREATE INDEX idx_invoices_status ON invoices(status);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount NUMERIC NOT NULL,
    paid_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'pending'))
);
CREATE INDEX idx_payments_invoice ON payments(invoice_id);

CREATE TABLE fraud_flags (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    reason TEXT NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    cdr_reference INTEGER REFERENCES cdr_raw(id)
);
CREATE INDEX idx_fraud_flags_subscriber ON fraud_flags(subscriber_id);

CREATE TABLE support_tickets (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    opened_at TIMESTAMP NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('billing', 'technical', 'network', 'account')),
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'escalated'))
);
CREATE INDEX idx_support_tickets_subscriber ON support_tickets(subscriber_id);

CREATE TABLE churn_scores (
    subscriber_id INTEGER PRIMARY KEY REFERENCES subscribers(id),
    score NUMERIC NOT NULL,
    computed_at TIMESTAMP NOT NULL,
    contributing_factors TEXT NOT NULL
);
