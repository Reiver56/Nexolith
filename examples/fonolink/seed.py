"""FonoLink reference data seed script (stress-test example).

Run with:
    uv run --with faker --with "psycopg[binary]" python examples/fonolink/seed.py

Populates `subscribers` and a pre-existing 90% of `sim_cards` directly in
Postgres (the established customer base). Everything else FonoLink's
pipelines are meant to actually process is written as CSV feed files under
`data/` instead -- ingest_cdr, provision_sim, ingest_support_tickets, and
the two Model B scripts (confirm_activation, reconcile_payments) all read
one of these, giving every pipeline genuine work to do rather than
operating on already-populated tables. See README.md for exactly which
pipeline consumes which feed.

Deterministic and idempotent: fixed SEED, fixed TODAY, TRUNCATE before
insert. Ephemeral dependencies (Faker, psycopg) via `uv run --with`, never
added to Nexolith's own pyproject.toml -- same discipline as the TEC
reference dataset's own seed script.
"""

import csv
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from faker import Faker

SEED = 42
TODAY = date(2026, 8, 1)
DATE_RANGE_DAYS = 14
START_DATE = TODAY - timedelta(days=DATE_RANGE_DAYS - 1)
RECENT_START = TODAY - timedelta(days=6)  # last 7 days vs. the 7 before, for churn scoring

N_SUBSCRIBERS = 800
FRAUD_COUNT = 16
CHURN_COUNT = 45

FONOLINK_DATABASE_URL = os.environ.get(
    "FONOLINK_DATABASE_URL", "postgresql://fonolink:fonolink@localhost:5434/fonolink"
)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

PLANS = ["basic", "standard", "premium", "unlimited"]
PLAN_WEIGHTS = [0.35, 0.35, 0.20, 0.10]

USAGE_PROFILES = ["light", "medium", "heavy"]
PROFILE_WEIGHTS = [0.50, 0.35, 0.15]
# (voice_calls_per_day, data_sessions_per_day, sms_per_day) ranges by profile
PROFILE_RANGES = {
    "light": ((1, 2), (1, 3), (0, 1)),
    "medium": ((2, 4), (3, 6), (1, 2)),
    "heavy": ((4, 8), (6, 12), (2, 4)),
}

TOWERS = [f"TWR-{n:03d}" for n in range(1, 31)]

TICKET_CATEGORIES = ["billing", "technical", "network", "account"]


def _connect() -> psycopg.Connection:
    return psycopg.connect(FONOLINK_DATABASE_URL, autocommit=True)


def truncate_all(conn: psycopg.Connection) -> None:
    # Only the two tables this script writes to directly -- everything else
    # is exclusively pipeline-derived and gets truncated by re-running the
    # DAGs, not this script (a fresh DAG run against append-mode/replace-mode
    # destinations is itself idempotent per the pipelines' own destination
    # modes -- see README.md).
    with conn.cursor() as cur:
        cur.execute("TRUNCATE sim_cards, subscribers RESTART IDENTITY CASCADE")


def generate_subscribers(rng: random.Random, fake: Faker) -> list[dict]:
    subscribers = []
    for i in range(1, N_SUBSCRIBERS + 1):
        activated_at = fake.date_between(start_date="-3y", end_date=START_DATE)
        status = rng.choices(["active", "suspended", "cancelled"], weights=[0.90, 0.05, 0.05])[0]
        subscribers.append(
            {
                "id": i,
                "name": fake.name(),
                "phone_number": fake.unique.numerify("+1-###-###-####"),
                "plan": rng.choices(PLANS, weights=PLAN_WEIGHTS)[0],
                "activated_at": activated_at,
                "status": status,
            }
        )
    return subscribers


def insert_subscribers(conn: psycopg.Connection, subscribers: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO subscribers (id, name, phone_number, plan, activated_at, status)
            VALUES (%(id)s, %(name)s, %(phone_number)s, %(plan)s, %(activated_at)s, %(status)s)
            """,
            subscribers,
        )
    # Keep the sequence consistent with explicit ids inserted above.
    with conn.cursor() as cur:
        cur.execute("SELECT setval('subscribers_id_seq', %s)", (N_SUBSCRIBERS,))


def insert_existing_sim_cards(
    conn: psycopg.Connection, subscribers: list[dict], rng: random.Random
) -> set[int]:
    """~90% of the existing customer base already has an active SIM --
    the established-base assumption the seed data starts from. Returns the
    set of subscriber ids WITHOUT one, who the sim_orders.csv feed (below)
    provisions instead, giving provision_sim/confirm_activation genuine
    incremental work.
    """
    with_sim = rng.sample(subscribers, k=int(N_SUBSCRIBERS * 0.90))
    with_sim_ids = {s["id"] for s in with_sim}
    rows = []
    for sub in with_sim:
        linked_at = datetime.combine(sub["activated_at"], datetime.min.time()) + timedelta(
            days=rng.randint(0, 3)
        )
        rows.append(
            {
                "subscriber_id": sub["id"],
                "iccid": f"8901{sub['id']:015d}",
                "activation_status": "active",
                "linked_at": linked_at,
            }
        )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sim_cards (subscriber_id, iccid, activation_status, linked_at)
            VALUES (%(subscriber_id)s, %(iccid)s, %(activation_status)s, %(linked_at)s)
            """,
            rows,
        )
    return {s["id"] for s in subscribers if s["id"] not in with_sim_ids}


