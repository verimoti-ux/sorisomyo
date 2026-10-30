#!/usr/bin/env python3
"""izotope_sim — iZotope Ozone/Neutron 스타일 마스터링 체인 시뮬레이터.

iZotope 실제 알고리즘이 아니라, 같은 모듈 구성(EQ · Dynamics · Exciter · Imager ·
Maximizer · De-esser)과 파라미터 개념을 numpy만으로 재현한 근사 구현이다.
Hermes 에이전트가 터미널에서 호출하고 JSON 결과를 읽도록 설계했다.

사용법:
  izotope_sim.py analyze IN [--json]
  izotope_sim.py process IN OUT (--chain CHAIN.json | --preset NAME) [--report R.json]
  izotope_sim.py assist IN --out CHAIN.json [--series electric-folklore|blues] [--target -14]
  izotope_sim.py match IN REF OUT
  izotope_sim.py presets
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

EPS = 1e-12


# ---------------------------------------------------------------- 입출력

def _next_pow2(n):
    return 1 << max(0, int(n - 1).bit_length())


def read_audio(path):
    """오디오를 (samples, channels) float64 배열과 샘플레이트로 읽는다."""
    if not os.path.exists(path):
        sys.exit(f"입력 파일이 없습니다: {path}")
    try:
        return _read_wav(path)
    except (wave.Error, EOFError, ValueError):
        pass
    if not shutil.which("ffmpeg"):
        sys.exit("PCM WAV가 아닌 파일은 ffmpeg가 필요합니다.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_wav = os.path.join(tmp, "in.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "pcm_s24le", tmp_wav],
                       check=True)
        return _read_wav(tmp_wav)


def _read_wav(path):
    with wave.open(path, "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    elif width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        data = v.astype(np.float64) / (1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float64) / (1 << 31)
    else:
        raise ValueError("지원하지 않는 WAV 비트 깊이")
    return data.reshape(-1, ch), sr


def write_audio(path, x, sr):
    """24비트 PCM으로 저장한다. .wav가 아니면 ffmpeg로 변환한다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if path.lower().endswith(".wav"):
        _write_wav(path, x, sr)
        return
    if not shutil.which("ffmpeg"):
        sys.exit("WAV 이외 형식으로 저장하려면 ffmpeg가 필요합니다.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_wav = os.path.join(tmp, "out.wav")
        _write_wav(tmp_wav, x, sr)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp_wav, path], check=True)


def _write_wav(path, x, sr):
    v = np.round(np.clip(x, -1.0, 1.0 - 1.0 / (1 << 23)) * (1 << 23)).astype(np.int32)
    b = np.empty((v.size, 3), np.uint8)
    flat = v.reshape(-1)
    b[:, 0] = flat & 0xFF
    b[:, 1] = (flat >> 8) & 0xFF
    b[:, 2] = (flat >> 16) & 0xFF
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(b.tobytes())


# ---------------------------------------------------------------- 필터 기본기

def fft_convolve(x, h, delay=0):
    """블록 FFT 컨볼루션. delay만큼 앞을 잘라 원래 길이로 돌려준다."""
    n, L = x.shape[0], len(h)
    block = 1 << 16
    nfft = _next_pow2(block + L - 1)
    H = np.fft.rfft(h, nfft)[:, None]
    out = np.zeros((n + L - 1, x.shape[1]))
    for start in range(0, n, block):
        seg = x[start:start + block]
        y = np.fft.irfft(np.fft.rfft(seg, nfft, axis=0) * H, nfft, axis=0)
        m = seg.shape[0] + L - 1
        out[start:start + m] += y[:m]
    return out[delay:delay + n]


