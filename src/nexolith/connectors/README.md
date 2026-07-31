# Connectors

Connectors translate external data to and from Nexolith's `Rows` representation. CSV and SQL
implementations are constructed through the connector registry.

New connectors should implement the source or destination protocol, raise domain exceptions, close
resources deterministically, and never include credentials or complete connection URLs in errors
or logs. Real PostgreSQL coverage lives in `tests/integration/test_postgresql.py`.
