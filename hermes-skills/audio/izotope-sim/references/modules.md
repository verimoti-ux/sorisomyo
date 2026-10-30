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
| `thicken` (enrich, body, warmth, fatten) | Ozone Exciter + Low End Focus + Neutron 병렬 컴프 + Imager Stereoize 조합 | 저역 배음 생성, 저중역 병렬 압축, 사이드 전용 더블러, 자동 바디 EQ, 음량 자동 복원 | Neutron Sculptor, 서브하모닉 합성 |
| `gain` | Utility | 단순 게인 | — |
| `upscale` 명령 | RX De-clip + 대역 확장(SBR 개념) + 리샘플 + Imager Stereoize | 손실 압축 차단 검출, 고역 복원, 클리핑 복원, 리샘플(최대 96kHz 이상), 모노→스테레오, 16/24/32f 저장 | RX Spectral Repair, De-crackle, MP3 아티팩트 제거, 머신러닝 복원 |
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

## thicken

빈약한(얇은) 음원을 두툼하게 채운다. 네 단계를 차례로 거치고, `amount`가 네 단계의 양을 함께 조절한다.

```json
{"module": "thicken", "amount": 50, "auto_gain": true,
 "harmonics": {"low": 60, "high": 350, "even": 0.7, "mix": 35},
 "density":   {"low": 150, "high": 1000, "ratio": 4, "mix": 30},
 "doubler":   {"low": 300, "high": 6000, "mix": 25},
 "body_eq": "auto"}
```

| 단계 | 하는 일 | 파라미터 |
|---|---|---|
| harmonics | `low`–`high` 저역에서 2·3차 배음을 만들어 `low×2`–`high×4`에 더한다. 원신호와 상관된 성분은 빼서 기음은 그대로 둔다. 작은 스피커에서도 저역이 들린다 | `even` 0–1(클수록 짝수 배음, 따뜻함), `mix` 0–100 |
| density | 저중역을 강하게 압축한 신호를 RMS를 맞춰 병렬로 섞는다. 큰 소리는 그대로, 서스테인·잔향이 올라와 속이 찬다 | `ratio`, `mix`(실효 최대 80) |
| doubler | 미드를 변조 딜레이 두 개(13ms/19ms)로 복제해 사이드에만 더한다. 모노 합에서 정확히 상쇄된다 | `mix`, 모노 입력은 건너뜀 |
| body_eq | 두께 지표가 목표(10dB)보다 모자란 만큼 300Hz 넓은 벨(Q 0.7)을 올린다(최대 +3dB) | `"auto"` 또는 dB 숫자, 0이면 끔 |

- `amount` 50이 각 `mix`의 기본값 그대로이고, 100이면 두 배, 0이면 모두 끈다.
- `auto_gain`(기본 true): 처리 전 통합 라우드니스로 되돌린다.
- 출력: `harmonics_db`(추가된 배음의 원신호 대비 레벨), `density_gr_db`, `doubler_mix`,
  `body_eq_db`, `body_index_db` [전, 후], `stereo_correlation` [전, 후], 필요하면 `phase_warning`·`warning`.

## upscale (명령)

정보량이 적은 파일을 고음질 파일로 보강한다. 체인 모듈이 아니라 별도 명령이다(샘플레이트가 바뀌기 때문).
결과 WAV를 `process`의 입력으로 쓰면 된다.

```bash
python3 izotope_sim.py upscale IN OUT [--sr 48000] [--bits 24|32f|16] [--amount 50] [--max-freq 20000]
                                      [--declip auto|on|off] [--stereo] [--width 35] [--report R.json]
```

| 단계 | 조건 | 하는 일 |
|---|---|---|
| declip | `auto`: 3샘플 이상 이어진 클립 구간이 10곳 초과 | 구간 앞뒤 기울기로 3차 에르미트 보간, 최대 +4dB까지 피크 복원 |
| resample | `--sr`(기본 max(원본, 48000))이 원본과 다르면 | 블록 FFT 유리수 비율 리샘플 |
| bandwidth_extend | 손실 압축 차단이 검출되거나 원본이 44.1kHz 미만 | 차단 주파수 아래 한 옥타브를 위로 복제(SBR), 원래 스펙트럼 기울기를 이어서 `--max-freq`까지 채움. 원신호는 건드리지 않고 생성분만 더함 |
| stereoize | `--stereo`이고 모노(또는 L=R) 음원 | 디코릴레이트한 300Hz–8kHz를 사이드에만 추가. 모노 합은 원본과 같음 |
| headroom | 트루피크 > -0.3dBTP | 전체 게인을 내려 클리핑 방지 |

- `--amount` 0–100(기본 50): 생성 고역의 양. 0이면 대역 확장을 끈다
- `--bits`: `24`(기본) | `32f`(float, 보관·재작업용) | `16`(TPDF 디더, CD 규격)
- 이미 고음질이면(차단 없음, 44.1kHz 이상, 클리핑 없음) 아무 것도 하지 않는다

### quality (analyze 결과에 포함)

| 키 | 의미 |
|---|---|
| `effective_bits` | 실제로 쓰인 비트 수(16비트 원본이면 16) |
| `bandwidth_hz` / `lossy_cutoff` | 손실 압축 차단 주파수(4–19kHz 범위에서 25dB 넘는 급경사). 없으면 나이퀴스트 |
| `mono_content` | 모노이거나 L=R |
| `clip_runs` | 3샘플 이상 이어진 클립 구간 수 |
| `issues` | 사람이 읽는 문제 목록. 비어 있으면 upscale 필요 없음 |

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

### thicken
HP 25Hz/24 · 3.5kHz -1 Q4 → thicken amount 60 → 싱글밴드 글루(-20dB, 1.5:1) → 저역 모노화(120Hz) →
maximizer -14 LUFS/-1 dBTP balanced

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
| `body_index_db` | 두께 지표: 150–600Hz 에너지 - 2.4–9.6kHz 에너지(둘 다 2옥타브, 핑크노이즈 = 0dB). 6 미만이면 빈약, 목표 10 내외. 휴리스틱이라 장르마다 다르다 |
| `stereo_correlation` | +1 모노, 0 무상관, 음수면 위상 문제 |
| `low_side_to_mid_db` | 120Hz 이하 사이드/미드 비율. -15dB보다 크면 저역 모노화 권장 |