def write_sim_orders_feed(without_sim_ids: list[int]) -> list[dict]:
    """New SIM provisioning orders -- consumed by provision_sim.yaml,
    landed into sim_cards as 'pending' (a literal constant column baked
    into the feed itself -- Nexolith's declarative transforms have no
    "add a constant column" step, and this doesn't need a job)."""
    orders = [
        {"subscriber_id": sid, "iccid": f"8901{sid:015d}9", "activation_status": "pending"}
        for sid in sorted(without_sim_ids)
    ]
    path = DATA_DIR / "sim_orders.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["subscriber_id", "iccid", "activation_status"])
        writer.writeheader()
        writer.writerows(orders)
    return orders


def write_activation_confirmations_feed(orders: list[dict], rng: random.Random) -> None:
    """The network's own confirmation feed -- ~85% of pending orders get
    confirmed; the rest deliberately stay 'pending', a realistic partial-
    activation batch. Consumed by confirm_activation.py (Model B script,
    since it does an UPDATE, not an append -- see that script's own
    docstring for why this can't be a plain Nexolith pipeline)."""
    confirmed = rng.sample(orders, k=int(len(orders) * 0.85))
    path = DATA_DIR / "activation_confirmations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["iccid", "confirmed_at"])
        writer.writeheader()
        for order in confirmed:
            confirmed_at = datetime.combine(TODAY, datetime.min.time()) + timedelta(
                hours=rng.randint(0, 20)
            )
            writer.writerow({"iccid": order["iccid"], "confirmed_at": confirmed_at.isoformat()})


def _daily_volume(rng: random.Random, profile: str) -> tuple[int, int, int]:
    (vlo, vhi), (dlo, dhi), (slo, shi) = PROFILE_RANGES[profile]
    return rng.randint(vlo, vhi), rng.randint(dlo, dhi), rng.randint(slo, shi)


