"""Compatibility alias; implementation lives in features.image_studio.receipts."""

import sys
from .features.image_studio import receipts as _implementation

sys.modules[__name__] = _implementation
