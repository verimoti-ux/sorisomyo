#!/usr/bin/env python3
"""izotope_sim — iZotope Ozone/Neutron 스타일 마스터링 체인 시뮬레이터.

iZotope 실제 알고리즘이 아니라, 같은 모듈 구성(EQ · Dynamics · Exciter · Imager · Thicken ·
Maximizer · De-esser, RX식 Upscale)과 파라미터 개념을 numpy만으로 재현한 근사 구현이다.
Hermes 에이전트가 터미널에서 호출하고 JSON 결과를 읽도록 설계했다.

사용법:
  izotope_sim.py analyze IN [--json]
  izotope_sim.py process IN OUT (--chain CHAIN.json | --preset NAME) [--report R.json]
  izotope_sim.py assist IN --out CHAIN.json [--series electric-folklore|blues] [--target -14]
  izotope_sim.py match IN REF OUT
  izotope_sim.py upscale IN OUT [--sr 48000] [--bits 24|32f|16] [--stereo] [--amount 50]
  izotope_sim.py presets
"""

import argparse
import json
import math
import os
import shutil
import struct
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
    try:
        return _read_wav_float(path)
    except (ValueError, struct.error, OSError):
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


def _read_wav_float(path):
    """wave 모듈이 못 읽는 IEEE float(포맷 3) WAV를 읽는다."""
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
        raise ValueError("WAV 아님")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(buf):
        cid, size = buf[pos:pos + 4], struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        body = buf[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
            if fmt[0] == 0xFFFE and len(body) >= 26:
                fmt = (struct.unpack("<H", body[24:26])[0],) + fmt[1:]
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if not fmt or data is None or fmt[0] != 3 or fmt[5] not in (32, 64):
        raise ValueError("float WAV 아님")
    arr = np.frombuffer(data, "<f4" if fmt[5] == 32 else "<f8").astype(np.float64)
    return arr.reshape(-1, fmt[1]), fmt[2]


def write_audio(path, x, sr, bits="24"):
    """bits: "24"(기본, PCM) | "16"(TPDF 디더) | "32f"(float). .wav가 아니면 ffmpeg로 변환한다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if path.lower().endswith(".wav"):
        _write_wav(path, x, sr, bits)
        return
    if not shutil.which("ffmpeg"):
        sys.exit("WAV 이외 형식으로 저장하려면 ffmpeg가 필요합니다.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_wav = os.path.join(tmp, "out.wav")
        _write_wav(tmp_wav, x, sr, bits)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp_wav, path], check=True)


def _write_wav(path, x, sr, bits="24"):
    bits = str(bits)
    if bits == "32f":
        data = x.astype("<f4").tobytes()
        ch = x.shape[1]
        body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 3, ch, sr, sr * ch * 4, ch * 4, 32)
                + b"fact" + struct.pack("<II", 4, len(x)) + b"data" + struct.pack("<I", len(data)) + data)
        with open(path, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
        return
    if bits == "16":
        rng = np.random.default_rng(0)
        tpdf = (rng.random(x.shape) - rng.random(x.shape))
        v = np.clip(np.round(x * 32768 + tpdf), -32768, 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(x.shape[1])
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(v.tobytes())
        return
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


BODY_TARGET_DB, BODY_THIN_DB = 10.0, 6.0


def _body_index(f, p):
    """두께 지표: 저중역(150–600Hz) 에너지 - 고역(2.4–9.6kHz) 에너지. 둘 다 2옥타브라 핑크노이즈는 0dB."""
    return _band_db(f, p, 150, 600) - _band_db(f, p, 2400, 9600)


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
        "body_index_db": round(_body_index(f, p), 2),
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


def _mod_delay(sig, sr, base_ms, depth_ms, rate_hz, phase):
    t = np.arange(len(sig))
    d = (base_ms + depth_ms * np.sin(2 * np.pi * rate_hz * t / sr + phase)) * sr / 1000
    return np.interp(t - d, t, sig, left=0.0)


def _rms(v):
    return math.sqrt(float(np.mean(v ** 2))) + EPS


def mod_thicken(x, sr, p):
    """빈약한 음원 보강: 저역 배음 생성 + 저중역 병렬 압축(밀도) + 사이드 전용 더블러 + 바디 EQ.

    amount(0-100, 기본 50)가 네 단계의 양을 함께 조절한다. auto_gain(기본 true)이면
    처리 전 통합 라우드니스로 되돌려 '커져서 두꺼워 보이는' 착시를 없앤다.
    """
    k = max(0.0, min(100.0, float(p.get("amount", 50)))) / 50
    f0, p0 = _avg_spectrum(x, sr)
    body_before = _body_index(f0, p0)
    lufs_before = loudness(x, sr)["integrated_lufs"]
    info = {"amount": round(k * 50)}
    y = x

    # 1) 하모닉: 저역(기본 60–350Hz)에서 2·3차 배음을 만들어 120Hz–1.4kHz에 더한다.
    #    v²(짝수)·v³(홀수) 배음에서 원신호와 상관된 성분을 최소제곱으로 빼서 기음은 건드리지 않는다.
    #    작은 스피커에서도 저역이 '들리는' 심리음향 베이스 보강(Ozone Exciter/Low End Focus 개념).
    #    even(기본 0.7)이 클수록 따뜻하다.
    h = p.get("harmonics", {})
    h_mix = float(h.get("mix", 35)) / 100 * k
    if h_mix > 0:
        lo, hi = float(h.get("low", 60)), float(h.get("high", 350))
        band = split_bands(y, sr, [lo, hi])[1]
        ref = float(np.percentile(np.abs(band), 99.5)) + EPS
        v = np.clip(band / ref, -1, 1)
        even = max(0.0, min(1.0, float(h.get("even", 0.7))))
        gen = even * v ** 2 + (1 - even) * v ** 3
        gen -= gen.mean(axis=0)
        gen -= np.sum(gen * v, axis=0) / (np.sum(v * v, axis=0) + EPS) * v
        gen *= _rms(v) / _rms(gen)
        harm = split_bands(gen * ref, sr, [lo * 2, hi * 4])[1]
        y = y + h_mix * harm
        info["harmonics_db"] = round(_db(_rms(h_mix * harm)) - _db(_rms(x)), 1)

    # 2) 밀도: 저중역(기본 150–1000Hz)을 강하게 압축한 신호를 RMS를 맞춰 병렬로 섞는다.
    #    큰 소리는 그대로, 잔향·서스테인처럼 작은 부분이 올라와 소리가 꽉 찬다.
    dn = p.get("density", {})
    d_mix = min(0.8, float(dn.get("mix", 30)) / 100 * k)
    if d_mix > 0:
        lo, hi = float(dn.get("low", 150)), float(dn.get("high", 1000))
        low, mid, high = split_bands(y, sr, [lo, hi])
        thr = _db(_rms(mid)) - 6
        curve, gr = _gain_reduction(mid, sr, {"threshold": thr, "ratio": float(dn.get("ratio", 4)),
                                              "attack": 10, "release": 120, "knee": 6})
        comp = mid * curve[:, None]
        comp *= _rms(mid) / _rms(comp)
        y = low + (1 - d_mix) * mid + d_mix * comp + high
        info["density_gr_db"] = round(max(0.0, gr), 2)

    # 3) 더블러: 300Hz–6kHz 미드를 서로 다른 변조 딜레이 두 개로 복제해 사이드에만 더한다.
    #    모노로 합치면 정확히 상쇄되므로 모노 호환성을 해치지 않는다.
    db_ = p.get("doubler", {})
    w_mix = float(db_.get("mix", 25)) / 100 * k
    if w_mix > 0 and y.shape[1] == 2:
        m, s = to_ms(y)
        src = split_bands(m[:, None], sr, [float(db_.get("low", 300)), float(db_.get("high", 6000))])[1][:, 0]
        v1 = _mod_delay(src, sr, 13.0, 1.2, 0.35, 0.0)
        v2 = _mod_delay(src, sr, 19.0, 1.5, 0.27, math.pi / 2)
        y = from_ms(m, s + w_mix * (v1 - v2) / 2)
        info["doubler_mix"] = round(w_mix * 100)
    elif w_mix > 0:
        info["doubler"] = "모노 입력이라 건너뜀"

    # 4) 바디 EQ: 두께 지표가 목표보다 모자란 만큼 300Hz 넓은 벨(150–600Hz)을 올린다(최대 +3dB).
    body_eq = p.get("body_eq", "auto")
    if body_eq == "auto":
        f1, p1 = _avg_spectrum(y, sr)
        body_eq = max(0.0, min(3.0, (BODY_TARGET_DB - _body_index(f1, p1)) / 3)) * min(1.0, k)
    body_eq = float(body_eq)
    if abs(body_eq) >= 0.1:
        hfir, dly = eq_fir([{"type": "bell", "freq": 300, "gain": body_eq, "q": 0.7}], sr)
        y = fft_convolve(y, hfir, dly)
    info["body_eq_db"] = round(body_eq, 1)

    if p.get("auto_gain", True) and math.isfinite(lufs_before):
        y = y * 10 ** ((lufs_before - loudness(y, sr)["integrated_lufs"]) / 20)
    f2, p2 = _avg_spectrum(y, sr)
    info["body_index_db"] = [round(body_before, 1), round(_body_index(f2, p2), 1)]
    if y.shape[1] == 2:
        def corr(v):
            return float(np.sum(v[:, 0] * v[:, 1]) / (math.sqrt(float(np.sum(v[:, 0] ** 2) * np.sum(v[:, 1] ** 2))) + EPS))
        c0, c1 = corr(x), corr(y)
        info["stereo_correlation"] = [round(c0, 2), round(c1, 2)]
        if c1 < c0 - 0.2:
            info["phase_warning"] = "스테레오 상관도가 0.2 넘게 떨어졌습니다. doubler.mix를 낮추세요."
    tp = _db(float(true_peak_env(y).max()))
    if tp > -0.5:
        info["warning"] = f"트루피크 {tp:.1f}dBTP. 뒤에 maximizer를 두거나 앞에 gain -3을 넣으세요."
    return y, info


MODULES = {"eq": mod_eq, "dynamics": mod_dynamics, "deesser": mod_deesser,
           "exciter": mod_exciter, "imager": mod_imager, "maximizer": mod_maximizer,
           "thicken": mod_thicken, "gain": mod_gain}
ALIASES = {"ozone_eq": "eq", "neutron_eq": "eq", "equalizer": "eq", "compressor": "dynamics",
           "ozone_dynamics": "dynamics", "de-esser": "deesser", "de_ess": "deesser",
           "stereo_imager": "imager", "ozone_imager": "imager", "limiter": "maximizer",
           "ozone_maximizer": "maximizer", "ozone_exciter": "exciter", "saturation": "exciter",
           "enrich": "thicken", "body": "thicken", "warmth": "thicken", "fatten": "thicken"}


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


# ---------------------------------------------------------------- 업스케일 (정보량 보강)
# 잃어버린 원본 정보를 되살리는 것이 아니라, 남은 신호로부터 그럴듯한 정보를 다시 만든다
# (RX De-clip, 대역 확장(SBR), 리샘플, 스테레오화의 개념).

def detect_cutoff(x, sr):
    """손실 압축이 남긴 고역 차단 주파수(Hz)를 찾는다. 자연스러운 롤오프면 None."""
    f, p = _avg_spectrum(x, sr, nfft=8192)
    S = 10 * np.log10(np.maximum(p, EPS))
    bw = max(1, int(150 / (f[1] - f[0])))
    S = np.convolve(S, np.ones(bw) / bw, mode="same")
    ref = float(np.median(S[(f >= 1000) & (f < 5000)]))
    best, best_drop = None, 0.0
    # 19kHz 이상 차단은 들리지 않으므로(그리고 upscale의 기본 확장 상한이 20kHz라) 문제로 보지 않는다.
    for fc in np.arange(4000, min(19000.0, sr / 2 - 1000), 100.0):
        below = S[(f >= fc - 600) & (f < fc - 100)].mean()
        above = S[(f >= fc + 100) & (f < fc + 600)].mean()
        rest = S[(f >= fc + 100) & (f < sr / 2 * 0.98)].mean()
        drop = below - above
        if drop > 25 and rest < ref - 40 and drop > best_drop:
            best, best_drop = float(fc), float(drop)
    return best


def _effective_bits(x):
    """24비트 격자 위에서 실제로 쓰인 비트 수. 16비트 원본이면 16."""
    q = np.round(x * (1 << 23)).astype(np.int64).reshape(-1)
    nz = q[q != 0]
    if not len(nz):
        return 0
    acc = int(np.bitwise_or.reduce(np.abs(nz)))
    tz = (acc & -acc).bit_length() - 1
    return 24 - tz


def _clip_runs(ch, min_run=3):
    peak = float(np.abs(ch).max())
    if peak <= 0:
        return [], peak
    m = np.abs(ch) >= peak * 0.998
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    keep = (ends - starts) >= min_run
    return list(zip(starts[keep].tolist(), ends[keep].tolist())), peak


def quality(x, sr):
    """정보량 진단: 샘플레이트, 유효 비트, 대역폭, 모노 여부, 클리핑."""
    fc = detect_cutoff(x, sr)
    runs = sum(len(_clip_runs(x[:, c])[0]) for c in range(x.shape[1]))
    dual_mono = x.shape[1] == 2 and float(np.max(np.abs(x[:, 0] - x[:, 1]))) < 1e-6
    q = {"sample_rate": sr, "effective_bits": _effective_bits(x),
         "bandwidth_hz": fc if fc else round(sr / 2), "lossy_cutoff": fc is not None,
         "channels": x.shape[1], "mono_content": x.shape[1] == 1 or dual_mono, "clip_runs": runs}
    issues = []
    if sr < 44100:
        issues.append(f"샘플레이트 {sr}Hz — 44.1kHz 미만")
    if q["effective_bits"] and q["effective_bits"] < 16:
        issues.append(f"유효 비트 {q['effective_bits']}bit")
    if fc:
        issues.append(f"{fc / 1000:.1f}kHz 이상이 잘림(손실 압축 흔적)")
    if q["mono_content"]:
        issues.append("모노 음원")
    if runs > 10:
        issues.append(f"클리핑 구간 {runs}곳")
    q["issues"] = issues
    return q


def declip(x):
    """클리핑 구간을 앞뒤 기울기로 이어 붙이는 3차 에르미트 보간으로 복원한다(RX De-clip 개념)."""
    y = x.copy()
    total = 0
    for c in range(x.shape[1]):
        runs, peak = _clip_runs(x[:, c])
        ch = y[:, c]
        for a, b in runs:
            if a < 2 or b + 2 > len(ch) or b - a > 400:
                continue
            p0, p1 = ch[a - 1], ch[b]
            m0, m1 = ch[a - 1] - ch[a - 2], ch[b + 1] - ch[b]
            D = b - a + 1
            t = (np.arange(a, b) - (a - 1)) / D
            h = ((2 * t ** 3 - 3 * t ** 2 + 1) * p0 + (t ** 3 - 2 * t ** 2 + t) * D * m0
                 + (-2 * t ** 3 + 3 * t ** 2) * p1 + (t ** 3 - t ** 2) * D * m1)
            sign = np.sign(ch[a:b])
            # 기울기 추정이 튀면 과하게 솟으므로 클립 레벨의 1.6배(+4dB)까지만 복원한다.
            ch[a:b] = sign * np.clip(sign * h, np.abs(ch[a:b]), peak * 1.6)
            total += 1
    return y, total


def resample(x, sr_in, sr_out):
    """블록 FFT 리샘플(유리수 비율). 경계는 넉넉한 겹침으로 버린다."""
    if sr_in == sr_out:
        return x
    g = math.gcd(sr_in, sr_out)
    L, M = sr_out // g, sr_in // g
    n, ch = x.shape
    n_out = n * L // M
    chunk = M * max(1, (1 << 16) // M)
    pad = M * max(1, -(-4096 // M))
    xp = np.vstack([np.zeros((pad, ch)), x, np.zeros((pad + chunk, ch))])
    out = np.zeros((n_out, ch))
    edge = 0.5 * min(sr_in, sr_out)
    for s in range(0, n, chunk):
        seg = xp[s:s + chunk + 2 * pad]
        mp = len(seg)
        mo = mp * L // M
        X = np.fft.rfft(seg, axis=0)
        fk = np.arange(len(X)) * sr_in / mp
        taper = np.clip((edge - fk) / (0.07 * edge), 0, 1)
        taper = 0.5 - 0.5 * np.cos(np.pi * taper)
        Y = np.zeros((mo // 2 + 1, ch), complex)
        k = min(len(X), len(Y))
        Y[:k] = X[:k] * taper[:k, None]
        y = np.fft.irfft(Y, mo, axis=0) * (mo / mp)
        o0, o1 = s * L // M, min(n_out, (s + chunk) * L // M)
        off = pad * L // M
        out[o0:o1] = y[off:off + o1 - o0]
    return out


def bandwidth_extend(x, sr, fc, max_freq=20000.0, amount=50.0):
    """SBR 방식 대역 확장: 차단 주파수 아래 한 옥타브를 위로 복제하고 스펙트럼 기울기를 이어 붙인다.

    원신호는 그대로 두고 생성한 고역만 더한다. 복제 간격을 홉의 배수 빈으로 맞춰 프레임 사이
    위상이 이어지게 했다.
    """
    nfft, hop = 2048, 512
    win = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(nfft) / nfft))
    kc = int(fc * nfft / sr)
    w = (kc // 2) // (nfft // hop) * (nfft // hop)
    kmax = int(min(max_freq, 0.97 * sr / 2) * nfft / sr)
    if w < 8 or kc >= kmax - 4:
        return x, {"skipped": "확장할 대역 없음"}
    f, p = _avg_spectrum(x, sr)
    sel = (f >= fc / 4) & (f < fc * 0.95)
    slope = float(np.polyfit(np.log2(f[sel]), 10 * np.log10(np.maximum(p[sel], EPS)), 1)[0])
    slope = max(-15.0, min(-3.0, slope)) - 1.5
    tgt = np.arange(kc, kmax)
    src = kc - w + ((tgt - kc) % w)
    gain = 10 ** (slope * np.log2(tgt / src) / 20) * (amount / 50)
    gain *= np.clip((tgt - kc + 1) / 8, 0, 1)
    n, ch = x.shape
    xp = np.vstack([np.zeros((nfft, ch)), x, np.zeros((nfft + hop, ch))])
    nfr = (len(xp) - nfft) // hop + 1
    gen = np.zeros_like(xp)
    q = nfft // hop
    for c in range(ch):
        for b0 in range(0, nfr, 1024):
            st = np.arange(b0, min(nfr, b0 + 1024)) * hop
            X = np.fft.rfft(xp[st[:, None] + np.arange(nfft), c] * win, axis=1)
            G = np.zeros_like(X)
            G[:, tgt] = X[:, src] * gain
            g = np.fft.irfft(G, nfft, axis=1) * win
            B = len(st)
            acc = np.zeros((B + q - 1) * hop)
            for j in range(q):
                acc[j * hop:(j + B) * hop] += g[:, j * hop:(j + 1) * hop].reshape(-1)
            e = min(len(gen), st[0] + len(acc))
            gen[st[0]:e, c] += acc[:e - st[0]]
    gen = gen[nfft:nfft + n] / (np.sum(win ** 2) / hop)
    info = {"cutoff_hz": fc, "extended_to_hz": round(kmax * sr / nfft), "slope_db_oct": round(slope, 1),
            "added_db": round(_db(_rms(gen)) - _db(_rms(x)), 1)}
    if fc < 16000:
        info["note"] = "차단 주파수가 16kHz 미만이라 금속성 대역(8–16kHz) 일부를 새로 만들었습니다. 쇳소리가 들리면 amount를 낮추세요."
    return x + gen, info


def stereoize(x, sr, width=35.0):
    """모노를 스테레오로: 디코릴레이트한 중역을 사이드에만 넣어 모노 합은 원본과 같다."""
    m = x.mean(axis=1)
    src = split_bands(m[:, None], sr, [300, 8000])[1][:, 0]
    s = (_mod_delay(src, sr, 11.0, 0.8, 0.31, 0.0) - _mod_delay(src, sr, 17.0, 1.1, 0.23, math.pi / 3)) / 2
    return from_ms(m, s * width / 100 * 2)


def upscale(x, sr, target_sr=None, amount=50.0, max_freq=20000.0, bwe=True, do_declip="auto",
            make_stereo=False, width=35.0):
    before = quality(x, sr)
    steps = []
    if do_declip == "on" or (do_declip == "auto" and before["clip_runs"] > 10):
        x, n = declip(x)
        steps.append({"step": "declip", "repaired_runs": n})
    target_sr = int(target_sr or max(sr, 48000))
    if target_sr != sr:
        x = resample(x, sr, target_sr)
        steps.append({"step": "resample", "from": sr, "to": target_sr})
    fc = detect_cutoff(x, target_sr) if bwe else None
    if fc is None and bwe and sr < 44100:
        fc = sr / 2 * 0.93  # 낮은 샘플레이트 원본은 원래 나이퀴스트가 곧 차단 주파수
    if fc and amount > 0:
        x, info = bandwidth_extend(x, target_sr, fc, max_freq, amount)
        steps.append({"step": "bandwidth_extend", **info})
    if make_stereo and before["mono_content"]:
        x = stereoize(x, target_sr, width)
        steps.append({"step": "stereoize", "width": width})
    tp = float(true_peak_env(x).max())
    if tp > 10 ** (-0.3 / 20):
        x = x * (10 ** (-0.3 / 20) / tp)
        steps.append({"step": "headroom", "gain_db": round(_db(10 ** (-0.3 / 20) / tp), 2)})
    return x, target_sr, steps, before


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
    "thicken": {
        "description": "빈약한 음원 보강 — 저역 배음·저중역 밀도·사이드 더블러·바디 EQ 후 -14 LUFS. 쇳소리 대역은 건드리지 않음.",
        "modules": [
            {"module": "eq", "bands": [HP, {"type": "bell", "freq": 3500, "gain": -1.0, "q": 4.0}]},
            {"module": "thicken", "amount": 60},
            {"module": "dynamics", "bands": [{"threshold": -20, "ratio": 1.5, "attack": 30, "release": 180, "knee": 8}]},
            {"module": "imager", "crossovers": [120], "widths": [-100, 0]},
            {"module": "maximizer", "target_lufs": -14, "ceiling": -1.0, "character": "balanced"},
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
    body = a["body_index_db"]
    if body < BODY_THIN_DB:
        amount = int(max(30, min(80, (BODY_TARGET_DB - body) * 8)))
        modules.append({"module": "thicken", "amount": amount})
        notes.append(f"두께 지표 {body:.1f}dB(기준 {BODY_THIN_DB:g} 미만)로 빈약함 → thicken amount {amount}")
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
    s = sub.add_parser("upscale")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--sr", type=int, help="출력 샘플레이트(기본: max(원본, 48000))")
    s.add_argument("--bits", choices=["16", "24", "32f"], default="24")
    s.add_argument("--amount", type=float, default=50.0, help="고역 복원 양 0–100 (0이면 끔)")
    s.add_argument("--max-freq", type=float, default=20000.0)
    s.add_argument("--declip", choices=["auto", "on", "off"], default="auto")
    s.add_argument("--stereo", action="store_true", help="모노 음원을 스테레오로")
    s.add_argument("--width", type=float, default=35.0)
    s.add_argument("--report")
    sub.add_parser("presets")
    args = ap.parse_args(argv)

    if args.cmd == "presets":
        _dump({k: v["description"] for k, v in PRESETS.items()})
    elif args.cmd == "analyze":
        x, sr = read_audio(args.input)
        res = analyze(x, sr)
        res["quality"] = quality(x, sr)
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
    elif args.cmd == "upscale":
        x, sr = read_audio(args.input)
        y, osr, steps, before = upscale(x, sr, args.sr, args.amount, args.max_freq, args.amount > 0,
                                        args.declip, args.stereo, args.width)
        write_audio(args.output, y, osr, args.bits)
        after = quality(*read_audio(args.output)) if args.output.lower().endswith(".wav") else quality(y, osr)
        res = {"output": args.output, "bits": args.bits, "steps": steps,
               "before": before, "after": after}
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        _dump(res)


if __name__ == "__main__":
    main()
