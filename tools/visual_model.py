"""Visual gate: a small network that learns from our own slide frames what a clean frame looks like, and flags a frame with a defect.

It is self-trained. Every clean frame of the deck is a positive example. The defects are made on the fly: a piece cut and pasted
(doubled text), a second slide showing through, a line across text, stray marks, blur, a shifted frame, an erased block. Real
defects are never used for training. They only test the model: the two faults of 5 November (lit panels of other slides showing
through, the row of progress dots running into the caption) are captured from test decks and scored by a model that has never
seen those frames.

Process note, so that nobody reads more into a result than it holds: the generators "bleed" and "marks" were written after the two
faults were known, to be realistic. So the test on the real faults checks that the made defects transfer to real frames. It does
not show that the model finds a fault nobody thought of. The red-team check in the verification does that.

The network is fully convolutional. It trains on full-resolution crops of 640 x 384 pixels, so thin lines stay visible, and it
scores a whole 1440 x 900 frame by its strongest spot (global max). About a third of the crops include the bottom band of the
frame (counter, caption, dots). The batches are made by a few threads in the training process (no worker processes: on Windows
they filled the paging file).

    py -3 -I tools/visual_model.py train --clean DIR --real bleed=DIR dots=DIR [--holdout-slides real] [--out models/visual_gate.pt] [--epochs 60] [--seed 7]
    py -3 -I tools/visual_model.py evaluate --clean DIR --real bleed=DIR dots=DIR [--model models/visual_gate.pt] [--json OUT] [--update-model]
    py -3 -I tools/visual_model.py score DIR [--model models/visual_gate.pt] [--log]
    py -3 -I tools/visual_model.py --selftest

DIR holds desktop PNG frames named d-<key>-<n>.png, as steps.mjs writes them (1440 x 900). The model decides nothing alone: it is
advisory. A flagged frame gets a gate row (gate visual-gate) with the score as prediction; the owner's verdict is added later with
gate.py label. The version sits in the header of the weights file (and the metrics file), not in the file name (policy section 6).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

MODEL_VERSION = 2
FW, FH = 1440, 900                   # a whole frame
CW, CH = 640, 384                    # a training crop
NAME = re.compile(r"^d-(?P<key>[A-Za-z0-9]+)-(?P<n>\d+)\.png$")      # desktop frames only: the phone frames (m-*) have another size
HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE.parent / "models" / "visual_gate.pt"
LINE_COLOURS = [(236, 236, 234), (127, 178, 170), (125, 147, 196), (141, 140, 136)]      # text white, teal, blue, grey of the palette


# ---------------------------------------------------------------- data
def load_frame(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if im.size != (FW, FH):
        im = im.resize((FW, FH), Image.Resampling.BOX)
    return np.asarray(im, dtype=np.uint8)


def list_frames(folder: Path) -> list[str]:
    names = sorted(p.name for p in Path(folder).glob("*.png") if NAME.match(p.name))
    if not names:
        raise SystemExit(f"no frames named d-<key>-<n>.png in {folder}")
    return names


def load_dir(folder: Path) -> tuple[list[str], np.ndarray]:
    names = list_frames(folder)
    out = np.empty((len(names), FH, FW, 3), dtype=np.uint8)          # filled one by one: no second copy of the whole set
    for i, n in enumerate(names):
        out[i] = load_frame(Path(folder) / n)
    return names, out


def key_of(name: str) -> str:
    m = NAME.match(name)
    return m.group("key") if m else name


# ---------------------------------------------------------------- defects made on the fly (every function: img, pool, rng; any size)
def _rect(rng: np.random.Generator, h: int, w: int, lo: float, hi: float) -> tuple[int, int, int, int]:
    rw, rh = max(4, int(rng.uniform(lo, hi) * w)), max(4, int(rng.uniform(lo, hi) * h))
    return int(rng.integers(0, w - rw + 1)), int(rng.integers(0, h - rh + 1)), rw, rh


def cut_paste(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.copy()
    src = img if rng.random() < 0.5 else pool[int(rng.integers(len(pool)))]
    x, y, rw, rh = _rect(rng, h, w, 0.04, 0.35)
    patch = src[y:y + rh, x:x + rw]
    if rng.random() < 0.4:           # doubled text: pasted close to where it was cut
        nx = int(np.clip(x + rng.integers(-24, 25), 0, w - rw))
        ny = int(np.clip(y + rng.integers(-12, 13), 0, h - rh))
    else:
        nx, ny = int(rng.integers(0, w - rw + 1)), int(rng.integers(0, h - rh + 1))
    out[ny:ny + rh, nx:nx + rw] = patch
    return out


def bleed(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Another slide shows through: it lightens the frame (all of it or a region), blends with it, or sits on it as an opaque panel at the same place."""
    h, w = img.shape[:2]
    other = pool[int(rng.integers(len(pool)))]
    base = img.astype(np.float32)
    oth = other.astype(np.float32)
    r = rng.random()
    if r < 0.4:
        mix = np.maximum(base, oth * rng.uniform(0.25, 1.0))
    elif r < 0.6:
        b = rng.uniform(0.2, 0.6)
        mix = base * (1 - b) + oth * b
    else:
        x, y, rw, rh = _rect(rng, h, w, 0.2, 0.6)
        out = img.copy()
        out[y:y + rh, x:x + rw] = other[y:y + rh, x:x + rw]
        return out
    if rng.random() < 0.5:
        x, y, rw, rh = _rect(rng, h, w, 0.25, 0.8)
        out = base.copy()
        out[y:y + rh, x:x + rw] = mix[y:y + rh, x:x + rw]
    else:
        out = mix
    return np.clip(out, 0, 255).astype(np.uint8)


