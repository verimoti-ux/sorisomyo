# 모듈 레퍼런스

체인은 `{"modules": [...]}` 형식의 JSON이다. 위에서 아래 순서로 처리한다. 모든 모듈에
`"bypass": true`를 넣으면 건너뛴다. 모듈 이름은 아래 이름 또는 별칭(괄호)을 쓸 수 있다.

## iZotope 대응표

| 시뮬레이터 모듈 | iZotope 대응 | 재현한 것 | 재현하지 않은 것 |
|---|---|---|---|
| `eq` (ozone_eq, neutron_eq) | Ozone EQ, Neutron EQ | 벨·쉘프·HP/LP(6–48dB/oct), Stereo/Mid/Side, Minimum/Linear phase | Dynamic EQ, Analog 모드, Baxandall |
| `dynamics` (compressor, ozone_dynamics) | Ozone/Neutron Dynamics | 멀티밴드(선형 위상 크로스오버), threshold·ratio·attack·release·knee·makeup·mix, 링크드 스테레오 | 리미터 섹션, 업워드 컴프, 사이드체인 필터 |
| `deesser` (de-esser, de_ess) | Nectar De-esser, RX De-ess | 치찰음 대역만 피크 검출 압축 | 스펙트럴 모드 |
| `exciter` (ozone_exciter, saturation) | Ozone Exciter | 멀티밴드, tape/tube/warm 커브, drive·mix | Retro/Triode 모드, 오버샘플링 |
| `imager` (stereo_imager, ozone_imager) | Ozone Imager | 멀티밴드 폭 -100~+100 | Stereoize, 벡터스코프 |
| `maximizer` (limiter, ozone_maximizer) | Ozone Maximizer | 룩어헤드 트루피크 리미터, 목표 LUFS 자동 게인, 성격별 릴리즈 | IRC 알고리즘, Soft Clip, Transient Emphasis |
| `gain` | Utility | 단순 게인 | — |
| `assist` 명령 | Master Assistant | 옥타브 밸런스 편차 보정, PLR 기반 글루 컴프, 저역 모노화, 목표 음압 | Tonal Balance 장르 타깃, Reference 매칭 |

## eq

```json
{"module": "eq", "phase": "minimum", "bands": [
  {"type": "highpass", "freq": 25, "slope": 24},
  {"type": "bell", "freq": 3500, "gain": -1.5, "q": 4, "channel": "stereo"},
  {"type": "low_shelf", "freq": 120, "gain": 1.0, "q": 0.707, "channel": "side"}
]}
```

- `type`: `bell` | `low_shelf` | `high_shelf` | `highpass` | `lowpass`
- `freq` Hz, `gain` dB(벨·쉘프), `q`(벨 기본 1.0, 쉘프 기본 0.707), `slope` dB/oct(HP/LP, 6의 배수, 기본 12)
- `channel`: `stereo`(기본) | `mid` | `side`
- `phase`: `minimum`(기본, 아날로그형) | `linear`(프리링잉 있음, 지연 보정됨)
- Q 표기: 좁게 = 4–6 / 중간 = 1.5–2 / 넓게 = 0.7–1

## dynamics

```json
{"module": "dynamics", "crossovers": [120, 2000, 8000], "mix": 100, "bands": [
  {"threshold": -22, "ratio": 2.0, "attack": 30, "release": 160, "knee": 6, "makeup": 0, "detection": "rms", "mix": 100}
]}
```

- `crossovers`: Hz 목록. 비우면 싱글밴드. `bands`는 crossovers 개수 + 1
- `threshold` dBFS, `ratio`(1 이하면 해당 밴드 통과), `attack`/`release` ms, `knee` dB, `makeup` dB
- `detection`: `rms`(기본) | `peak`
- 밴드별 `mix`와 모듈 전체 `mix`(병렬 압축) 0–100

## deesser

```json
{"module": "deesser", "low": 5500, "high": 9500, "threshold": -32, "ratio": 3, "attack": 2, "release": 60}
```