def generate_cdr_feed(
    subscribers: list[dict],
    fraud_ids: set[int],
    churn_ids: set[int],
    rng: random.Random,
) -> int:
    """The day's raw call-detail-record feed -- consumed by ingest_cdr.yaml.
    Fraud subscribers get one deliberately extreme day (a voice-burst or a
    data-volume spike, split evenly, so detect_fraud has two distinct
    signal shapes to find); churn subscribers get a declining-usage trend
    across the second half of the range, on top of their normal profile.
    """
    path = DATA_DIR / "cdr_feed.csv"
    fraud_list = sorted(fraud_ids)
    fraud_voice_burst = set(fraud_list[: len(fraud_list) // 2])
    fraud_data_spike = set(fraud_list[len(fraud_list) // 2 :])
    fraud_day_index = 7  # roughly mid-range

    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["subscriber_id", "call_type", "duration_or_volume", "occurred_at", "cell_tower"]
        )
        for sub in subscribers:
            if sub["status"] != "active":
                continue  # cancelled/suspended subscribers generate no fresh traffic
            profile = rng.choices(USAGE_PROFILES, weights=PROFILE_WEIGHTS)[0]
            for day_index in range(DATE_RANGE_DAYS):
                day = START_DATE + timedelta(days=day_index)
                voice_n, data_n, sms_n = _daily_volume(rng, profile)

                decline_factor = 1.0
                if sub["id"] in churn_ids and day_index >= DATE_RANGE_DAYS // 2:
                    steps_in = day_index - DATE_RANGE_DAYS // 2
                    decline_factor = max(0.05, 0.8 ** (steps_in + 1))
                voice_n = max(0, round(voice_n * decline_factor))
                data_n = max(0, round(data_n * decline_factor))
                sms_n = max(0, round(sms_n * decline_factor))

                for _ in range(voice_n):
                    occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(
                        seconds=rng.randint(0, 86399)
                    )
                    writer.writerow(
                        [
                            sub["id"],
                            "voice",
                            rng.randint(15, 900),
                            occurred_at.isoformat(),
                            rng.choice(TOWERS),
                        ]
                    )
                    row_count += 1
                for _ in range(data_n):
                    occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(
                        seconds=rng.randint(0, 86399)
                    )
                    writer.writerow(
                        [
                            sub["id"],
                            "data",
                            round(rng.uniform(5, 250), 2),
                            occurred_at.isoformat(),
                            rng.choice(TOWERS),
                        ]
                    )
                    row_count += 1
                for _ in range(sms_n):
                    occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(
                        seconds=rng.randint(0, 86399)
                    )
                    writer.writerow(
                        [sub["id"], "sms", 1, occurred_at.isoformat(), rng.choice(TOWERS)]
                    )
                    row_count += 1

                if day_index == fraud_day_index:
                    if sub["id"] in fraud_voice_burst:
                        for _ in range(rng.randint(180, 260)):
                            occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(
                                seconds=rng.randint(0, 86399)
                            )
                            writer.writerow(
                                [
                                    sub["id"],
                                    "voice",
                                    rng.randint(5, 40),
                                    occurred_at.isoformat(),
                                    rng.choice(TOWERS),
                                ]
                            )
                            row_count += 1
                    elif sub["id"] in fraud_data_spike:
                        occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(
                            seconds=rng.randint(0, 86399)
                        )
                        writer.writerow(
                            [
                                sub["id"],
                                "data",
                                round(rng.uniform(6000, 12000), 2),
                                occurred_at.isoformat(),
                                rng.choice(TOWERS),
                            ]
                        )
                        row_count += 1
    return row_count


def write_support_tickets_feed(
    subscribers: list[dict], churn_ids: set[int], rng: random.Random
) -> int:
    """Churn-subset subscribers get 1-3 tickets (mostly technical/billing,
    mostly still open/escalated -- unresolved friction is part of the churn
    signal); everyone else gets the normal low background rate. Consumed by
    ingest_support_tickets.yaml.
    """
    path = DATA_DIR / "support_tickets_feed.csv"
    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["subscriber_id", "opened_at", "category", "status"])
        for sub in subscribers:
            if sub["id"] in churn_ids:
                n_tickets = rng.randint(1, 3)
                categories = ["technical", "billing"]
                statuses = ["open", "escalated"]
            elif rng.random() < 0.06:
                n_tickets = 1
                categories = TICKET_CATEGORIES
                statuses = ["open", "resolved", "escalated"]
            else:
                continue
            for _ in range(n_tickets):
                opened_at = datetime.combine(
                    START_DATE + timedelta(days=rng.randint(0, DATE_RANGE_DAYS - 1)),
                    datetime.min.time(),
                ) + timedelta(hours=rng.randint(0, 23))
                writer.writerow(
                    [sub["id"], opened_at.isoformat(), rng.choice(categories), rng.choice(statuses)]
                )
                row_count += 1
    return row_count


def write_bank_settlements_feed(subscribers: list[dict], rng: random.Random) -> int:
    """Which subscribers settled this billing period -- not an amount (the
    real invoice amount is only known once generate_invoices has actually
    run; reconcile_payments.py looks it up at run time and writes the real
    figure, rather than this feed trying to predict it). Consumed by
    reconcile_payments.py (Model B script).
    """
    path = DATA_DIR / "bank_settlements.csv"
    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["subscriber_id", "period"])
        for sub in subscribers:
            if sub["status"] != "active":
                continue
            if rng.random() < 0.82:
                writer.writerow([sub["id"], "2026-08"])
                row_count += 1
    return row_count


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    fake = Faker()
    Faker.seed(SEED)

    subscribers = generate_subscribers(rng, fake)
    all_ids = [s["id"] for s in subscribers if s["status"] == "active"]
    fraud_ids = set(rng.sample(all_ids, k=FRAUD_COUNT))
    remaining = [i for i in all_ids if i not in fraud_ids]
    churn_ids = set(rng.sample(remaining, k=CHURN_COUNT))

    with _connect() as conn:
        truncate_all(conn)
        insert_subscribers(conn, subscribers)
        without_sim_ids = insert_existing_sim_cards(conn, subscribers, rng)

    orders = write_sim_orders_feed(list(without_sim_ids))
    write_activation_confirmations_feed(orders, rng)
    cdr_rows = generate_cdr_feed(subscribers, fraud_ids, churn_ids, rng)
    ticket_rows = write_support_tickets_feed(subscribers, churn_ids, rng)
    settlement_rows = write_bank_settlements_feed(subscribers, rng)

    print(f"subscribers: {N_SUBSCRIBERS}")
    print(f"existing active sim_cards: {N_SUBSCRIBERS - len(without_sim_ids)}")
    print(f"sim_orders feed (new provisioning): {len(orders)}")
    print(f"cdr_feed rows: {cdr_rows}")
    print(f"support_tickets_feed rows: {ticket_rows}")
    print(f"bank_settlements_feed rows: {settlement_rows}")
    print(f"fraud subset: {sorted(fraud_ids)}")
    print(f"churn subset ({len(churn_ids)} subscribers): {sorted(churn_ids)[:10]}...")


if __name__ == "__main__":
    main()
