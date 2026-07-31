# Your first Nexolith pipeline

This tutorial takes you from a fresh clone to a verified CSV output. It uses only local files: no
database, Docker, credentials, `.env`, or external service is required. Allow about five minutes
after Python and `uv` are installed.

## Prerequisites

Install:

- [Git](https://git-scm.com/)
- Python 3.12, 3.13, or 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Confirm that the tools are available:

```bash
git --version
uv --version
```

`uv` creates and manages the Python environment, so you do not need to activate a virtual
environment manually.

## 1. Clone and install

```bash
git clone https://github.com/Reiver56/Nexolith.git
cd Nexolith
uv sync --extra dev
uv run nexolith --version
```

The last command should print:

```text
Nexolith 0.2.0
```

`uv sync` creates `.venv` when needed and installs Nexolith plus its development dependencies from
the lock file.

## 2. Meet the example

The tutorial uses two committed files:

```text
examples/
├── data/
│   └── orders.csv
└── pipelines/
    └── completed_orders.yaml
```

[`orders.csv`](examples/data/orders.csv) contains five orders: three have `completed` status, one
is `pending`, and one is `cancelled`.
[`completed_orders.yaml`](examples/pipelines/completed_orders.yaml) defines this flow:

1. The `csv` **source** reads all five rows from `examples/data/orders.csv`.
2. The ordered **transformations** keep rows whose `status` equals `completed`, select `id`,
   `customer_id`, and `total`, then rename `total` to `order_total`.
3. The `csv` **destination** writes the three remaining rows to
   `build/completed_orders.csv`. Nexolith creates the `build` directory if necessary and
   overwrites the CSV on later runs.

Open the small YAML file before continuing if you want to see the complete declarative pipeline.
Run all following commands from the repository root because its paths are relative to that
directory.

## 3. Validate

Validation checks the YAML structure and configuration without reading input or writing output:

```bash
uv run nexolith validate examples/pipelines/completed_orders.yaml
```

Expected output:

```text
Pipeline 'completed_orders' is valid.
```

## 4. Run

```bash
uv run nexolith run examples/pipelines/completed_orders.yaml
```

The duration varies, but the meaningful output is:

```text
Pipeline: completed_orders
Status: succeeded
Rows read: 5
Rows written: 3
Duration: <varies>s
```

Informational log lines may also appear. A failed pipeline exits with a non-zero status and prints
an error.

## 5. Inspect the output

Use Python's standard CSV reader for the same check in PowerShell, Command Prompt, bash, and other
common shells:

```bash
uv run python -c "import csv; print(*csv.DictReader(open('build/completed_orders.csv', newline='', encoding='utf-8')), sep='\n')"
```

Expected rows:

```text
{'id': '1', 'customer_id': '101', 'order_total': '49.90'}
{'id': '3', 'customer_id': '103', 'order_total': '125.00'}
{'id': '5', 'customer_id': '105', 'order_total': '75.25'}
```

The header and those three rows are the entire generated file. The source remains unchanged.
Because `build/` is ignored by Git, this output will not be committed accidentally.

## Troubleshooting

- **`uv` is not recognized:** install `uv`, restart the shell so its installation directory is on
  `PATH`, and run `uv --version`.
- **Unsupported Python version:** install Python 3.12, 3.13, or 3.14 (for example,
  `uv python install 3.12`), then repeat `uv sync --extra dev`.
- **Pipeline file not found or input CSV cannot be read:** return to the cloned repository root and
  run the commands there. Confirm that both files shown in the tree above exist.
- **Validation fails:** use the reported field path to check the YAML. Spaces matter in YAML; do
  not replace indentation with tabs.
- **The output differs after editing the example:** rerun validation and execution. The
  destination is overwritten on each successful run.

## Next step

Copy `completed_orders.yaml`, change the filter or selected columns, and give the destination a
different path under `build/`. Then review the supported
[connectors and transformations](README.md#connectors). When you need a database destination,
start with SQLite before moving to the optional PostgreSQL example.
