"""MYIR chat templates, recurrent-state reset and EOS behavior."""
from types import SimpleNamespace

import numpy as np

from tools.zu3eg import chat_board as chat


def test_chat_uses_template_without_thinking():
    class Tokenizer:
        def apply_chat_template(self, history, **kwargs):
            assert history == [{'role': 'user', 'content': 'Hello'}]
            assert kwargs == dict(tokenize=True, add_generation_prompt=True, enable_thinking=False)
            return {'input_ids': [4, 5, 6]}
    assert chat.prompt_ids(Tokenizer(), [{'role': 'user', 'content': 'Hello'}]) == [4, 5, 6]


def test_recurrent_reset_clears_only_merged_state_regions():
    writes, events = [], []
    class Backend:
        def write(self, sid, address, data):
            assert sid == 0 and not np.any(data)
            writes.append((address, data.nbytes))
    eng = SimpleNamespace(
        image=SimpleNamespace(state_regions=lambda: [(32, 16), (16, 16), (128, 4)]),
        backend=Backend(), _drain=lambda: events.append('drain'), reset=lambda: events.append('reset'))
    chat.clear_recurrent_state(eng)
    assert writes == [(16, 32), (128, 4)]
    assert events == ['drain', 'reset']


def test_reply_streams_seed_and_stops_at_eos(monkeypatch, capsys):
    monkeypatch.setattr(chat, 'clear_recurrent_state', lambda eng: None)
    monkeypatch.setattr(chat, 'forward_prefill', lambda *a, **kw: np.array([0, 9, 0], np.float32))
    steps = []
    def step(token):
        steps.append(token)
        return np.array([0, 0, 9], np.float32)
    eng = SimpleNamespace(rows=4, spec=SimpleNamespace(eos=(2,)), step=step)
    tok = SimpleNamespace(eos_token_id=2, decode=lambda ids, **kwargs: ''.join('A' for _ in ids))
    assert chat.reply(eng, tok, [7, 8], 8) == 'A'
    assert steps == [1]
    assert 'TTFT' in capsys.readouterr().out
