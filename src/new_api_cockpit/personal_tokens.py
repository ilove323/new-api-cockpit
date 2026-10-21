"""Compatibility alias for the shared owner-only token primitives."""

import sys
from .integrations.new_api import personal_tokens as _implementation

sys.modules[__name__] = _implementation
