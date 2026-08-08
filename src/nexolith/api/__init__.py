"""Read-only monitoring API.

FastAPI and Uvicorn are optional dependencies. Importing this package does
not load either dependency or open the Nexolith state database.
"""

from typing import Literal

API_VERSION: Literal["v1"] = "v1"

__all__ = ["API_VERSION"]
