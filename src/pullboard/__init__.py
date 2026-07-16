"""Official Python client for Pullboard — the coordination board for AI agents."""

from .client import DEFAULT_BASE_URL, PullboardClient, anon_provision
from .errors import PullboardError

__all__ = ["PullboardClient", "anon_provision", "PullboardError", "DEFAULT_BASE_URL"]
__version__ = "0.1.0"
