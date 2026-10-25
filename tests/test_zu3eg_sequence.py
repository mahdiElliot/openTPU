"""Request timing must include prefill and avoid counting an extra decode step."""
import numpy as np
import pytest

from opentpu import isa as I
from opentpu.isasim import Config, Machine
from tools.zu3eg.sequence import CheckedRtlBackend, metrics


def test_request_rate_includes_64_prefill_and_63_decode_steps():
    steps = ([{'phase': 'prefill', 'cycles': 10} for _ in range(64)] +
             [{'phase': 'decode', 'cycles': 20} for _ in range(63)])
    result = metrics(steps, 112.5, 64)
    assert result['total_cycles'] == 1900
    assert result['simulated_time_to_first_token_seconds'] == pytest.approx(640 / 112_500_000)
    assert result['simulated_request_output_tokens_per_second'] == pytest.approx(64 * 112_500_000 / 1900)
    assert result['simulated_decode_tokens_per_second'] == pytest.approx(112_500_000 / 20)


def test_single_output_has_no_decode_step():
    result = metrics([{'phase': 'prefill', 'cycles': 100}], 112.5, 1)
    assert result['decode_cycles'] == 0
    assert result['simulated_decode_tokens_per_second'] is None
    assert result['simulated_request_output_tokens_per_second'] == 1_125_000


def test_checked_backend_carries_dram_and_rejects_bad_scratchpad(monkeypatch):
    # Stand in for the expensive subprocess with the golden machine, then corrupt
    # only its scratchpad. This checks the new backend's state and comparison wiring.
    from opentpu import rtlsim

    cfg = Config(DRAM_BYTES=4096, TMEM_WORDS=1024)
    image = np.zeros(256, dtype=np.uint8)
    programs = [[I.halt()]]
    corrupt = False

    def simulate(cfg, programs, images, **kwargs):
        machine = Machine(cfg, programs, images, kwargs['args']).run()
        dram = [s.dram.copy() for s in machine.slices]
        tmem = [s.tmem.copy() for s in machine.slices]
        if corrupt:
            tmem[0][0] ^= np.uint32(1)
        return dram, tmem, {'cycles': 10}

    monkeypatch.setattr(rtlsim, 'run', simulate)
    backend = CheckedRtlBackend(cfg, [image], {})
    backend.write(0, 0, np.array([123], dtype=np.uint32))
    backend.run(programs)
    backend.phase = 'decode'
    backend.run(programs)
    assert int(backend.read(0, 0, 4).view(np.uint32)[0]) == 123
    assert [s['phase'] for s in backend.steps] == ['prefill', 'decode']
    corrupt = True
    with pytest.raises(RuntimeError, match='TMEM mismatch'):
        backend.run(programs)
