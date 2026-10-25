"""Compatibility alias; implementation lives in features.image_studio.results."""

import sys
from .features.image_studio import results as _implementation

sys.modules[__name__] = _implementation
