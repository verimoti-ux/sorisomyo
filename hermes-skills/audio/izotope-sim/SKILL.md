---
name: izotope-sim
description: Simulate iZotope Ozone/Neutron/RX-style processing on audio files — mastering chains (EQ, dynamics, de-esser, exciter, imager, maximizer), thickening thin tracks, upscaling low-quality files (bandwidth extension, de-clip, resample, stereoize), and LUFS/true peak/quality analysis
version: 1.2.0
author: SORISOMYO
metadata:
  tags: [izotope, ozone, neutron, rx, mastering, mixing, loudness, lufs, audio, suno, thicken, warmth, upscale, restoration, mp3]
  language: ko
---

# iZotope 플러그인 시뮬레이터

iZotope Ozone·Neutron의 모듈 구성(EQ · Dynamics · De-esser · Exciter · Imager · Maximizer)과
빈약한 음원을 두툼하게 채우는 Thicken 모듈, 정보량이 적은(저음질) 파일을 보강하는 Upscale(RX 개념),
Master Assistant 개념을 numpy로 재현한 처리 엔진. 실제 오디오 파일을 처리하고, 처리 전후를
BS.1770 라우드니스·트루피크·대역 밸런스로 측정해 JSON으로 돌려준다.

iZotope의 실제 알고리즘이나 제품이 아니다(제휴 없음). 플러그인이 없는 환경에서 체인을
설계·검증하고, 실제 Ozone/Neutron에 옮길 출발값을 찾는 용도다.

## When to Use

- 사용자가 Ozone·Neutron·iZotope 처리, 마스터링, 음압 맞추기를 요청할 때
- WAV/MP3/FLAC 파일의 LUFS·트루피크·LRA·대역 밸런스를 물을 때
- Suno 스템/믹스를 외부 처리(쇳소리·샤머 정리, 디에싱, 저역 모노화)하려 할 때
- 유튜브 업로드용(-14 LUFS, -1 dBTP) 최종 음원을 만들 때
- 정보량이 적은 파일(저비트레이트 MP3, 22/32kHz, 고역이 잘림, 모노, 클리핑)을 고음질 파일로 만들어 달라는 요청일 때
- 소리가 얇다·빈약하다·가볍다·속이 비었다·두툼하게/풍성하게/따뜻하게 해 달라는 요청일 때
- 체인 값을 바꿔 가며 결과를 비교하고 실제 플러그인 세팅으로 옮길 때

## Quick Reference

스크립트: `scripts/izotope_sim.py` (Python 3.9+, numpy 필수, WAV 외 형식은 ffmpeg 필요)

```bash
S=scripts/izotope_sim.py
python3 $S analyze song.wav --json                      # 측정만
python3 $S presets                                      # 프리셋 목록
python3 $S process song.wav out.wav --preset electric-folklore --report report.json
python3 $S assist song.wav --out chain.json --series blues   # 분석 기반 체인 자동 생성
python3 $S process song.wav out.wav --chain chain.json  # 체인 JSON으로 처리
python3 $S match song.wav out.wav song_matched.wav      # A/B용 음량 맞춤
python3 $S upscale low.mp3 hq.wav [--stereo] [--sr 96000 --bits 32f]   # 정보량 보강
```

- 프리셋: `electric-folklore`, `blues`, `suno-cleanup`, `thicken`, `youtube-loudness`
- 두께 보강만 할 때: `{"modules": [{"module": "thicken", "amount": 50}]}` (amount 0–100)
- 체인 JSON 형식·모듈 파라미터·iZotope 대응표: `references/modules.md`
- 예시 체인: `templates/chain-example.json`
- 실행 시간: 3분 스테레오 48kHz 기준 프리셋 하나에 약 30–40초(`thicken` 포함 시 약 1분), `upscale` 약 10–20초
- `analyze` 결과의 `quality.issues`가 비어 있지 않으면 upscale 대상이다

## Procedure

1. `analyze --json`으로 원본을 측정한다. `integrated_lufs`, `true_peak_dbtp`, `plr_db`,
   `band_balance_db`(특히 `metallic_2k_4k5`, `metallic_8k_16k`), `low_side_to_mid_db`,
   `body_index_db`(두께 지표), `quality`(정보량 진단)를 본다.
2. `quality.issues`가 있으면(손실 압축 차단, 44.1kHz 미만, 모노, 클리핑) 다른 처리보다 먼저
   `upscale`을 돌리고, 그 출력 WAV를 이후 모든 단계의 입력으로 쓴다.
   - 모노 음원이면 `--stereo`를 붙일지 사용자에게 묻는다(원래 모노 의도일 수 있다).
   - 결과의 `steps`에서 `bandwidth_extend.cutoff_hz` → `extended_to_hz`, `declip.repaired_runs`를 전달한다.
   - 보관·재작업용은 `--bits 32f`, 업로드용은 기본 24비트로 충분하다.
3. 시리즈를 판별한다. 신스·패드·사이드체인 → `electric-folklore`, 어쿠스틱 기타·하모니카·
   브러시 드럼 → `blues`. 판별이 어려우면 `assist`로 시작한다.
4. Suno 생성 음원이고 쇳소리가 들린다는 언급이 있으면 먼저 `suno-cleanup`을 적용한 파일을
   다음 단계 입력으로 쓴다.
