"""Load DDR through the temporary AXI/JTAG image, then restore the accelerator."""
import argparse
import json
import subprocess
from pathlib import Path

from .evaluate import ROOT
from .jtag import Jtag, quote
from .program_board import program
from .sequence import save
from .run_board import file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loader', type=Path, default=ROOT / 'build/zu3eg/deployment512_16/loader')
    parser.add_argument('--hardware', type=Path, default=ROOT / 'build/zu3eg/deployment512_16/hardware')
    parser.add_argument('--image', type=Path, default=ROOT / 'build/zu3eg/deployment512_16/run/model_image.bin')
    parser.add_argument('--limit', type=int, default=0, help='test only this many bytes at a scratch address')
    options = parser.parse_args()
    if not options.image.is_file() or options.image.stat().st_size % 8:
        parser.error('the prepared model image must exist and contain whole 64-bit beats')
    if not options.limit:
        manifest = json.loads(options.image.with_suffix('.json').read_text())
        if file_digest(options.image) != manifest['sha256']:
            raise RuntimeError('Prepared model checksum mismatch')
    bit = options.loader.resolve() / 'loader.bit'
    ps_init = options.loader.resolve() / 'project/loader.gen/sources_1/bd/loader/ip/loader_ps_0/psu_init.tcl'
    if not bit.is_file() or not ps_init.is_file():
        raise FileNotFoundError('Build the temporary loader first')
    link = Jtag(options.loader / 'program')
    try:
        if link.words(0xFD070004)[0] & 7 != 1:
            raise RuntimeError('DDR is not initialized')
        link.command('targets -set -filter {name == "Cortex-A53 #0"}; '
                     'if {[catch {stop} message] && $message ne "Already stopped"} {error $message}; '
                     'targets -set -filter {name == "PSU"}')
        print('Programming temporary AXI loader; preserving initialized PS DDR...', flush=True)
        link.command(f'targets -set -filter {{name == "PL"}}; fpga -file {quote(bit)}; targets -set -filter {{name == "PSU"}}')
        link.command(f'source {quote(ps_init)}; init_ps $psu_afi_config; psu_ps_pl_isolation_removal; psu_ps_pl_reset_config')
    finally:
        link.close()
    address = 0x50000000 if options.limit else 0x10000000
    log_path = options.loader / ('upload_test.log' if options.limit else 'upload.log')
    print(f'Uploading DDR image; detailed progress: {log_path.resolve()}', flush=True)
    command = ['/home/mahdi/vivado/Vivado/2022.2/bin/vivado', '-mode', 'batch', '-notrace',
               '-source', str(ROOT / 'boards/mys-zu3eg/load_axi.tcl'),
               '-tclargs', str(options.image.resolve()), hex(address), str(options.limit)]
    with log_path.open('w') as log:
        result = subprocess.run(command, cwd=options.loader, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode or 'AXI_UPLOAD_COMPLETE' not in log_path.read_text():
        raise RuntimeError(f'AXI upload failed: {log_path}')
    print('AXI upload and sample readbacks passed.', flush=True)
    link = Jtag(options.loader / 'restore')
    try:
        restored = program(link, options.hardware)
        save(options.loader / 'restore_result.json', restored)
    finally:
        link.close()
    if not options.limit:
        save(options.image.parent / 'model_loaded.json', {
            'image_bytes': options.image.stat().st_size, 'ddr_base': hex(address),
            'method': 'temporary 64-bit JTAG-to-AXI loader',
            'sha256': manifest['sha256'], 'target': manifest['target'],
            'hardware_id': file_digest(options.hardware / 'opentpu_zu3eg.bit'),
            'sample_readbacks_passed': True, 'accelerator_restored': True, 'pristine': True})


if __name__ == '__main__':
    main()
