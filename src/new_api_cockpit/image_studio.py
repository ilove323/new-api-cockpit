"""Compatibility alias; implementation lives in features.image_studio.service."""

import sys
from .features.image_studio import service as _implementation

sys.modules[__name__] = _implementation