def lines(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.astype(np.float32)
    for _ in range(int(rng.integers(1, 4))):
        horiz = rng.random() < 0.6
        length = max(8, int(rng.uniform(0.05, 0.5) * (w if horiz else h)))
        t = int(rng.integers(1, 5))
        col = np.array(LINE_COLOURS[int(rng.integers(len(LINE_COLOURS)))], dtype=np.float32)
        a = rng.uniform(0.3, 1.0)
        if horiz:
            x, y = int(rng.integers(0, w - length)), int(rng.integers(0, h - t))
            region = out[y:y + t, x:x + length]
        else:
            x, y = int(rng.integers(0, w - t)), int(rng.integers(0, h - length))
            region = out[y:y + length, x:x + t]
        region[...] = region * (1 - a) + col * a
    return np.clip(out, 0, 255).astype(np.uint8)


def marks(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Stray marks: a few small squares, alone or in a row, in palette colours, drawn over the content."""
    h, w = img.shape[:2]
    out = img.copy()
    n = int(rng.integers(1, 5)) if rng.random() < 0.5 else int(rng.integers(5, 25))
    sz = int(rng.integers(3, 13))
    gap = int(rng.integers(1, 6))
    col = LINE_COLOURS[int(rng.integers(len(LINE_COLOURS)))]
    horiz = rng.random() < 0.8
    span = n * (sz + gap)
    x0 = int(rng.integers(0, max(1, w - (span if horiz else sz))))
    y0 = int(rng.integers(0, max(1, h - (sz if horiz else span))))
    for i in range(n):
        x, y = (x0 + i * (sz + gap), y0) if horiz else (x0, y0 + i * (sz + gap))
        out[y:y + sz, x:x + sz] = col
    return out


def blur(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    from scipy.ndimage import gaussian_filter
    h, w = img.shape[:2]
    sigma = float(rng.uniform(0.8, 3.0))
    if rng.random() < 0.4:
        return gaussian_filter(img, sigma=(sigma, sigma, 0))
    out = img.copy()
    x, y, rw, rh = _rect(rng, h, w, 0.15, 0.6)
    out[y:y + rh, x:x + rw] = gaussian_filter(img[y:y + rh, x:x + rw], sigma=(sigma, sigma, 0))
    return out


def shift(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    dx, dy = int(rng.uniform(-0.1, 0.1) * w), int(rng.uniform(-0.1, 0.1) * h)
    out = np.zeros_like(img)
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    out[yd, xd] = img[ys, xs]
    return out


def erase(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.copy()
    x, y, rw, rh = _rect(rng, h, w, 0.05, 0.3)
    out[y:y + rh, x:x + rw] = (11, 11, 13)
    return out


KINDS = {"cutpaste": cut_paste, "bleed": bleed, "line": lines, "marks": marks, "blur": blur, "shift": shift, "erase": erase}
WEIGHTS = {"cutpaste": 0.20, "bleed": 0.25, "line": 0.15, "marks": 0.18, "blur": 0.08, "shift": 0.07, "erase": 0.07}


def corrupt(img: np.ndarray, pool: np.ndarray, rng: np.random.Generator, kind: str | None = None) -> tuple[np.ndarray, str]:
    kind = kind or str(rng.choice(list(WEIGHTS), p=list(WEIGHTS.values())))
    return KINDS[kind](img, pool, rng), kind


# ---------------------------------------------------------------- the network
def build_net():
    import torch
    import torch.nn as nn

    def blk(i: int, o: int, stride: int = 2, dil: int = 1):
        return nn.Sequential(nn.Conv2d(i, o, 3, stride, dil, dilation=dil, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.f = nn.Sequential(blk(3, 24), blk(24, 48), blk(48, 64, 1), blk(64, 96), blk(96, 128), blk(128, 128, 1, 2), blk(128, 128, 1, 4))
            self.head = nn.Linear(256, 1)

        def forward(self, x):
            z = self.f(x)
            return self.head(torch.cat([z.amax((2, 3)), z.mean((2, 3))], 1)).squeeze(1)

    return Net()


def norm_u8(x, jitter=None):
    """Bytes (N, H, W, 3) on the device to the network input (N, 3, H, W). jitter: one brightness factor per sample."""
    v = x.permute(0, 3, 1, 2).float() / 255.0
    if jitter is not None:
        v = (v * jitter.view(-1, 1, 1, 1)).clamp_(0, 1)
    return ((v - 0.1) / 0.2).contiguous()


def to_tensor(batch, device):
    import torch
    return norm_u8(torch.as_tensor(np.ascontiguousarray(batch)).to(device))


def predict(model, frames: np.ndarray, device, bs: int = 4) -> np.ndarray:
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(frames), bs):
            out.append(torch.sigmoid(model(to_tensor(frames[i:i + bs], device))).cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


# ---------------------------------------------------------------- training data: random crops of clean frames, defects made on the fly
def make_batch(frames: np.ndarray, idx: np.ndarray, seed: int, b: int, bs: int) -> tuple[np.ndarray, np.ndarray]:
    """One batch: bs random crops of the training frames. Every second crop gets a made defect (label 1). A pure function of (seed, b)."""
    xs = np.empty((bs, CH, CW, 3), dtype=np.uint8)
    ys = np.zeros(bs, dtype=np.float32)
    for j in range(bs):
        rng = np.random.default_rng([seed, b, j])
        k = int(idx[rng.integers(len(idx))])
        x = int(rng.integers(0, FW - CW + 1))
        y = FH - CH - int(rng.integers(0, 60)) if rng.random() < 0.35 else int(rng.integers(0, FH - CH + 1))
        img = frames[k, y:y + CH, x:x + CW]
        if j % 2:
            pool = frames[idx[rng.integers(len(idx), size=6)], y:y + CH, x:x + CW]      # other training slides, same place
            img, _ = corrupt(img, pool, rng)
            ys[j] = 1.0
        xs[j] = img
    return xs, ys


def split_slides(keys: list[str], seed: int, n_hold: int) -> list[str]:
    uniq = sorted(set(keys))
    return sorted(random.Random(seed).sample(uniq, min(n_hold, max(1, len(uniq) // 5))))


def auc(neg: np.ndarray, pos: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(np.r_[np.zeros(len(neg)), np.ones(len(pos))], np.r_[neg, pos]))


def evaluate(model, device, names: list[str], frames: np.ndarray, hold: list[str], real: dict[str, Path], seed: int) -> dict:
    """Every test of a trained model. The threshold is the 95th percentile of the score of the held-out CLEAN frames. Then: made defects on
    whole held-out frames, and each real defect frame paired with its clean twin (the clean frame with the same file name)."""
    is_train = np.array([key_of(n) not in hold for n in names])
    val_f = frames[~is_train]
    vrng = np.random.default_rng(seed + 1)
    p_all = predict(model, frames, device)                          # score of every clean frame, training slides too
    p_clean = p_all[~is_train]
    thr = float(np.percentile(p_clean, 95))
    metrics: dict = {"version": MODEL_VERSION, "date": time.strftime("%Y-%m-%d"), "held_out_slides": hold, "n_train": int(is_train.sum()), "n_val": int(len(val_f)),
                     "threshold": thr, "false_alarm_val": float((p_clean > thr).mean()), "false_alarm_train": float((p_all[is_train] > thr).mean()),
                     "crop": [CW, CH], "seed": seed}
    synth = {}
    for kind in KINDS:
        fr = np.stack([corrupt(f, val_f[vrng.choice(len(val_f), min(6, len(val_f)), replace=False)], vrng, kind)[0] for f in val_f for _ in range(2)])
        p = predict(model, fr, device)
        synth[kind] = {"auc": auc(p_clean, p), "detected": float((p > thr).mean())}
    metrics["synthetic"] = synth
    twin = {nm: i for i, nm in enumerate(names)}                     # the clean twin of a real-defect frame has the same file name
    real_m = {}
    for name, folder in real.items():
        rnames, rfr = load_dir(folder)
        p = predict(model, rfr, device)
        del rfr

        def paired(sel: list[tuple[int, int]]) -> dict | None:
            if not sel:
                return None
            d = np.array([p[ri] - p_all[ci] for ri, ci in sel])
            return {"n": len(sel), "win": float((d > 0).mean()), "median_diff": float(np.median(d)), "detected": float(np.mean([p[ri] > thr for ri, _ in sel])),
                    "clean_twin_flagged": float(np.mean([p_all[ci] > thr for _, ci in sel]))}

        pairs = [(ri, twin[nm]) for ri, nm in enumerate(rnames) if nm in twin]
        real_m[name] = {"n": int(len(p)), "auc_vs_val": auc(p_clean, p), "detected": float((p > thr).mean()), "median_score": float(np.median(p)),
                        "paired_held_out_slides": paired([t for t in pairs if not is_train[t[1]]]),
                        "paired_training_slides": paired([t for t in pairs if is_train[t[1]]])}
    metrics["real"] = real_m
    return metrics


def evaluate_saved(clean_dir: Path, real: dict[str, Path], model_path: Path, json_path: Path | None, update: bool, device_name: str) -> dict:
    """Run every test again on a saved model. The held-out slides and the seed come from the model file."""
    import torch
    device = torch.device(device_name if device_name != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    ck = torch.load(model_path, map_location=device, weights_only=False)
    model = build_net().to(device)
    model.load_state_dict(ck["state"])
    names, frames = load_dir(clean_dir)
    metrics = evaluate(model, device, names, frames, ck["metrics"]["held_out_slides"], real, ck["seed"])
    metrics["steps"] = ck["steps"] * ck["epochs"]
    text = json.dumps(metrics, indent=1)
    if json_path:
        json_path.write_text(text, encoding="utf8")
    if update:
        ck["threshold"], ck["metrics"] = metrics["threshold"], metrics
        torch.save(ck, model_path)
        model_path.with_suffix(".json").write_text(text, encoding="utf8")
    print(text)
    return metrics


def train(clean_dir: Path, real: dict[str, Path], out: Path, epochs: int, seed: int, n_hold: int, device_name: str, workers: int, steps: int, hold_fixed: list[str] | None = None) -> dict:
    import torch
    import torch.nn.functional as F
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    device = torch.device(device_name if device_name != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    names, frames = load_dir(clean_dir)
    keys = [key_of(n) for n in names]
    if hold_fixed:
        missing = [k for k in hold_fixed if k not in keys]
        if missing:
            raise SystemExit(f"held-out slides not found among the clean frames: {missing}")
        hold = sorted(set(hold_fixed))
    else:
        hold = split_slides(keys, seed, n_hold)
    is_train = np.array([k not in hold for k in keys])
    tr_idx = np.flatnonzero(is_train)
    val_f = frames[~is_train]
    print(f"frames {len(frames)}: train {len(tr_idx)} ({len(set(np.array(keys)[is_train]))} slides), held out {len(val_f)} ({len(hold)} slides: {', '.join(hold)}); device {device}", flush=True)
    bs, total = 32, epochs * steps
    model = build_net().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=total)
    gen = torch.Generator(device=device)
    gen.manual_seed(seed)
    t0, tot, n = time.time(), 0.0, 0
    nw = max(1, workers)
    with ThreadPoolExecutor(max_workers=nw) as ex:                  # numpy and scipy release the GIL in their big operations
        window = deque(ex.submit(make_batch, frames, tr_idx, seed, b, bs) for b in range(min(total, 3 * nw)))
        nxt = len(window)
        for step in range(total):
            xb, yb = window.popleft().result()
            if nxt < total:
                window.append(ex.submit(make_batch, frames, tr_idx, seed, nxt, bs))
                nxt += 1
            model.train()
            jit = 1.0 + (torch.rand(bs, device=device, generator=gen) - 0.5) * 0.06
            x = norm_u8(torch.from_numpy(xb).to(device), jit)
            loss = F.binary_cross_entropy_with_logits(model(x), torch.from_numpy(yb).to(device))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach())
            n += 1
            if step == 19:
                print(f"first 20 steps {time.time() - t0:.1f} s; {total} steps planned", flush=True)
            if (step + 1) % (steps * 2) == 0:
                print(f"epoch {(step + 1) // steps}/{epochs} loss {tot / n:.4f} ({time.time() - t0:.0f} s)", flush=True)
                tot, n = 0.0, 0
    metrics = evaluate(model, device, names, frames, hold, real, seed)
    metrics["steps"] = total
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state": model.state_dict(), "threshold": metrics["threshold"], "metrics": metrics, "version": MODEL_VERSION, "seed": seed, "epochs": epochs, "steps": steps,
                "trained": time.strftime("%Y-%m-%dT%H:%M:%S")}, out)
    out.with_suffix(".json").write_text(json.dumps(metrics, indent=1), encoding="utf8")
    print(json.dumps(metrics, indent=1))
    return metrics


def score(folder: Path, model_path: Path, log: bool, device_name: str) -> int:
    import torch
    device = torch.device(device_name if device_name != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    ck = torch.load(model_path, map_location=device, weights_only=False)
    model = build_net().to(device)
    model.load_state_dict(ck["state"])
    names = list_frames(folder)
    p = np.concatenate([predict(model, np.stack([load_frame(Path(folder) / n) for n in names[i:i + 8]]), device) for i in range(0, len(names), 8)])      # eight frames at a time: a large deck must not fill the memory
    thr = ck["threshold"]
    flagged = [(n, float(s)) for n, s in zip(names, p) if s > thr]
    for n, s in sorted(flagged, key=lambda t: -t[1])[:40]:
        print(f"FLAG {s:.3f} {n}")
    print(f"{len(names)} frames, {len(flagged)} flagged (threshold {thr:.3f}; about 5 percent of clean frames are flagged by design)")
    if log:
        sys.path.insert(0, str(HERE))
        import gate
        for n, s in zip(names, p):
            gate.record("visual-gate", n, "warn" if s > thr else "pass", by="visual_model", prediction=round(float(s), 4), features={"thr": round(float(thr), 4)})
    return 0


def selftest() -> None:
    rng = np.random.default_rng(1)
    base = np.full((CH, CW, 3), (11, 11, 13), dtype=np.uint8)
    base[60:120, 40:300] = (60, 70, 90)
    pool = np.stack([base, np.roll(base, 40, axis=1)])
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9 and set(WEIGHTS) == set(KINDS)
    for kind in KINDS:
        out, k = corrupt(base, pool, rng, kind)
        assert out.shape == base.shape and out.dtype == np.uint8 and k == kind, kind
    assert not np.array_equal(corrupt(base, pool, rng, "marks")[0], base)
    fr = np.zeros((3, FH, FW, 3), dtype=np.uint8)
    fr[:, 100:300, 100:900] = (60, 70, 90)
    idx = np.array([0, 1, 2])
    xa, ya = make_batch(fr, idx, 1, 0, 4)
    xb, yb = make_batch(fr, idx, 1, 0, 4)
    assert xa.shape == (4, CH, CW, 3) and list(ya) == [0, 1, 0, 1] and np.array_equal(xa, xb), "make_batch must be a pure function of (seed, b)"
    import torch
    net = build_net()
    x = to_tensor(np.stack([base, base]), torch.device("cpu"))
    assert net(x).shape == (2,)
    full = to_tensor(np.zeros((1, FH, FW, 3), dtype=np.uint8), torch.device("cpu"))
    assert net(full).shape == (1,)                  # fully convolutional: a whole frame is scored too
    assert abs(auc(np.array([0.1, 0.2]), np.array([0.8, 0.9])) - 1.0) < 1e-9
    names = [f"d-{k}-{i}.png" for k in "abc" for i in (1, 2)]       # evaluate() on six small fake frames of three slides, slide c held out
    small = np.stack([np.roll(np.full((160, 256, 3), 20 + 10 * j, dtype=np.uint8), 7 * j, axis=1) for j in range(6)])
    m = evaluate(build_net(), torch.device("cpu"), names, small, ["c"], {}, 1)
    assert m["n_val"] == 2 and m["n_train"] == 4 and set(m["synthetic"]) == set(KINDS) and m["real"] == {}, m
    print("visual_model selftest: ok")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("train")
    t.add_argument("--clean", required=True, type=Path)
    t.add_argument("--real", nargs="*", default=[], help="name=DIR of frames with a real defect, used only to test")
    t.add_argument("--out", type=Path, default=DEFAULT_MODEL)
    t.add_argument("--epochs", type=int, default=60)
    t.add_argument("--steps", type=int, default=100, help="steps per epoch")
    t.add_argument("--seed", type=int, default=7)
    t.add_argument("--holdout", type=int, default=7, help="number of slides held out of training, drawn at random")
    t.add_argument("--holdout-slides", default="", help="comma list of slide keys to hold out, or 'real' for every slide that has frames in a --real set (then --holdout is ignored)")
    t.add_argument("--workers", type=int, default=4, help="threads that make the batches")
    t.add_argument("--device", default="auto")
    s = sub.add_parser("score")
    s.add_argument("folder", type=Path)
    s.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    s.add_argument("--log", action="store_true")
    s.add_argument("--device", default="auto")
    v = sub.add_parser("evaluate", help="run every test again on a saved model; its held-out slides and seed come from the model file")
    v.add_argument("--clean", required=True, type=Path)
    v.add_argument("--real", nargs="*", default=[], help="name=DIR of frames with a real defect")
    v.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    v.add_argument("--json", type=Path, default=None, help="also write the metrics here")
    v.add_argument("--update-model", action="store_true", help="write the new metrics into the model file and the .json beside it")
    v.add_argument("--device", default="auto")
    ns = ap.parse_args(argv)
    if ns.selftest:
        selftest()
        return 0
    if ns.cmd == "train":
        real = {kv.split("=", 1)[0]: Path(kv.split("=", 1)[1]) for kv in ns.real}
        if ns.holdout_slides == "real":                  # every slide that has frames in a real-defect set stays out of training
            fixed = sorted({key_of(n) for folder in real.values() for n in list_frames(folder)})
        else:
            fixed = [k for k in ns.holdout_slides.split(",") if k]
        train(ns.clean, real, ns.out, ns.epochs, ns.seed, ns.holdout, ns.device, ns.workers, ns.steps, fixed)
        return 0
    if ns.cmd == "score":
        return score(ns.folder, ns.model, ns.log, ns.device)
    if ns.cmd == "evaluate":
        real = {kv.split("=", 1)[0]: Path(kv.split("=", 1)[1]) for kv in ns.real}
        evaluate_saved(ns.clean, real, ns.model, ns.json, ns.update_model, ns.device)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