`low`–`high` 대역만 분리해 압축한다. 보컬 스템은 5–8kHz, 믹스 전체는 6–11kHz에서 시작.

## exciter

```json
{"module": "exciter", "crossovers": [200, 2000], "mix": 100, "bands": [
  {"mix": 0}, {"mode": "tube", "drive": 25, "mix": 15}, {"mode": "tape", "drive": 20, "mix": 10}
]}
```

- `mode`: `tape`(대칭 tanh) | `tube`(비대칭, 짝수 배음) | `warm`(부드러운 3차)
- `drive` 0–100(기본 30), `mix` 0–100(기본 0 = 통과)

## imager

```json
{"module": "imager", "crossovers": [120, 2000], "widths": [-100, 0, 10]}
```

`widths`: -100 = 모노, 0 = 원본, +100 = 사이드 2배. 모노 입력은 건너뛴다.

## maximizer

```json
{"module": "maximizer", "target_lufs": -14, "ceiling": -1.0, "character": "balanced", "true_peak": true}
```

- `target_lufs`를 주면 측정을 반복해 입력 게인을 찾는다. 없으면 `gain`(dB)만큼 밀어 넣는다
- `ceiling` dBTP(기본 -1.0), `true_peak`(기본 true, 4배 오버샘플 검출)
- `character`(릴리즈/룩어헤드): `transparent` 300ms/5ms, `balanced` 150/3, `loud` 60/1.5,
  `irc1`–`irc4`는 비슷한 성격의 별칭
- 출력 `max_gr_db` > 6이면 `warning`, 목표에 0.3 LU 넘게 못 미치면 `target_missed`

## gain

```json
{"module": "gain", "gain": -3}
```

## 프리셋 내용

### electric-folklore
HP 25Hz/24 · 280Hz -1 Q1 · 3.5kHz -1 Q4 · 11kHz -1 Q5 · Side 120Hz 로우쉘프 +1 →
4밴드 dynamics(120/2k/8k, ratio 1.6–2) → imager [-100, 0, +10] (120/2k) → maximizer -14 LUFS/-1 dBTP balanced

### blues
HP 30Hz/18 · 320Hz -1.5 Q1.2 · 2.8kHz -1 Q3 · 16kHz 하이쉘프 -1 → deesser 5.5–9.5k →
싱글밴드 글루(-20dB, 1.5:1, knee 8) → exciter 중역 tube 25/15% → imager [-60, +5] (150Hz) →
maximizer -14 LUFS/-1 dBTP transparent

### suno-cleanup
선형 위상 HP 25Hz/24 · 3.2kHz -1.5 Q5 · 9.5kHz -1.5 Q5 · 13kHz -1 Q4 → deesser 6–11k. 음압 변경 없음

### youtube-loudness
maximizer -14 LUFS / -1 dBTP transparent만

## 측정값 설명 (analyze)

| 키 | 의미 |
|---|---|
| `integrated_lufs` | BS.1770-4 통합 라우드니스(게이팅 포함). ffmpeg ebur128과 ±0.1 일치 |
| `true_peak_dbtp` | 4배 오버샘플 트루피크 |
| `lra_lu` | EBU Tech 3342 라우드니스 레인지 |
| `plr_db` | 트루피크 - 통합 라우드니스. 8 이하면 과압축 의심, 16 이상이면 다이내믹이 넓음 |
| `band_balance_db` | 전체 에너지 대비 대역 에너지(dB). `metallic_*`는 쇳소리 대역 |
| `octave_db` | 옥타브 밴드 에너지. assist가 기울기 회귀로 튀는 대역을 찾는 데 쓴다 |
| `stereo_correlation` | +1 모노, 0 무상관, 음수면 위상 문제 |
| `low_side_to_mid_db` | 120Hz 이하 사이드/미드 비율. -15dB보다 크면 저역 모노화 권장 |
