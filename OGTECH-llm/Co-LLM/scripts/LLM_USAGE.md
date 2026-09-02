# LLM 폴더 사용법 — 음성 녹음 · 변환 · 출력

Jetson에 SSH로 접속해서 쓰는 절차입니다. 노트북에서 `ssh kit@<젯슨IP>` 로 붙은 뒤 그대로 따라 하면 됩니다.

## 표기 약속

| 표기 | 뜻 |
|---|---|
| `{위치}` | 명령을 치기 **전에 있어야 하는 폴더**. 다르면 파일을 못 찾습니다 |
| `{실행}` | 실제로 타이핑하는 명령 |
| `{결과}` | 어디에 무엇이 생기는지 |

**이 폴더의 모든 명령은 `{위치}` 가 `/home/kit/00_TEST/LLM` 하나뿐입니다.**
아래 명령을 한 번 쳐 두면 그 다음부터는 `{실행}` 줄만 복사해 쓰면 됩니다.

```bash
cd /home/kit/00_TEST/LLM
```

---

## 0. 처음 한 번만

### 0-1. 파일이 다 왔는지 확인

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | `ls` |

```bash
cd /home/kit/00_TEST/LLM
ls
```

아래 9개가 **반드시** 있어야 합니다. 특히 `stt_prompt.txt` 와 `stt_prompt.sh` 가 없으면 `06_demo.sh` 가 안 돕니다.

```
00_check_audio.sh  02_play.sh  04_record_set.sh  06_demo.sh     stt_prompt.txt
01_record.sh       03_echo.sh  05_bench.sh       stt_prompt.sh  stt_prompt_s.txt
```

`engines.py` · `voice_loop.py` · `config.py` 는 파이썬 종단 지연 측정용이라 이 문서의 1~4장에는 필요 없습니다. (`voice_loop.py` 를 쓸 거면 `config.py` 가 **한 단계 위 폴더**에 있어야 합니다.)

### 0-2. 윈도우 줄바꿈 제거

윈도우에서 복사해 온 파일은 줄 끝에 `\r` 이 붙어 있어 bash가 거부합니다. **파일을 새로 복사할 때마다 한 번씩 칩니다.**

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | 아래 한 줄 |

```bash
sed -i 's/\r$//' /home/kit/00_TEST/LLM/*.sh
```

`.txt` 와 `.py` 는 안 해도 됩니다 — 스크립트가 읽을 때 알아서 걸러냅니다.

### 0-3. 마이크 게인

**초기값이 0이라 그냥 쓰면 아무것도 안 들립니다** `[실측]`. 최대(16)로 올리고 자동 게인은 끕니다 — 자동 게인은 발화마다 증폭이 달라져서 연속 20회 재현성과 충돌합니다.

```bash
amixer -c Device cset numid=3 16
amixer -c Device cset numid=4 off
```

카드 이름이 안 먹으면 `-c Device` 를 `-c 0` 으로 바꿔서 다시 칩니다.
재부팅해도 유지하려면 한 번 더:

```bash
sudo alsactl store
```

---

## 1. 녹음만 하기

마이크만 꽂혀 있으면 됩니다. 스피커는 없어도 됩니다.

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | `bash 01_record.sh 5` ← **끝의 숫자가 녹음 초** |
| `{결과}` | `/home/kit/00_TEST/LLM/test_rec/rec.wav` |

```bash
cd /home/kit/00_TEST/LLM
bash 01_record.sh 5
```

숫자를 빼면 5초입니다. `bash 01_record.sh 8` 이면 8초입니다.

`recording 5s -- SPEAK NOW` 가 뜨는 **순간부터** 말합니다. 마이크와 입 사이는 **10~15 cm**.

끝나면 이런 줄이 나옵니다.

```
level  : peak 8154/32767  rms 4210
  OK
```

| 나온 값 | 뜻 | 할 일 |
|---|---|---|
| `OK` | 정상 | 그대로 진행 |
| `rms` 3000 미만 | 소리가 약함 | 더 가까이, 더 크게 다시 녹음. 자음이 뭉개집니다 `[실측]` |
| `FAIL: silent` | 게인 0 | 0-3 으로 돌아갑니다 |
| `WARN: clipping` | 너무 큼 | 조금 떨어져서 다시 |

> **다음 녹음이 `rec.wav` 를 덮어씁니다.** 남겨야 하면 4장을 보세요.

---

## 2. 스피커로 출력만 하기

스피커만 꽂혀 있으면 됩니다. 마이크는 없어도 됩니다.

### 2-1. 방금 녹음한 것 듣기

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | `bash 02_play.sh` |
| `{재생 파일}` | `/home/kit/00_TEST/LLM/test_rec/rec.wav` (인자 없을 때 기본값) |

```bash
cd /home/kit/00_TEST/LLM
bash 02_play.sh
```

### 2-2. 특정 파일 듣기

파일 이름을 뒤에 붙입니다.

