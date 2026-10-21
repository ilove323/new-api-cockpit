"""Compatibility alias; implementation lives in features.image_studio.contracts."""

import sys
from .features.image_studio import contracts as _implementation

sys.modules[__name__] = _implementation
