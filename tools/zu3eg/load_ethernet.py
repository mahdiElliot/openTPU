"""Upload and SHA-256-readback-verify a prepared model over direct Ethernet."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import zlib

import numpy as np

from .ethernet import Ethernet
from .evaluate import ROOT
from .jtag import Jtag, CONTROL_BASE
from .run_board import file_digest
from .sequence import save


def restore_chunk(link, address, actual, expected, block_bytes=64 << 10):
    """Repair changed DDR blocks; return bytes actually read back after repair."""
    actual = np.asarray(actual, dtype=np.uint8).copy()
    expected = np.frombuffer(expected, dtype=np.uint8)
    if actual.size != expected.size:
        raise ValueError('Restore buffers differ in size')
    repaired = 0
    for at in range(0, actual.size, block_bytes):
        end = min(at+block_bytes, actual.size)
        if not np.array_equal(actual[at:end], expected[at:end]):
            link.write(address+at, expected[at:end])
            readback = link.read(address+at, end-at)
            if not np.array_equal(readback, expected[at:end]):
                raise RuntimeError(f'Restored DDR block readback differs at {address+at:#x}')
            actual[at:end] = readback
            repaired += end-at
    return actual, repaired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--hardware', type=Path, required=True)
    parser.add_argument('--address', default='192.168.200.2')
    parser.add_argument('--keep-running', action='store_true',
                        help='leave the Arm monitor running for Ethernet inference data transfers')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify-only', action='store_true',
                        help='verify the entire existing DDR image without rewriting it; useful after a readback timeout')
    mode.add_argument('--restore-pristine', action='store_true',
                      help='repair only changed 64-KiB DDR blocks, then verify full-image SHA-256')
    options = parser.parse_args()
    image, hardware = options.image.resolve(), options.hardware.resolve()
    manifest = json.loads(image.with_suffix('.json').read_text())
    configuration = json.loads((hardware / 'configuration.json').read_text())
    if file_digest(image) != manifest['sha256']:
        raise RuntimeError('Local prepared-image checksum mismatch')
    if manifest['config'] != configuration['arch']:
        raise RuntimeError('The model image and hardware architecture differ')
    if 0x10000000 + image.stat().st_size >= 0x80000000:
        raise RuntimeError('The model exceeds the accelerator DDR window')
    loaded_path = image.parent / 'model_loaded.json'
    if loaded_path.exists():
        previous = json.loads(loaded_path.read_text())
        previous['pristine'] = False
        save(loaded_path, previous)
    link = Ethernet(options.address)
    base, total, chunk = 0x10000000, image.stat().st_size, 8 << 20
    begin = time.monotonic()
    try:
        cfg = manifest['config']
        wanted = [0x4f545055, (cfg['D'] << 16) | (cfg['MCOLS'] << 8) | cfg['LANES']]
        identity = link.read(CONTROL_BASE, 8).view('<u4').tolist()
        if identity != wanted:
            raise RuntimeError(f'Unexpected accelerator identity: {identity}')
        status = int(link.read(CONTROL_BASE + 0xc, 4).view('<u4')[0])
        if status & 0x80 or not status & 8:
            raise RuntimeError('Stop the accelerator and wait for DDR writes to drain before loading a model')
        if options.verify_only or options.restore_pristine:
            print('ETHERNET_RESTORE_PRISTINE' if options.restore_pristine else
                  'ETHERNET_VERIFY_ONLY checking existing DDR; no model writes', flush=True)
            upload_seconds = 0.0
        else:
            for at in range(0, total, chunk):
                size = min(chunk, total - at)
                with image.open('rb') as stream:
                    stream.seek(at)
                    data = stream.read(size)
                link.write(base + at, np.frombuffer(data, dtype=np.uint8))
                if link.crc32(base + at, size) != zlib.crc32(data):
                    raise RuntimeError(f'Ethernet DDR CRC mismatch at {at}')
                print(f'ETHERNET_LOADED {at+size}/{total} bytes, CRC verified, {time.monotonic()-begin:.2f} s', flush=True)
            upload_seconds = time.monotonic() - begin
        digest = hashlib.sha256()
        restored_bytes = 0
        read_begin = time.monotonic()
        for at in range(0, total, chunk):
            size = min(chunk, total - at)
            data = link.read(base + at, size)
            if options.restore_pristine:
                with image.open('rb') as stream:
                    stream.seek(at)
                    expected = stream.read(size)
                data, repaired = restore_chunk(link, base+at, data, expected)
                restored_bytes += repaired
            digest.update(data)
            if at == 0 or at + size == total or (at + size) % (64 << 20) == 0:
                print(f'ETHERNET_READBACK {at+size}/{total} bytes', flush=True)
        remote_sha = digest.hexdigest()
        if remote_sha != manifest['sha256']:
            raise RuntimeError('Full physical-DDR readback SHA-256 mismatch')
        verification_seconds = time.monotonic() - read_begin
        print('ETHERNET_FULL_DDR_SHA256_PASS', remote_sha, flush=True)
        khz = int(link.read(CONTROL_BASE + 0x44, 4).view('<u4')[0])
        if khz != round(configuration['core_mhz'] * 1000):
            raise RuntimeError('The programmed core clock differs from the hardware build')
    finally:
        link.close()
    # Pure JTAG inference halts the monitor; Ethernet DDR transfers need it alive.
    if not options.keep_running:
        debug = Jtag(image.parent / 'ethernet_handoff')
        try:
            debug.command('targets -set -filter {name == "Cortex-A53 #0"}; stop; '
                          'targets -set -filter {name == "PSU"}')
        finally:
            debug.close()
    result = {'image_bytes': total, 'ddr_base': hex(base), 'method': 'bare-metal A53 Ethernet monitor',
              'sha256': manifest['sha256'], 'full_ddr_readback_sha256': remote_sha,
              'full_readback_passed': True, 'sample_readbacks_passed': True,
              'target': manifest['target'], 'hardware_id': file_digest(hardware / 'opentpu_zu3eg.bit'),
              'accelerator_restored': True, 'pristine': True,
              'cpu0_halted': not options.keep_running,
              'verification_only': options.verify_only,
              'restore_pristine': options.restore_pristine,
              'restored_bytes': restored_bytes,
              'upload_and_crc_seconds': upload_seconds,
              'full_readback_seconds': verification_seconds}
    save(loaded_path, result)
    save(image.parent / 'ethernet_load_result.json', result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
