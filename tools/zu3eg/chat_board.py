"""Terminal chat on MYIR using the prepared int4/int8-head image and Ethernet."""
import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from opentpu import target as T
from .ethernet import Ethernet, EthernetBackend, EthernetDataLink
from .evaluate import MODEL
from .jtag import CONTROL_BASE, Jtag
from .run_board import engine, file_digest, forward_prefill
from .sequence import save


def prompt_ids(tokenizer, history):
    ids = tokenizer.apply_chat_template(history, tokenize=True, add_generation_prompt=True,
                                         enable_thinking=False)
    return list(ids['input_ids'] if hasattr(ids, 'keys') else ids)


def clear_recurrent_state(eng):
    # Re-rendering a completed reply can change the template's token prefix.
    # Replay the conversation from a clean recurrent state rather than rolling
    # back only KV positions, which is invalid for Qwen3.5's DeltaNet state.
    eng._drain()
    regions = sorted(eng.image.state_regions())
    merged = []
    for address, size in regions:
        if merged and address <= merged[-1][0] + merged[-1][1]:
            start, previous = merged[-1]
            merged[-1] = (start, max(start + previous, address + size) - start)
        else:
            merged.append((address, size))
    for address, size in merged:
        for offset in range(0, size, 1 << 20):
            eng.backend.write(0, address + offset, np.zeros(min(1 << 20, size-offset), np.uint8))
    eng.reset()


def reply(eng, tokenizer, ids, max_new):
    clear_recurrent_state(eng)
    begin = time.monotonic()
    logits = forward_prefill(eng, ids, lambda *args: None, True, rows=eng.rows)
    first_token_seconds = time.monotonic() - begin
    eos = set(getattr(eng.spec, 'eos', ()))
    if tokenizer.eos_token_id is not None:
        eos.add(tokenizer.eos_token_id)
    generated, shown, decode_steps = [], '', 0
    decode_begin = time.monotonic()
    for index in range(max_new):
        if not np.isfinite(logits).all():
            raise RuntimeError('Nonfinite board logits')
        token = int(np.argmax(logits))
        if token in eos:
            break
        generated.append(token)
        text = tokenizer.decode(generated, skip_special_tokens=True)
        # Tokenizer decoding can temporarily end in a partial UTF-8 character.
        visible = text.rstrip('\ufffd')
        if visible.startswith(shown):
            print(visible[len(shown):], end='', flush=True)
            shown = visible
        if index + 1 < max_new:
            logits = eng.step(token)
            decode_steps += 1
    text = tokenizer.decode(generated, skip_special_tokens=True)
    if text.startswith(shown):
        print(text[len(shown):], end='', flush=True)
    elapsed = time.monotonic() - begin
    decode_seconds = time.monotonic() - decode_begin
    print(f'\n[TTFT {first_token_seconds:.2f}s; '
          f'decode {decode_steps/decode_seconds:.2f} steps/s; '
          f'end-to-end {len(generated)/elapsed:.2f} output tokens/s]', flush=True)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True, help='directory containing the prepared and loaded image')
    parser.add_argument('--model', type=Path, default=MODEL)
    parser.add_argument('--address', default='192.168.200.2')
    parser.add_argument('--max-new', type=int, default=64)
    parser.add_argument('--prompt', help='one question, then exit; otherwise enter interactive mode')
    options = parser.parse_args()
    if options.max_new < 1:
        parser.error('--max-new must be positive')
    for name in ('OTPU_IMG_CACHE', 'OTPU_IMAGE_CACHE', 'OTPU_PROG_CACHE'):
        os.environ[name] = '0'
    os.environ.pop('OTPU_FORMATS', None)
    run, hardware = options.run.resolve(), options.hardware.resolve()
    config = json.loads((hardware / 'configuration.json').read_text())
    manifest = json.loads((run / 'model_image.json').read_text())
    loaded = json.loads((run / 'model_loaded.json').read_text())
    target = config['target']
    cfg = T.config(target)
    hardware_id = file_digest(hardware / 'opentpu_zu3eg.bit')
    if config['arch'] != asdict(cfg) or manifest['config'] != asdict(cfg):
        raise RuntimeError('The target, hardware and prepared image differ')
    if not (loaded.get('pristine') and loaded.get('full_readback_passed') and
            loaded.get('hardware_id') == hardware_id and loaded.get('sha256') == manifest['sha256']):
        raise RuntimeError('Reload this image with load_ethernet --keep-running before starting chat')
    for fmt in ('int4', 'int8'):
        check = json.loads((run / f'kernel_{fmt}_result.json').read_text())
        if not check['passed'] or check['hardware_id'] != hardware_id:
            raise RuntimeError('Matching physical kernel checks are required')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(options.model, local_files_only=True)
    if not tokenizer.chat_template:
        raise RuntimeError('This checkpoint needs a chat template')
    control, link, eng = Jtag(run / 'chat'), None, None
    try:
        link = EthernetDataLink(control, Ethernet(options.address), run_control='ethernet')
        if link.words(CONTROL_BASE, 2) != [0x4f545055, (cfg.D << 16) | (cfg.MCOLS << 8) | cfg.LANES]:
            raise RuntimeError('Program the matching accelerator first')
        if (link.words(CONTROL_BASE + 0x44)[0] != round(config['core_mhz'] * 1000) or
            link.words(CONTROL_BASE + 0x9c)[0] != T.arch_hash(cfg)):
            raise RuntimeError('Programmed clock/configuration differs from the selected hardware')
        eng = engine(options.model, run,
                     lambda c, images: EthernetBackend(c, images, link, verify=False, upload=False),
                     target, manifest['capacity'], pipeline='thread', rows=manifest.get('rows', 1))
        loaded['pristine'] = False
        save(run / 'model_loaded.json', loaded)
        history = []
        print(f'Board chat ready; context {eng.cap} tokens, max reply {options.max_new}. '
              '/reset clears history; /quit exits. History is replayed each turn.', flush=True)
        while True:
            try:
                question = options.prompt if options.prompt is not None else input('\nYou> ')
            except EOFError:
                break
            if question.strip() in ('/quit', '/exit'):
                break
            if question.strip() == '/reset':
                history.clear()
                print('Conversation cleared.')
                continue
            if not question.strip():
                continue
            candidate = history + [{'role': 'user', 'content': question}]
            ids = prompt_ids(tokenizer, candidate)
            if len(ids) + options.max_new > eng.cap:
                message = (f'Context needs {len(ids)} prompt + {options.max_new} reply tokens; '
                           f'capacity is {eng.cap}. Use /reset, a shorter question, or a smaller --max-new.')
                if options.prompt is not None:
                    raise RuntimeError(message)
                print(message)
                continue
            print('Assistant> ', end='', flush=True)
            text = reply(eng, tokenizer, ids, options.max_new)
            history = candidate + [{'role': 'assistant', 'content': text}]
            if options.prompt is not None:
                break
    finally:
        if eng is not None:
            eng._drain()
        (link or control).close()


if __name__ == '__main__':
    main()
