# Connectors

Connectors translate external data to and from Nexolith's `Rows` representation. CSV and SQL
implementations are constructed through the connector registry.

New connectors should implement the source or destination protocol, wrap expected I/O or driver
failures in `ConnectorError` with exception chaining, and close resources deterministically.
Public errors and logs must never include usernames, credentials, complete connection URLs, or raw
driver diagnostics. Real PostgreSQL coverage lives in `tests/integration/test_postgresql.py`.
