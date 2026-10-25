"""Bounded, process-local offsets into an already validated journal snapshot.

No journal copies or persistent sidecars. Readers verify the original private
inode and the hashes of the blocks they actually read; a later append cannot
extend the published prefix or change previously validated event bytes.
"""

from array import array
from bisect import bisect_right
from collections.abc import Sequence
from contextlib import closing
import hashlib
import os
import sys

from .fs import SafeFS, storage_error, strict_json

BLOCK_BYTES = 65536


def memory_size(value):
    """Count the retained graph once, including arrays, not journal byte size."""
    total, seen, pending = 0, set(), [value]
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
    return total


class EventIndex(Sequence):
    def __init__(self, fd, raw, count):
        # The caller transfers an owned, verified fd only after matching the
        # source fingerprint to the bytes used by the complete journal scan.
        self.fd = -1
        self.size = len(raw)
        self.offsets = array("Q", [0])
        start = 0
        while start < len(raw):
            end = raw.find(b"\n", start)
            if end < 0:
                raise ValueError("incomplete indexed journal")
            start = end + 1
            self.offsets.append(start)
        if len(self.offsets) != count + 1:
            raise ValueError("indexed event count mismatch")
        self.starts = array("Q")
        self.blocks = []
        first = 0
        for end in range(1, len(self.offsets)):
            if self.offsets[end] - self.offsets[first] >= BLOCK_BYTES or end == count:
                self.starts.append(first)
                self.blocks.append(
                    (
                        self.offsets[first],
                        self.offsets[end],
                        hashlib.sha256(
                            raw[self.offsets[first] : self.offsets[end]]
                        ).digest(),
                    )
                )
                first = end
        self.fd = fd

    def __del__(self):
        fd = getattr(self, "fd", -1)
        if fd >= 0:
            self.fd = -1
            os.close(fd)

    @property
    def memory_bytes(self):
        return memory_size((self.offsets, self.starts, self.blocks))

    def __len__(self):
        return len(self.offsets) - 1

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return list(self.iter_range(start, stop))
            return [self[item] for item in range(start, stop, step)]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        with closing(self.iter_range(index, index + 1)) as events:
            return next(events)

    def __iter__(self):
        return self.iter_range(0, len(self))

    def iter_range(self, start, stop):
        if start >= stop:
            return
        fd = os.dup(self.fd)
        try:
            if SafeFS._regular(fd).st_size < self.size:
                raise storage_error()
            block_id, content, base = -1, b"", 0
            for index in range(start, stop):
                current = bisect_right(self.starts, index) - 1
                if current != block_id:
                    base, end, digest = self.blocks[current]
                    content = os.pread(fd, end - base, base)
                    if (
                        len(content) != end - base
                        or hashlib.sha256(content).digest() != digest
                    ):
                        raise storage_error()
                    block_id = current
                yield strict_json(
                    content[
                        self.offsets[index] - base : self.offsets[index + 1] - base
                    ].decode("utf-8")
                )
            SafeFS._regular(fd)
        finally:
            os.close(fd)
