"""Compatibility alias; implementation lives in features.image_studio.transport."""

import sys
from .features.image_studio import transport as _implementation

sys.modules[__name__] = _implementation
