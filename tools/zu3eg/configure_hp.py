"""Configure active HP0[/HP1] credits and static metadata QoS while idle."""
import argparse
import json
from pathlib import Path

from .jtag import CONTROL_BASE, Jtag


def inspect(link, ports):
    """Read PS MMIO through the debugger, including when DDR uses Ethernet."""
    control = getattr(link, 'control', link)
    result = {}
    for hp in ports:
        if hp not in (0, 1):
            raise ValueError('HP2 is reserved')
        base = 0xfd380000 + hp*0x10000
        result[f'hp{hp}'] = {
            'read_credits': (control.words(base+4)[0] & 15)+1,
            'write_credits': (control.words(base+0x18)[0] & 15)+1,
            'read_qos': control.words(base+8)[0] & 15,
            'write_qos': control.words(base+0x1c)[0] & 15,
        }
    return result


def configure(link, hardware, metadata_qos=4, read_credits=None):
    ports = hardware.get('hp_ports', [0])
    if ports not in ([0], [0, 1]):
        raise ValueError('Use HP0 or HP0/HP1; HP2 and HP3 are reserved')
    if not 0 <= metadata_qos <= 15:
        raise ValueError('Metadata QoS must be between 0 and 15')
    if read_credits is not None and read_credits not in (4, 8, 16):
        raise ValueError('Bulk read credits must be 4, 8 or 16')
    status = link.words(CONTROL_BASE + 0xc)[0]
    if status & 0x84 or not status & 8:
        raise RuntimeError('Stop the accelerator and drain writes before configuring HP ports')
    if hardware.get('memory_stats') and len(ports) > 1:
        if link.words(CONTROL_BASE+0x310, 2) != [0x4d485031, len(ports)]:
            raise RuntimeError('The active FPGA HP-port count differs from the selected hardware')
        if hardware.get('stripe_weights') and link.words(CONTROL_BASE+0x318)[0] != 1:
            raise RuntimeError('The active FPGA does not support the selected striped weight path')
    result = {}
    for hp in ports:
        base = 0xfd380000 + hp*0x10000
        reads = (2 if hp == 1 and not hardware.get('stripe_weights') else
                 min(hardware['read_depth'], read_credits or 16))
        writes = min(hardware['write_depth'], 16)
        qos = metadata_qos if hp == 1 and not hardware.get('stripe_weights') else 0
        # UG1085 AFIFM: RD/WRCTRL bit 2 selects fabric QoS (0 = static);
        # RD/WRISSUE bits 3:0 encode the number of outstanding commands minus one.
        fields = [(0x0, 4, 0), (0x4, 15, reads-1), (0x8, 15, qos),
                  (0x14, 4, 0), (0x18, 15, writes-1), (0x1c, 15, qos)]
        settings = {}
        for offset, mask, wanted in fields:
            address = base+offset
            old = link.words(address)[0]
            value = (old & ~mask) | wanted
            if old != value:
                link.write_word(address, value)
            if link.words(address)[0] != value:
                raise RuntimeError(f'HP{hp} configuration readback differs at {address:#x}')
            settings[hex(offset)] = value
        result[f'hp{hp}'] = {'read_credits': reads, 'write_credits': writes,
                             'static_qos': qos, 'registers': settings}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', type=Path, required=True)
    parser.add_argument('--metadata-qos', type=int, choices=range(16), default=4,
                        help='static metadata priority in legacy unstriped builds; striped ports use equal priority')
    parser.add_argument('--read-credits', type=int, choices=(4, 8, 16),
                        help='bulk HP read credits, capped by the bridge depth (default: up to 16)')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    hardware = json.loads((args.hardware/'configuration.json').read_text())
    link = Jtag(args.out)
    try:
        result = configure(link, hardware, args.metadata_qos, args.read_credits)
        (args.out/'hp_configuration.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
    finally:
        link.close()


if __name__ == '__main__':
    main()