5. 소리가 얇다는 요청이거나 `body_index_db`가 6 미만이면 두께 보강을 넣는다.
   - 시리즈 체인을 쓸 때: 체인 JSON의 eq·deesser 다음, dynamics 앞에
     `{"module": "thicken", "amount": 40–70}`을 끼운다. `assist`는 이것을 자동으로 넣는다.
   - 시리즈 판별 없이 보강만: `--preset thicken`.
   - amount 기준: 살짝 따뜻하게 30 / 기본 50 / 많이 얇음(`body_index_db` 0 미만) 60–80.
6. `process ... --preset <시리즈>` 또는 `assist`로 만든 `chain.json`으로 처리한다. `--report`로
   전후 전체 측정값을 저장한다.
7. 출력 JSON의 `steps`를 확인한다. `warning`(리미터 GR 6dB 초과)이나 `target_missed`가 있으면
   앞단 dynamics threshold를 낮추거나 목표 음압을 1–2dB 낮춰 다시 처리한다. thicken 단계에
   `phase_warning`이 있으면 `doubler.mix`를 낮춘다. `body_index_db`의 [전, 후]로 효과를 확인한다.
8. 수정이 필요하면 체인 JSON을 복사해 값만 바꾼다(게인 ±1–2dB 단위). 프리셋 내용은
   `references/modules.md`에 그대로 적혀 있다.
9. `match`로 원본을 결과물 음량에 맞춘 파일을 만들어 사용자에게 A/B 비교를 권한다.
10. 사용자가 실제 Ozone/Neutron을 쓴다면 최종 체인 값을 `references/modules.md`의 대응표에
   따라 모듈별 세팅으로 풀어서 전달한다.

## Pitfalls

- 시뮬레이션이다. IRC 리미터, Spectral Shaper, Tonal Balance 타깃 등 iZotope 고유 알고리즘은
  재현하지 않는다. 결과 음색은 실제 플러그인과 다르다. 사용자에게 "근사값"임을 밝힌다.
- 금속성 대역(2–4.5kHz, 8–16kHz)은 부스트하지 않는다. `assist`도 이 대역 부스트는 건너뛴다.
- EQ 부스트로 음량을 올리지 않는다. 음압은 마지막 maximizer에서만 맞춘다.
- exciter는 오버샘플링 없이 동작해 drive를 높이면(60 이상) 에일리어싱이 생긴다. mix 10–25%로 쓴다.
- upscale은 잃어버린 원본을 복원하지 않는다. 남은 대역에서 그럴듯한 고역·피크를 새로 만드는 것이다.
  사용자에게 "복원"이 아니라 "보강"이라고 설명한다. 샘플레이트·비트 깊이만 올리는 것은 정보량을 늘리지 않는다
  (변환이 할 일을 해 둘 뿐이고, 실제로 더해지는 것은 대역 확장·디클립·스테레오화다).
- upscale 대역 확장은 차단 주파수가 16kHz 미만이면 금속성 대역(8–16kHz)을 새로 만든다. 결과에 `note`가
  붙으면 쇳소리를 확인하고, 거슬리면 `--amount 25–35`로 다시 만든다.
- declip은 톤 성분(보컬·악기 지속음)의 클리핑에 효과가 크고, 노이즈성 소리(심벌·디스토션 기타)에는 효과가 작다.
  복원된 피크 때문에 전체 게인이 내려가면(`headroom` 단계) 뒤에 maximizer로 음압을 다시 맞춘다.
- MP3 특유의 프리에코·스월링(물 흐르는 소리) 같은 압축 잡음은 제거하지 못한다.
- thicken은 없는 소리를 만들지 못한다. 저역이 아예 잘린 음원(150Hz 이하가 비어 있음)은 배음
  재료가 없어 바디 EQ와 밀도만 동작한다. 이때 amount를 80 넘게 올리지 말고 원본 재생성을 권한다.
- thicken은 자체적으로 음량을 원래대로 되돌린다(auto_gain). 두꺼워진 느낌을 음량 차이로
  착각하지 않게 하려는 것이므로 끄지 않는다.
- thicken의 doubler는 사이드에만 더해 모노에서 사라진다. 휴대폰 스피커(모노)에서 두께는 배음·밀도·
  바디 EQ가 담당한다.
- imager는 저역(120Hz 이하)을 넓히지 않는다. 저역은 -100(모노) 쪽으로만 조정한다.
- 체인 JSON에서 `crossovers`가 N개면 `bands`/`widths`는 반드시 N+1개다. 아니면 오류로 멈춘다.
- 입력이 이미 -10 LUFS보다 크게 마스터링된 음원이면 maximizer를 다시 걸지 말고
  `youtube-loudness`도 생략한다(유튜브가 알아서 낮춘다).
- 출력은 24비트 PCM WAV. 확장자가 .wav가 아니면 ffmpeg로 변환한다. 원본 파일을 덮어쓰지 않는다.

## Verification

- 처리 후 출력 JSON의 `after.integrated_lufs`가 목표 ±0.3 이내, `after.true_peak_dbtp`가
  ceiling 이하인지 확인한다.
- `delta.band_balance_db`에서 어떤 대역도 ±3dB 넘게 움직이지 않았는지 본다(넘으면 과처리).
- ffmpeg가 있으면 독립 측정으로 교차 확인한다:
  `ffmpeg -nostats -i out.wav -af ebur128=peak=true -f null - 2>&1 | tail -12`
- upscale 후 `after.quality.issues`가 비었는지(모노 유지를 택한 경우 "모노 음원"만 남는 것은 정상) 확인한다.
- 같은 음량(`match`)으로 맞춘 원본과 결과를 같은 구간끼리 비교하도록 안내한다.
