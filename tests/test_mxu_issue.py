"""Independent A/B issue queues must make progress and obey shared credits."""
from opentpu import target as T, rtlsim


def test_weight_and_scale_issue_continue_when_the_other_port_stalls(have_verilator, monkeypatch):
    flags=list(rtlsim.VFLAGS)
    flags[flags.index('-j')+1]='2'
    monkeypatch.setattr(rtlsim,'VFLAGS',flags)
    sources=[rtlsim.RTL/p for p in T.CORE+T.TECH['generic']]+[rtlsim.TB/'tb_mxu_issue.sv']
    exe=rtlsim.build('tb_mxu_issue',sources,{})
    result=rtlsim.run_sim([str(exe)],timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'MXU_INDEPENDENT_ISSUE_PASS' in result.stdout
