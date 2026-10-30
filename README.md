# sorisomyo
유튜브 채널 소리소묘(sorisomyo) 아티팩트를 제공하고 있습니다.

## Hermes 스킬

`hermes-skills/` 아래 스킬은 [Hermes Agent](https://github.com/NousResearch/hermes-agent)의 스킬 형식(SKILL.md)을 따른다.

| 스킬 | 설명 |
|---|---|
| `audio/izotope-sim` | iZotope Ozone/Neutron 스타일 체인(EQ·Dynamics·De-esser·Exciter·Imager·Maximizer) 시뮬레이션, 빈약한 음원 두께 보강(Thicken), 저음질 파일 정보량 보강(Upscale), LUFS/트루피크·품질 측정 |

설치:

```bash
mkdir -p ~/.hermes/skills/audio
cp -r hermes-skills/audio/izotope-sim ~/.hermes/skills/audio/
pip install numpy   # WAV 외 형식(mp3·flac)은 ffmpeg도 필요
```
