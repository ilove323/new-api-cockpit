"""Narrow host services, supplied by the application's composition root."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class HostServices:
    access: Callable
    validate_write: Callable
    site_name: Callable
