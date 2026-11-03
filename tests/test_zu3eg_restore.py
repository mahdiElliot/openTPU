"""Delta reloads must verify repaired bytes and leave equal blocks alone."""
import numpy as np
import pytest

from tools.zu3eg.load_ethernet import restore_chunk


class Memory:
    def __init__(self, contents, corrupt=False):
        self.contents = np.array(contents, dtype=np.uint8)
        self.writes = []
        self.corrupt = corrupt

    def write(self, address, data):
        self.writes.append((address, len(data)))
        if not self.corrupt:
            self.contents[address:address+len(data)] = data

    def read(self, address, size):
        return self.contents[address:address+size].copy()


def test_restore_changes_only_different_blocks_and_checks_partial_tail():
    expected = np.arange(23, dtype=np.uint8)
    old = expected.copy()
    old[9] ^= 1
    old[-1] ^= 1
    memory = Memory(old)
    actual, repaired = restore_chunk(memory, 0, old, expected.tobytes(), block_bytes=8)
    assert memory.writes == [(8, 8), (16, 7)]
    assert repaired == 15
    assert np.array_equal(actual, expected)
    assert np.array_equal(memory.contents, expected)


def test_restore_cannot_report_success_when_a_write_was_lost():
    memory = Memory(np.zeros(8, np.uint8), corrupt=True)
    with pytest.raises(RuntimeError, match='readback differs'):
        restore_chunk(memory, 0, memory.contents, bytes([1]*8), block_bytes=8)
