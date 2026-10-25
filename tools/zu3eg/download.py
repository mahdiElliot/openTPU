"""Resume the official checkpoint with verified parallel HTTP byte ranges."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = 'Qwen/Qwen3.5-0.8B'
REVISION = '2fc06364715b967f1860aea9cf38778875588b17'


def main():
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    out = ROOT / 'build/zu3eg'
    out.mkdir(parents=True, exist_ok=True)
    metadata_file = out / 'weight_metadata.json'
    if not metadata_file.exists():
        from huggingface_hub import HfApi, get_hf_file_metadata, hf_hub_url, snapshot_download

        info = HfApi().model_info(REPO, revision=REVISION, files_metadata=True)
        names = [f.rfilename for f in info.siblings if f.rfilename.endswith('.safetensors')]
        assert len(names) == 1, 'This downloader expects the pinned single-file checkpoint'
        url = hf_hub_url(REPO, names[0], revision=REVISION)
        remote = get_hf_file_metadata(url)
        metadata_file.write_text(json.dumps({'name': names[0], 'size': remote.size,
                                             'sha256': remote.etag, 'url': url}, indent=2) + '\n')
        source = {'repo': REPO, 'revision': REVISION,
                  'files': [{'name': f.rfilename, 'size': f.size} for f in info.siblings]}
        (out / 'model_source.json').write_text(json.dumps(source, indent=2) + '\n')
        snapshot_download(REPO, revision=REVISION, local_dir=ROOT / 'models/Qwen3.5-0.8B',
                          allow_patterns=['*.json', '*.jinja', '*.txt', '*.model', 'LICENSE'],
                          max_workers=4)
    meta = json.loads(metadata_file.read_text())
    needed = ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt')
    if not all((ROOT / 'models/Qwen3.5-0.8B' / name).exists() for name in needed):
        from huggingface_hub import snapshot_download

        snapshot_download(REPO, revision=REVISION, local_dir=ROOT / 'models/Qwen3.5-0.8B',
                          allow_patterns=['*.json', '*.jinja', '*.txt', '*.model', 'LICENSE'],
                          max_workers=4)
    size, digest = int(meta["size"]), meta["sha256"].strip('"')
    assert re.fullmatch("[a-f0-9]{64}", digest), "Expected a SHA-256 from the official file metadata"
    model = ROOT / "models/Qwen3.5-0.8B"
    dest = model / meta["name"]
    partial = model / ".weights.parallel.partial"
    journal = ROOT / "build/zu3eg/download_ranges.json"
    workers = 8
    starts = [size * i // workers for i in range(workers)]
    ends = [size * (i + 1) // workers for i in range(workers)]
    if dest.exists():
        partial = dest
        done = [b - a for a, b in zip(starts, ends)]
    elif partial.exists() and journal.exists():
        saved = json.loads(journal.read_text())
        assert saved["sha256"] == digest and saved["size"] == size
        done = saved["done"]
    else:
        incomplete = [p for p in (model / ".cache/huggingface/download").glob("*.incomplete")
                      if digest in p.name]
        prefix = 0
        if incomplete:
            assert len(incomplete) == 1
            prefix = incomplete[0].stat().st_size
            assert prefix <= size
            incomplete[0].rename(partial)
        done = [max(0, min(prefix - a, b - a)) for a, b in zip(starts, ends)]
    fd = os.open(partial, os.O_RDWR | os.O_CREAT, 0o644)
    os.ftruncate(fd, size)
    lock = threading.Lock()

    def save():
        with lock:
            state = {"size": size, "sha256": digest, "done": done.copy()}
        journal.write_text(json.dumps(state, indent=2) + "\n")
        print(f"Checkpoint: {sum(state['done']) / 2**20:.1f} / {size / 2**20:.1f} MiB", flush=True)

    def transfer(i):
        for attempt in range(6):
            offset = starts[i] + done[i]
            if offset >= ends[i]:
                return
            request = urllib.request.Request(meta["url"], headers={
                "Range": f"bytes={offset}-{ends[i]-1}", "User-Agent": "openTPU-model-evaluation/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    expected = f"bytes {offset}-{ends[i]-1}/{size}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise RuntimeError("Server did not honor the requested byte range")
                    while offset < ends[i]:
                        data = response.read(min(256 << 10, ends[i] - offset))
                        if not data:
                            raise EOFError("Truncated range response")
                        assert os.pwrite(fd, data, offset) == len(data)
                        offset += len(data)
                        with lock:
                            done[i] += len(data)
                return
            except Exception as error:
                code = error.code if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                print(f"Range {i} attempt {attempt + 1}: {code}", flush=True)
                if attempt == 5:
                    raise RuntimeError(f"Range {i} failed: {code}") from None
                time.sleep(min(2 ** attempt, 16))
    try:
        save()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            remaining = {pool.submit(transfer, i) for i in range(workers)}
            while remaining:
                complete, remaining = concurrent.futures.wait(
                    remaining, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
                save()
                for future in complete:
                    future.result()
        os.fsync(fd)
    finally:
        save()
        os.close(fd)
    with partial.open("rb") as weights:
        actual = hashlib.file_digest(weights, "sha256").hexdigest()
    if actual != digest:
        raise RuntimeError(f"Checkpoint checksum mismatch: {actual}")
    if partial != dest:
        partial.rename(dest)
    report = {"name": meta["name"], "bytes": size, "sha256": actual, "verified": True}
    (ROOT / "build/zu3eg/checkpoint_integrity.json").write_text(json.dumps(report, indent=2) + "\n")
    print("CHECKPOINT_SHA256_VERIFIED", actual, flush=True)


if __name__ == "__main__":
    main()