def biquad(kind, f, sr, gain_db=0.0, q=0.7071):
    """RBJ Audio EQ Cookbook 계수 (b, a)."""
    f = min(max(f, 1.0), sr * 0.49)
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "bell":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "low_shelf":
        s = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw),
             A * ((A + 1) - (A - 1) * cw - s)]
        a = [(A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - s]
    elif kind == "high_shelf":
        s = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw),
             A * ((A + 1) + (A - 1) * cw - s)]
        a = [(A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - s]
    elif kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind in ("lowpass1", "highpass1"):
        K = math.tan(w0 / 2)
        a = [1.0, (K - 1) / (K + 1)]
        b = [K / (1 + K), K / (1 + K)] if kind == "lowpass1" else [1 / (1 + K), -1 / (1 + K)]
        return np.array(b), np.array(a)
    else:
        raise ValueError(f"알 수 없는 필터 종류: {kind}")
    return np.array(b) / a[0], np.array(a) / a[0]


def _response(b, a, w):
    z = np.exp(-1j * w)
    num = sum(bk * z ** k for k, bk in enumerate(b))
    den = sum(ak * z ** k for k, ak in enumerate(a))
    return num / den


def _cut_sections(kind, f, slope):
    """하이패스/로우패스 기울기(dB/oct)를 버터워스 캐스케이드로 만든다."""
    order = max(1, int(round(slope / 6)))
    secs = []
    if order % 2:
        secs.append((kind + "1", f, 0.0, 0.7071))
    for k in range(1, order // 2 + 1):
        q = 1 / (2 * math.cos((2 * k - 1) * math.pi / (2 * order)))
        secs.append((kind, f, 0.0, q))
    return secs


def _band_sections(band):
    kind = band.get("type", "bell")
    f = float(band["freq"])
    if kind in ("highpass", "lowpass"):
        return _cut_sections(kind, f, float(band.get("slope", 12)))
    default_q = 1.0 if kind == "bell" else 0.7071
    return [(kind, f, float(band.get("gain", 0.0)), float(band.get("q", default_q)))]


def _fir_len(sr, seconds):
    return _next_pow2(int(sr * seconds))


def eq_fir(bands, sr, phase="minimum"):
    """EQ 밴드 목록을 FIR로 만든다. 반환값은 (h, delay)."""
    L = _fir_len(sr, 0.34)
    w = 2 * np.pi * np.arange(L // 2 + 1) / L
    H = np.ones_like(w, dtype=complex)
    for band in bands:
        for kind, f, g, q in _band_sections(band):
            b, a = biquad(kind, f, sr, g, q)
            H *= _response(b, a, w)
    if phase == "linear":
        return _linear_phase_fir(np.abs(H), L), L // 2
    h = np.fft.irfft(H, L)
    fade = L // 8
    h[-fade:] *= np.linspace(1, 0, fade)
    return h, 0


def _periodic_blackman(L):
    n = np.arange(L)
    return 0.42 - 0.5 * np.cos(2 * np.pi * n / L) + 0.08 * np.cos(4 * np.pi * n / L)


def _linear_phase_fir(mag, L):
    return np.roll(np.fft.irfft(mag, L), L // 2) * _periodic_blackman(L)


def split_bands(x, sr, crossovers):
    """선형 위상 상보 크로스오버로 나눈다. 밴드를 모두 더하면 원신호가 된다."""
    xs = sorted(float(c) for c in crossovers)
    if not xs:
        return [x]
    L = _fir_len(sr, 0.17)
    f = np.arange(L // 2 + 1) * sr / L
    lows = [1 / (1 + (f / fc) ** 4) for fc in xs]
    masks, rest = [], np.ones_like(f)
    for lp in lows:
        masks.append(rest * lp)
        rest = rest * (1 - lp)
    masks.append(rest)
    return [fft_convolve(x, _linear_phase_fir(m, L), L // 2) for m in masks]


def to_ms(x):
    return (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2


def from_ms(m, s):
    return np.stack([m + s, m - s], axis=1)


# ---------------------------------------------------------------- 측정

K_SHELF = dict(type="high_shelf", freq=1681.974450955533, gain=3.999843853973347,
               q=0.7071752369554196)
K_HP = (("highpass", 38.13547087602444, 0.0, 0.5003270373238773),)


def _k_weight(x, sr):
    L = _fir_len(sr, 0.34)
    w = 2 * np.pi * np.arange(L // 2 + 1) / L
    H = np.ones_like(w, dtype=complex)
    for kind, f, g, q in _band_sections(K_SHELF) + list(K_HP):
        b, a = biquad(kind, f, sr, g, q)
        H *= _response(b, a, w)
    h = np.fft.irfft(H, L)
    h[-L // 8:] *= np.linspace(1, 0, L // 8)
    return fft_convolve(x, h)


def _block_power(y, sr, win_s, hop_s):
    """창별 채널 합산 평균 파워(BS.1770 z)."""
    win, hop = int(sr * win_s), int(sr * hop_s)
    if len(y) < win:
        return np.array([np.sum(np.mean(y ** 2, axis=0))])
    c = np.concatenate([np.zeros((1, y.shape[1])), np.cumsum(y ** 2, axis=0)])
    starts = np.arange(0, len(y) - win + 1, hop)
    return np.sum((c[starts + win] - c[starts]) / win, axis=1)


def _lufs(z):
    return -0.691 + 10 * np.log10(np.maximum(z, EPS))


def loudness(x, sr):
    """ITU-R BS.1770-4 통합 라우드니스, 숏텀 최대, LRA(EBU Tech 3342)."""
    y = _k_weight(x, sr)
    z = _block_power(y, sr, 0.4, 0.1)
    z = z[_lufs(z) > -70]
    if not len(z):
        integrated = -math.inf
    else:
        rel = _lufs(np.mean(z)) - 10
        zg = z[_lufs(z) > rel]
        integrated = float(_lufs(np.mean(zg))) if len(zg) else -math.inf
    st = _lufs(_block_power(y, sr, 3.0, 0.1))
    st_gated = st[st > -70]
    lra = 0.0
    if len(st_gated):
        rel = _lufs(np.mean(10 ** ((st_gated + 0.691) / 10))) - 20
        sg = st_gated[st_gated > rel]
        if len(sg) > 1:
            lra = float(np.percentile(sg, 95) - np.percentile(sg, 10))
    return {"integrated_lufs": integrated,
            "short_term_max_lufs": float(st.max()) if len(st) else -math.inf,
            "lra_lu": lra}


def true_peak_env(x, os_factor=4):
    """원 샘플마다 4배 오버샘플 구간의 최대 절댓값(채널 통합)."""
    n = len(x)
    chunk, pad = 1 << 15, 256
    env = np.zeros(n)
    for s in range(0, n, chunk):
        a, b = max(0, s - pad), min(n, s + chunk + pad)
        seg = x[a:b]
        m = b - a
        y = np.fft.irfft(np.fft.rfft(seg, axis=0), m * os_factor, axis=0) * os_factor
        y = np.abs(y).max(axis=1).reshape(m, os_factor).max(axis=1)
        e = min(n, s + chunk)
        env[s:e] = np.maximum(y[s - a:s - a + e - s], np.abs(x[s:e]).max(axis=1))
    return env


def _db(v):
    return float(20 * math.log10(max(v, EPS)))


BANDS = [("sub", 20, 60), ("low", 60, 250), ("low_mid", 250, 2000),
         ("high_mid", 2000, 6000), ("high", 6000, 20000)]
METALLIC = [("metallic_2k_4k5", 2000, 4500), ("metallic_8k_16k", 8000, 16000)]
OCTAVES = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def _avg_spectrum(x, sr, nfft=4096):
    mono = x.mean(axis=1)
    hop = nfft // 2
    frames = max(1, (len(mono) - nfft) // hop + 1)
    idx = np.linspace(0, frames - 1, min(frames, 1500)).astype(int) * hop
    win = np.hanning(nfft)
    seg = np.stack([np.pad(mono[i:i + nfft], (0, max(0, nfft - len(mono[i:i + nfft])))) for i in idx])
    p = np.mean(np.abs(np.fft.rfft(seg * win, axis=1)) ** 2, axis=0)
    return np.fft.rfftfreq(nfft, 1 / sr), p


def _band_db(f, p, lo, hi):
    sel = (f >= lo) & (f < hi)
    return 10 * math.log10(max(p[sel].sum(), EPS))


def analyze(x, sr):
    peak = float(np.abs(x).max())
    tp = float(true_peak_env(x).max())
    loud = loudness(x, sr)
    f, p = _avg_spectrum(x, sr)
    total = 10 * math.log10(max(p[(f >= 20) & (f < 20000)].sum(), EPS))
    rms = math.sqrt(float(np.mean(x ** 2)))
    res = {
        "sample_rate": sr, "channels": x.shape[1], "duration_s": round(len(x) / sr, 2),
        "sample_peak_dbfs": round(_db(peak), 2), "true_peak_dbtp": round(_db(tp), 2),
        "rms_dbfs": round(_db(rms), 2), "crest_db": round(_db(peak) - _db(rms), 2),
        "integrated_lufs": round(loud["integrated_lufs"], 2),
        "short_term_max_lufs": round(loud["short_term_max_lufs"], 2),
        "lra_lu": round(loud["lra_lu"], 2),
        "plr_db": round(_db(tp) - loud["integrated_lufs"], 2),
        "band_balance_db": {n: round(_band_db(f, p, lo, hi) - total, 2) for n, lo, hi in BANDS + METALLIC},
        "octave_db": {str(c): round(_band_db(f, p, c / 2 ** 0.5, c * 2 ** 0.5) - total, 2) for c in OCTAVES},
        "clipped_samples": int(np.sum(np.abs(x) >= 0.9999)),
    }
    if x.shape[1] == 2:
        m, s = to_ms(x)
        den = math.sqrt(float(np.sum(x[:, 0] ** 2) * np.sum(x[:, 1] ** 2))) + EPS
        res["stereo_correlation"] = round(float(np.sum(x[:, 0] * x[:, 1]) / den), 3)
        res["side_to_mid_db"] = round(_db(math.sqrt(np.mean(s ** 2))) - _db(math.sqrt(np.mean(m ** 2))), 2)
        lows = split_bands(x, sr, [120])[0]
        lm, ls = to_ms(lows)
        res["low_side_to_mid_db"] = round(_db(math.sqrt(np.mean(ls ** 2))) - _db(math.sqrt(np.mean(lm ** 2))), 2)
    return res


# ---------------------------------------------------------------- 모듈

def mod_eq(x, sr, p):
    """Ozone/Neutron EQ: bell, low/high_shelf, highpass/lowpass, 채널 stereo/mid/side."""
    bands = [b for b in p.get("bands", []) if not b.get("bypass")]
    phase = p.get("phase", "minimum")
    groups = {c: [b for b in bands if b.get("channel", "stereo") == c] for c in ("stereo", "mid", "side")}
    y = x
    if groups["stereo"]:
        h, d = eq_fir(groups["stereo"], sr, phase)
        y = fft_convolve(y, h, d)
    if (groups["mid"] or groups["side"]) and y.shape[1] == 2:
        m, s = to_ms(y)
        if groups["mid"]:
            h, d = eq_fir(groups["mid"], sr, phase)
            m = fft_convolve(m[:, None], h, d)[:, 0]
        if groups["side"]:
            h, d = eq_fir(groups["side"], sr, phase)
            s = fft_convolve(s[:, None], h, d)[:, 0]
        y = from_ms(m, s)
    elif groups["mid"] and y.shape[1] == 1:
        h, d = eq_fir(groups["mid"], sr, phase)
        y = fft_convolve(y, h, d)
    return y, {"bands": len(bands), "phase": phase}


def _gain_reduction(x, sr, b, hop=32):
    """링크드 스테레오 컴프레서 게인 커브(선형)와 최대 GR(dB)."""
    thr = float(b.get("threshold", -20))
    ratio = max(1.0, float(b.get("ratio", 2)))
    knee = max(0.0, float(b.get("knee", 6)))
    n = len(x)
    nb = -(-n // hop)
    padded = np.zeros((nb * hop, x.shape[1]))
    padded[:n] = x
    blk = padded.reshape(nb, hop, -1)
    if b.get("detection", "rms") == "peak":
        lvl = np.abs(blk).max(axis=(1, 2))
    else:
        lvl = np.sqrt(np.mean(blk ** 2, axis=(1, 2)))
    over = 20 * np.log10(np.maximum(lvl, EPS)) - thr
    slope = 1 / ratio - 1
    gr = np.where(over <= -knee / 2, 0.0, slope * over)
    if knee > 0:
        inknee = np.abs(over) < knee / 2
        gr = np.where(inknee, slope * (over + knee / 2) ** 2 / (2 * knee), gr)
    crate = sr / hop
    att = math.exp(-1 / max(EPS, float(b.get("attack", 20)) / 1000 * crate))
    rel = math.exp(-1 / max(EPS, float(b.get("release", 150)) / 1000 * crate))
    sm = np.empty(nb)
    state = 0.0
    for i, t in enumerate(gr.tolist()):
        c = att if t < state else rel
        state = c * state + (1 - c) * t
        sm[i] = state
    g = 10 ** ((sm + float(b.get("makeup", 0))) / 20)
    curve = np.interp(np.arange(n), np.arange(nb) * hop + hop / 2, g)
    return curve, float(-sm.min()) if nb else 0.0


def mod_dynamics(x, sr, p):
    """멀티밴드 컴프레서. crossovers가 N개면 bands는 N+1개."""
    xs = p.get("crossovers", [])
    bands = p.get("bands", [{}])
    if len(bands) != len(xs) + 1:
        raise ValueError("dynamics: bands 개수는 crossovers 개수 + 1이어야 합니다")
    parts = split_bands(x, sr, xs) if xs else [x]
    out, info = np.zeros_like(x), []
    for part, b in zip(parts, bands):
        if b.get("bypass") or float(b.get("ratio", 2)) <= 1:
            out += part
            info.append({"max_gr_db": 0.0})
            continue
        curve, gr = _gain_reduction(part, sr, b)
        mix = float(b.get("mix", 100)) / 100
        out += part * ((1 - mix) + mix * curve)[:, None]
        info.append({"max_gr_db": round(max(0.0, gr), 2)})
    mix = float(p.get("mix", 100)) / 100
    return (1 - mix) * x + mix * out, {"bands": info}


def mod_deesser(x, sr, p):
    """치찰음 대역만 압축하는 스플릿 디에서 (Nectar/RX De-ess 개념)."""
    lo, hi = float(p.get("low", 5000)), float(p.get("high", 10000))
    low, ess, high = split_bands(x, sr, [lo, hi])
    curve, gr = _gain_reduction(ess, sr, {
        "threshold": p.get("threshold", -30), "ratio": p.get("ratio", 4),
        "attack": p.get("attack", 2), "release": p.get("release", 60), "knee": 3, "detection": "peak"})
    return low + ess * curve[:, None] + high, {"band_hz": [lo, hi], "max_gr_db": round(max(0.0, gr), 2)}


def _saturate(v, mode):
    if mode == "tube":
        bias = 0.3
        return (np.tanh(v + bias) - math.tanh(bias)) / (1 - math.tanh(bias) ** 2)
    if mode == "warm":
        c = np.clip(v, -1, 1)
        return (c - c ** 3 / 3) * 1.5
    return np.tanh(v)  # tape


def mod_exciter(x, sr, p):
    """멀티밴드 하모닉 익사이터. 밴드별 mode(tape/tube/warm), drive(0-100), mix(0-100)."""
    xs = p.get("crossovers", [])
    bands = p.get("bands", [{}])
    if len(bands) != len(xs) + 1:
        raise ValueError("exciter: bands 개수는 crossovers 개수 + 1이어야 합니다")
    parts = split_bands(x, sr, xs) if xs else [x]
    out = np.zeros_like(x)
    for part, b in zip(parts, bands):
        mix = float(b.get("mix", 0)) / 100
        if b.get("bypass") or mix <= 0:
            out += part
            continue
        ref = float(np.percentile(np.abs(part), 99.5)) + EPS
        d = 1 + float(b.get("drive", 30)) / 100 * 9
        wet = _saturate(part / ref * d, b.get("mode", "tape")) / d * ref
        wet -= wet.mean(axis=0)
        out += (1 - mix) * part + mix * wet
    mix = float(p.get("mix", 100)) / 100
    return (1 - mix) * x + mix * out, {"bands": len(bands)}


def mod_imager(x, sr, p):
    """멀티밴드 스테레오 이미저. widths: -100(모노) ~ 0(원본) ~ +100(사이드 2배)."""
    if x.shape[1] != 2:
        return x, {"skipped": "mono"}
    xs = p.get("crossovers", [])
    widths = p.get("widths", [0])
    if len(widths) != len(xs) + 1:
        raise ValueError("imager: widths 개수는 crossovers 개수 + 1이어야 합니다")
    parts = split_bands(x, sr, xs) if xs else [x]
    out = np.zeros_like(x)
    for part, wdt in zip(parts, widths):
        m, s = to_ms(part)
        out += from_ms(m, s * (1 + max(-100.0, min(100.0, float(wdt))) / 100))
    return out, {"widths": widths}


CHARACTERS = {  # 릴리즈(ms), 룩어헤드(ms) — IRC 모드의 성격을 흉내낸 값
    "transparent": (300, 5.0), "balanced": (150, 3.0), "loud": (60, 1.5),
    "irc1": (200, 5.0), "irc2": (150, 3.0), "irc3": (100, 2.0), "irc4": (60, 1.5),
}


def _limit(x, sr, gain_db, ceiling_db, character, true_peak=True, hop=16):
    rel_ms, la_ms = CHARACTERS.get(character, CHARACTERS["balanced"])
    y = x * 10 ** (gain_db / 20)
    ceil = 10 ** (ceiling_db / 20)
    env = true_peak_env(y) if true_peak else np.abs(y).max(axis=1)
    req = np.minimum(1.0, ceil / (env + EPS))
    n = len(y)
    nb = -(-n // hop)
    padded = np.ones(nb * hop)
    padded[:n] = req
    g = padded.reshape(nb, hop).min(axis=1)
    la = max(1, int(math.ceil(la_ms / 1000 * sr / hop)))
    ext = np.concatenate([g, np.ones(la)])
    gmin = np.min(np.stack([ext[k:k + nb] for k in range(la)]), axis=0)
    r = math.exp(-1 / (rel_ms / 1000 * sr / hop))
    sm = np.empty(nb)
    state = 1.0
    for i, t in enumerate(gmin.tolist()):
        state = t if t < state else r * state + (1 - r) * t
        sm[i] = state
    c = np.concatenate([np.ones(la), sm])
    c = np.cumsum(c)
    avg = (c[la:] - c[:-la]) / la
    knots = np.minimum(np.concatenate([[avg[0]], avg[:-1]]), avg)
    knots = np.append(knots, avg[-1])
    curve = np.interp(np.arange(n), np.arange(nb + 1) * hop, knots)
    out = y * curve[:, None]
    tp = float(true_peak_env(out).max()) if true_peak else float(np.abs(out).max())
    if tp > ceil:
        out *= ceil / tp
    return out, _db(float(curve.min()))


def mod_maximizer(x, sr, p):
    """룩어헤드 트루피크 리미터. target_lufs를 주면 반복 측정으로 게인을 찾는다."""
    ceiling = float(p.get("ceiling", -1.0))
    character = p.get("character", "balanced")
    tp_mode = bool(p.get("true_peak", True))
    target = p.get("target_lufs")
    gain = float(p.get("gain", 0.0))
    if target is not None:
        cur = loudness(x, sr)["integrated_lufs"]
        if not math.isfinite(cur):
            return x, {"skipped": "silence"}
        gain = float(target) - cur
        for _ in range(6):
            y, gr = _limit(x, sr, gain, ceiling, character, tp_mode)
            got = loudness(y, sr)["integrated_lufs"]
            if abs(got - float(target)) < 0.1:
                break
            gain += float(target) - got
    else:
        y, gr = _limit(x, sr, gain, ceiling, character, tp_mode)
    info = {"input_gain_db": round(gain, 2), "max_gr_db": round(max(0.0, -gr), 2),
            "ceiling_db": ceiling, "character": character}
    if target is not None:
        got = loudness(y, sr)["integrated_lufs"]
        if abs(got - float(target)) >= 0.3:
            info["target_missed"] = f"목표 {target} LUFS에 못 미친 {got:.1f} LUFS. 리미터만으로는 더 올릴 수 없습니다."
    if -gr > 6:
        info["warning"] = "리미터 GR이 6dB를 넘습니다. 펌핑·왜곡 가능성이 커서 앞단 다이내믹스나 목표 음압을 재검토하세요."
    return y, info


def mod_gain(x, sr, p):
    return x * 10 ** (float(p.get("gain", 0)) / 20), {}


MODULES = {"eq": mod_eq, "dynamics": mod_dynamics, "deesser": mod_deesser,
           "exciter": mod_exciter, "imager": mod_imager, "maximizer": mod_maximizer,
           "gain": mod_gain}
ALIASES = {"ozone_eq": "eq", "neutron_eq": "eq", "equalizer": "eq", "compressor": "dynamics",
           "ozone_dynamics": "dynamics", "de-esser": "deesser", "de_ess": "deesser",
           "stereo_imager": "imager", "ozone_imager": "imager", "limiter": "maximizer",
           "ozone_maximizer": "maximizer", "ozone_exciter": "exciter", "saturation": "exciter"}


def run_chain(x, sr, chain):
    steps = []
    for i, m in enumerate(chain.get("modules", [])):
        name = ALIASES.get(m.get("module", ""), m.get("module", ""))
        if name not in MODULES:
            raise ValueError(f"알 수 없는 모듈: {m.get('module')} (가능: {', '.join(MODULES)})")
        if m.get("bypass"):
            steps.append({"index": i, "module": name, "bypass": True})
            continue
        x, info = MODULES[name](x, sr, m)
        steps.append({"index": i, "module": name, **info})
    return x, steps


# ---------------------------------------------------------------- 프리셋

HP = {"type": "highpass", "freq": 25, "slope": 24}

PRESETS = {
    "electric-folklore": {
        "description": "Electric Folklore — 신스·패드·사이드체인. 저역 모노, 샤머 대역 좁은 컷, -14 LUFS.",
        "modules": [
            {"module": "eq", "bands": [HP,
                                       {"type": "bell", "freq": 280, "gain": -1.0, "q": 1.0},
                                       {"type": "bell", "freq": 3500, "gain": -1.0, "q": 4.0},
                                       {"type": "bell", "freq": 11000, "gain": -1.0, "q": 5.0},
                                       {"type": "low_shelf", "freq": 120, "gain": 1.0, "channel": "side"}]},
            {"module": "dynamics", "crossovers": [120, 2000, 8000], "bands": [
                {"threshold": -22, "ratio": 2.0, "attack": 30, "release": 160},
                {"threshold": -24, "ratio": 1.6, "attack": 20, "release": 120},
                {"threshold": -28, "ratio": 1.8, "attack": 10, "release": 90},
                {"threshold": -32, "ratio": 2.0, "attack": 5, "release": 60}]},
            {"module": "imager", "crossovers": [120, 2000], "widths": [-100, 0, 10]},
            {"module": "maximizer", "target_lufs": -14, "ceiling": -1.0, "character": "balanced"},
        ],
    },
    "blues": {
        "description": "She Sings the Blues — 어쿠스틱 기타·하모니카·브러시 드럼. 따뜻한 튜브, 부드러운 글루, -14 LUFS.",
        "modules": [
            {"module": "eq", "bands": [{"type": "highpass", "freq": 30, "slope": 18},
                                       {"type": "bell", "freq": 320, "gain": -1.5, "q": 1.2},
                                       {"type": "bell", "freq": 2800, "gain": -1.0, "q": 3.0},
                                       {"type": "high_shelf", "freq": 16000, "gain": -1.0}]},
            {"module": "deesser", "low": 5500, "high": 9500, "threshold": -32, "ratio": 3},
            {"module": "dynamics", "bands": [{"threshold": -20, "ratio": 1.5, "attack": 30, "release": 200, "knee": 8}]},
            {"module": "exciter", "crossovers": [200, 2000], "bands": [
                {"mix": 0}, {"mode": "tube", "drive": 25, "mix": 15}, {"mix": 0}]},
            {"module": "imager", "crossovers": [150], "widths": [-60, 5]},
            {"module": "maximizer", "target_lufs": -14, "ceiling": -1.0, "character": "transparent"},
        ],
    },
    "suno-cleanup": {
        "description": "Suno 생성음원 정리 — 금속성 대역(2–4.5k, 8–16k) 좁은 컷과 디에싱. 음압은 건드리지 않음.",
        "modules": [
            {"module": "eq", "phase": "linear", "bands": [HP,
                                                          {"type": "bell", "freq": 3200, "gain": -1.5, "q": 5.0},
                                                          {"type": "bell", "freq": 9500, "gain": -1.5, "q": 5.0},
                                                          {"type": "bell", "freq": 13000, "gain": -1.0, "q": 4.0}]},
            {"module": "deesser", "low": 6000, "high": 11000, "threshold": -34, "ratio": 3},
        ],
    },
    "youtube-loudness": {
        "description": "유튜브 기준 음압만 맞춤 — -14 LUFS, -1 dBTP.",
        "modules": [{"module": "maximizer", "target_lufs": -14, "ceiling": -1.0, "character": "transparent"}],
    },
}


# ---------------------------------------------------------------- 어시스턴트

def assist(x, sr, series=None, target=-14.0):
    """Master Assistant 개념: 분석값으로 출발 체인을 만든다. 보수적으로(±2dB 이내)."""
    a = analyze(x, sr)
    notes = []
    oct_db = a["octave_db"]
    cs = [c for c in OCTAVES if 63 <= c <= 8000]
    lx = np.log2(cs)
    ly = np.array([oct_db[str(c)] for c in cs])
    slope, icpt = np.polyfit(lx, ly, 1)
    bands = [dict(HP)]
    devs = [(c, v - (slope * math.log2(c) + icpt)) for c, v in zip(cs, ly)]
    devs = sorted((d for d in devs if abs(d[1]) >= 2.0), key=lambda d: -abs(d[1]))[:4]
    for c, dev in sorted(devs):
        # 컷은 편차의 절반(최대 -2dB), 부스트는 더 보수적으로(최대 +1dB)
        gain = max(-2.0, min(1.0, -dev / 2))
        metallic = any(lo <= c <= hi for _, lo, hi in METALLIC)
        if gain > 0 and metallic:
            notes.append(f"{c:g}Hz 대역이 {dev:+.1f}dB 부족하지만 금속성 대역이라 부스트하지 않음")
            continue
        bands.append({"type": "bell", "freq": c, "gain": round(gain, 1), "q": 1.4})
        notes.append(f"{c:g}Hz 옥타브 {dev:+.1f}dB 편차 → {gain:+.1f}dB 벨")
    if slope < -6.0:
        notes.append(f"전체 기울기 {slope:.1f}dB/oct로 어두운 편. 고역 부스트 대신 저중역 컷을 검토")
        bands.append({"type": "bell", "freq": 300, "gain": -1.0, "q": 0.9})
    elif slope > -2.5:
        notes.append(f"전체 기울기 {slope:.1f}dB/oct로 밝은 편 → 8kHz 하이쉘프 -1dB")
        bands.append({"type": "high_shelf", "freq": 8000, "gain": -1.0})
    modules = [{"module": "eq", "bands": bands}]
    if series == "blues" or a["band_balance_db"]["metallic_8k_16k"] > -14:
        modules.append({"module": "deesser", "low": 5500, "high": 10000, "threshold": -32, "ratio": 3})
    plr = a["plr_db"]
    if plr > 16:
        thr = round(a["rms_dbfs"] + 4, 1)
        modules.append({"module": "dynamics", "bands": [
            {"threshold": thr, "ratio": 1.5 if series == "blues" else 2.0, "attack": 30, "release": 180, "knee": 8}]})
        notes.append(f"PLR {plr:.1f}dB로 다이내믹이 넓음 → 글루 컴프(threshold {thr}dB)")
    if a.get("low_side_to_mid_db", -99) > -15:
        modules.append({"module": "imager", "crossovers": [120], "widths": [-100, 0]})
        notes.append(f"120Hz 이하 사이드 비율 {a['low_side_to_mid_db']}dB → 저역 모노화")
    if series == "blues":
        modules.append({"module": "exciter", "crossovers": [200, 2000], "bands": [
            {"mix": 0}, {"mode": "tube", "drive": 20, "mix": 12}, {"mix": 0}]})
    modules.append({"module": "maximizer", "target_lufs": target, "ceiling": -1.0,
                    "character": "transparent" if series == "blues" else "balanced"})
    return {"description": f"assist 생성 체인 (series={series or 'auto'}, target={target})",
            "modules": modules, "notes": notes, "analysis": a}


# ---------------------------------------------------------------- CLI

def _load_chain(args):
    if args.preset:
        if args.preset not in PRESETS:
            sys.exit(f"알 수 없는 프리셋: {args.preset} (가능: {', '.join(PRESETS)})")
        return PRESETS[args.preset]
    with open(args.chain, encoding="utf-8") as f:
        return json.load(f)


def _delta(before, after):
    keys = ["integrated_lufs", "true_peak_dbtp", "lra_lu", "plr_db", "crest_db"]
    d = {k: round(after[k] - before[k], 2) for k in keys}
    d["band_balance_db"] = {k: round(after["band_balance_db"][k] - before["band_balance_db"][k], 2)
                            for k in before["band_balance_db"]}
    return d


def _dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=lambda v: None))


def main(argv=None):
    ap = argparse.ArgumentParser(description="iZotope Ozone/Neutron 스타일 체인 시뮬레이터")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("analyze")
    s.add_argument("input")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("process")
    s.add_argument("input")
    s.add_argument("output")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--chain")
    g.add_argument("--preset")
    s.add_argument("--report")
    s = sub.add_parser("assist")
    s.add_argument("input")
    s.add_argument("--out", required=True)
    s.add_argument("--series", choices=["electric-folklore", "blues"])
    s.add_argument("--target", type=float, default=-14.0)
    s = sub.add_parser("match")
    s.add_argument("input")
    s.add_argument("reference")
    s.add_argument("output")
    sub.add_parser("presets")
    args = ap.parse_args(argv)

    if args.cmd == "presets":
        _dump({k: v["description"] for k, v in PRESETS.items()})
    elif args.cmd == "analyze":
        x, sr = read_audio(args.input)
        res = analyze(x, sr)
        if args.json:
            _dump(res)
        else:
            for k, v in res.items():
                print(f"{k}: {v}")
    elif args.cmd == "process":
        chain = _load_chain(args)
        x, sr = read_audio(args.input)
        before = analyze(x, sr)
        y, steps = run_chain(x, sr, chain)
        write_audio(args.output, y, sr)
        after = analyze(y, sr)
        report = {"input": args.input, "output": args.output, "steps": steps,
                  "before": before, "after": after, "delta": _delta(before, after)}
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        _dump({"output": args.output, "steps": steps, "after": {k: after[k] for k in (
            "integrated_lufs", "true_peak_dbtp", "lra_lu", "plr_db")}, "delta": report["delta"]})
    elif args.cmd == "assist":
        x, sr = read_audio(args.input)
        chain = assist(x, sr, args.series, args.target)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({k: chain[k] for k in ("description", "modules", "notes")}, f, ensure_ascii=False, indent=2)
        _dump({"chain": args.out, "notes": chain["notes"], "modules": [m["module"] for m in chain["modules"]]})
    elif args.cmd == "match":
        x, sr = read_audio(args.input)
        r, rsr = read_audio(args.reference)
        lx, lr = loudness(x, sr)["integrated_lufs"], loudness(r, rsr)["integrated_lufs"]
        y = x * 10 ** ((lr - lx) / 20)
        tp = _db(float(true_peak_env(y).max()))
        write_audio(args.output, y, sr)
        res = {"output": args.output, "gain_db": round(lr - lx, 2), "lufs": round(lr, 2), "true_peak_dbtp": round(tp, 2)}
        if tp > 0:
            res["warning"] = "트루피크가 0dBTP를 넘어 저장 시 클리핑됩니다. A/B용으로는 기준 파일을 낮춰 맞추세요."
        _dump(res)


if __name__ == "__main__":
    main()