```bash
cd /home/kit/00_TEST/LLM
bash 02_play.sh test_rec/버너_01.wav
```

### 2-3. 스피커가 살아 있는지만 확인 (TTS 테스트)

녹음 없이 한국어 음성을 합성해서 바로 냅니다.

```bash
cd /home/kit/00_TEST/LLM
bash 02_play.sh --tts
```

> 목소리가 로봇 같고 알아듣기 어려운 것이 **정상**입니다. espeak-ng는 최종 엔진이 아니라 폴백입니다(미결 #2). 여기서는 "스피커에서 소리가 나느냐"만 봅니다.

소리가 안 나면: `alsamixer` → `F6` 으로 USB 카드 선택 → `M` 으로 음소거 해제 → 볼륨 올리기.

---

## 3. 녹음 → 변환 → 출력 한 번에 (시연용)

**시연 때 쓰는 건 이거 하나입니다.** 마이크와 스피커를 **둘 다 꽂아 둡니다.** 서로 다른 USB 카드라 중간에 뽑았다 꽂을 일이 없습니다.

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | `bash 06_demo.sh 5` ← **끝의 숫자가 녹음 초** |
| `{결과}` | `test_rec/demo.wav` (녹음) · `test_rec/demo_say.wav` (출력) |

```bash
cd /home/kit/00_TEST/LLM
bash 06_demo.sh 5
```

숫자를 빼면 4초입니다.

### 화면 읽는 법

```
------------------------------------------------------------
mic    : plughw:CARD=Device,DEV=0          <- 마이크 카드
spk    : plughw:CARD=UACDemoV10,DEV=0      <- 스피커 카드
model  : ggml-base.bin   flags: -ng -ac 450 -bo 1 -bs 1 -nf -t 6
prompt : stt_prompt.txt                    <- 프롬프트가 붙었다는 증거
         14 sentences, 157 chars, ~154-227 tok of 224 max
         갈림길에서 왼쪽으로 꺾었어. 목적지까지 반쯤 왔어. ...
------------------------------------------------------------
>> SPEAK NOW -- 5s, mic 10-15 cm away     <- 이 줄이 뜨면 바로 말합니다
>> recorded (plughw:CARD=Device,DEV=0)
   level : peak 8154/32767  rms 4210  ok   <- rms 3000 이상이어야 합니다
------------------------------------------------------------
 heard : 텐트 안에서 버너 켜도 돼           <- 인식 결과
 STT 1510 ms   TTS 62 ms   rec-end -> first sound 1621 ms   budget 2000 ms   OK
 wav   : /home/kit/00_TEST/LLM/test_rec/demo.wav
------------------------------------------------------------
```

**`prompt :` 줄이 안 보이면 프롬프트가 안 붙은 것입니다.** 0-1로 돌아가 `stt_prompt.txt` 가 있는지 확인하세요.

**`rec-end -> first sound`** 가 사용자가 실제로 기다리는 시간입니다. 목표는 2000 ms 이내이고, 넘으면 `OVER` 가 뜹니다. 판정은 **20회 중 최댓값**으로 봅니다 — 실행마다 최대 1.7배까지 흔들려서 `[실측]` 한 번 잘 나온 걸로 통과라고 하면 안 됩니다.

### 인식 결과 대신 정해진 문장을 읽게 하기

시연에서 "질문 → 검수된 카드 응답" 장면을 찍을 때 씁니다.

```bash
cd /home/kit/00_TEST/LLM
bash 06_demo.sh 5 --say "귀환 권고 시각에 도달했습니다. 베이스캠프 경로를 화면에서 확인하세요."
```

녹음과 인식은 그대로 하고, **스피커로는 `--say` 문장만** 나갑니다.

> 어떤 카드를 읽을지는 아직 **사람이 고릅니다.** 키워드 게이트(미결 #9)가 아직 없어서 스크립트가 자동으로 고르는 척하지 않습니다.

### 그 밖의 옵션

| 옵션 | 언제 쓰나 |
|---|---|
| `--keep` | 녹음본을 덮어쓰지 않고 번호를 붙여 남깁니다 |
| `--no-prompt` | 프롬프트를 끄고 한 번 찍어 봅니다. A/B 비교용 |

```bash
cd /home/kit/00_TEST/LLM
bash 06_demo.sh 5 --keep
```

---

## 4. 녹음본 이름 바꾸기 / 남기기

기본값은 **덮어쓰기**입니다. `01_record.sh` 는 항상 `rec.wav`, `06_demo.sh` 는 항상 `demo.wav` 에 씁니다. 다음 녹음을 하면 사라집니다.

### 방법 1 — 녹음한 뒤 이름 바꾸기 (가장 확실)

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{실행}` | 녹음 → `mv` 두 줄 |

```bash
cd /home/kit/00_TEST/LLM
bash 01_record.sh 5
mv test_rec/rec.wav test_rec/버너_01.wav
```

`06_demo.sh` 로 녹음했으면 원본 이름이 `demo.wav` 입니다.

```bash
cd /home/kit/00_TEST/LLM
mv test_rec/demo.wav test_rec/버너_01.wav
```

이름을 바꾼 파일은 다시 들을 수도, 다시 변환할 수도 있습니다.

```bash
bash 02_play.sh test_rec/버너_01.wav
bash 03_echo.sh test_rec/버너_01.wav
```

### 방법 2 — `--keep` 으로 자동 번호

`06_demo.sh` 에만 있습니다. 실행할 때마다 번호가 하나씩 올라갑니다.

```bash
cd /home/kit/00_TEST/LLM
bash 06_demo.sh 5 --keep
```

```
test_rec/demo_0001.wav       <- 녹음한 내 목소리
test_rec/demo_0001_say.wav   <- 스피커로 나간 소리
test_rec/demo_0002.wav
```

### 방법 3 — 21문장 평가 세트

벤치용 21개 녹음은 처음부터 번호가 붙어 저장되므로 이름을 바꿀 필요가 없습니다.

```bash
cd /home/kit/00_TEST/LLM
bash 04_record_set.sh 4
```

`{결과}` `test_rec/ko_1.wav` ~ `test_rec/ko_21.wav`
특정 번호만 다시 녹음하려면 뒤에 번호를 붙입니다 — `bash 04_record_set.sh 4 13`

### 저장 위치 정리

| 파일 | 만든 스크립트 | 덮어쓰나 |
|---|---|---|
| `test_rec/rec.wav` | `01_record.sh` | **예** |
| `test_rec/demo.wav` · `demo_say.wav` | `06_demo.sh` | **예** |
| `test_rec/demo_0001.wav` … | `06_demo.sh --keep` | 아니오 |
| `test_rec/ko_1.wav` ~ `ko_21.wav` | `04_record_set.sh` | 같은 번호만 |
| `test_rec/echo.wav` | `03_echo.sh` | **예** |

---

## 5. 인식용 프롬프트 바꾸기

발음이 뭉개지는 단어(`버너`→`번호` 등)를 살리려고 미리 넣어 둔 문장입니다. **파일에 들어 있어서 한 번 넣으면 계속 적용됩니다.** 매번 타이핑할 필요가 없습니다.

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/LLM` |
| `{파일}` | `/home/kit/00_TEST/LLM/stt_prompt.txt` |

```bash
cd /home/kit/00_TEST/LLM
nano stt_prompt.txt
```

- `#` 로 시작하는 줄은 설명이라 무시됩니다. **아래쪽 14줄만 실제로 쓰입니다.**
- 한 줄에 한 문장, 마침표로 끝냅니다. 쉼표 단어 나열은 넣지 마세요 — 출력 형식까지 따라 합니다 `[실측]`.
- **줄 순서에 의미가 있습니다.** 중요한 단어일수록 아래쪽에 둡니다.
- 저장하고 나오면 다음 실행부터 바로 적용됩니다. 재시작할 것 없습니다.

시연 도중 프롬프트가 엉뚱한 단어를 만들어 내면 짧은 버전으로 내려갑니다.

```bash
cd /home/kit/00_TEST/LLM
WHISPER_PROMPT_FILE=stt_prompt_s.txt bash 06_demo.sh 5
```

아예 끄고 한 번 보려면 `bash 06_demo.sh 5 --no-prompt`.

---

## 6. 안 될 때

| 증상 | 원인 | 할 일 |
|---|---|---|
| `$'\r': command not found` | 윈도우 줄바꿈 | 0-2 의 `sed` |
| `FAIL: no USB capture device` | 마이크 미인식 | `lsusb` · `arecord -l` 로 확인, 다시 꽂기 |
| `FAIL: no USB playback device` | 스피커 미인식 | `aplay -l` 로 확인. HDMI는 일부러 안 씁니다 |
| `FAIL: silent` / `rms` 낮음 | 게인 0 또는 멀리서 발화 | 0-3 실행, 10~15 cm |
| `FAIL: whisper-cli exited 139` | GPU 경로로 갔다가 메모리 부족 | `-ng` 가 빠진 것. `stt_prompt.sh` 를 원본으로 되돌리세요 |
| `FAIL: empty transcription` | 인식 결과가 빔 | `bash 02_play.sh test_rec/demo.wav` 로 직접 들어 보기. 사람이 못 알아들으면 마이크 문제 |
| 장치가 `busy` | PulseAudio가 잡고 있음 | `fuser -v /dev/snd/*` 로 확인 후 `pulseaudio -k` |
| `prompt :` 줄이 안 보임 | 프롬프트 파일이 없음 | 0-1 로 파일 확인 |

관련 문서: [저장소 README](../../README.md) STT 실행 구성과 실측표 · [stt_prompt.txt](stt_prompt.txt) 머리말
