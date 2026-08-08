"""Export Nexolith's deterministic OpenAPI schema without starting a server."""

import json
import sys

from nexolith.api.app import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
