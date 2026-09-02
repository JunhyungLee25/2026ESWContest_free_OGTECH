# 03. STT 후보 3안

## 왜 이 3개인가

Xavier NX(aarch64)에서 한국어 STT를 고를 때 실제로 걸리는 축은 3개입니다.

1. **설치가 되는가** — aarch64 휠이 없어서 못 쓰는 라이브러리가 많습니다
2. **경로 B 2.0초 안에 들어가는가** — STT가 이 예산의 대부분을 먹습니다
3. **메모리 0.5 GB 안에 들어가는가** — `docs/00_frozen_decisions.md` §2 STT 온디맨드 예산

세 안을 각각 다른 축에 배치했습니다. 하나가 막히면 다음으로 갑니다.

| | 1안 whisper.cpp | 2안 sherpa-onnx | 3안 faster-whisper |
|---|---|---|---|
| 강점 | **설치 확실성** | **속도·메모리** | **정확도** |
| 모델 | `ggml-small` (466 MB) | 한국어 zipformer int8 | `small` int8_float16 |
| 한국어 학습 | 다국어(한국어 포함) | **KsponSpeech 한국어 전용** | 다국어(한국어 포함) |
| 실행 | C++ CLI 서브프로세스 | Python 인프로세스 | Python 인프로세스 |
| GPU | CUDA 빌드 지원 | CPU (ONNX Runtime) | CUDA |
| 메모리 [추정] | ~0.5 GB | **~0.2 GB** | ~0.5 GB |
| 언로드 | **프로세스 종료 = 자동** | 명시적 `del` + `gc` | 명시적 `del` + `gc` |
| aarch64 리스크 | **없음** | 낮음 | **높음** (ctranslate2 휠) |

> **CLI 서브프로세스가 이 프로젝트에 유리한 이유**
> `docs/00_frozen_decisions.md` §2는 "STT·TTS는 온디맨드 로드/언로드이며 동시에 올리지 않습니다"를 요구합니다.
> whisper.cpp처럼 CLI를 호출하는 방식은 **프로세스가 끝나면 메모리가 커널에 반납되므로
> 이 규칙을 공짜로 지킵니다.** 파이썬 인프로세스 방식은 `del model; gc.collect()`를 직접
> 불러야 하고, 그래도 아레나가 남아 실제 반납이 안 될 수 있습니다.

---

## 1안 — whisper.cpp + ggml-small (기본)

설치는 [`02_install_a_to_z.md`](02_install_a_to_z.md) 2단계 그대로입니다.

### 크기를 조절하는 순서

**모델을 바로 키우지 마세요.** 순서가 있습니다.

```
느리다  -> ① CUDA 빌드 확인 (jtop에서 GPU 사용률)
        -> ② 전원 모드 확인:  sudo nvpmodel -m 0 && sudo jetson_clocks
        -> ③ 그래도 느리면 small -> base
못 알아듣는다 -> ① 마이크 거리 20 cm 이내, alsamixer 캡처 게인
             -> ② 녹음 wav를 직접 들어 본다 (사람이 못 알아들으면 모델도 못 합니다)
             -> ③ 그래도 안 되면 small -> medium (메모리 예산 초과 주의)
```

### 양자화 모델로 크기 줄이기

```bash
cd ~/ogtech_ai/stt/whisper.cpp
bash ./models/download-ggml-model.sh small-q5_1    # 약 190 MB
```

`config.py`:

```python
WHISPER_CPP_MODEL = "~/ogtech_ai/stt/whisper.cpp/models/ggml-small-q5_1.bin"
```

### 알려진 함정

- 바이너리 이름이 버전에 따라 `main` ↔ `whisper-cli`로 바뀝니다. `config.py`에서 지정합니다.
- 입력은 **16 kHz 모노 wav만** 받습니다. 다른 포맷이면 조용히 이상한 결과가 나옵니다.
  `voice_loop.py`는 `arecord`로 16 kHz 모노를 직접 녹음하므로 문제없습니다.
- 무음 구간에서 `[음악]`, `.`, `감사합니다` 같은 환각이 나옵니다. 이건 whisper 계열 공통 특성입니다.
  실제 제품에서는 **버튼을 누르는 동안만 녹음**하므로 완화됩니다.

---

## 2안 — sherpa-onnx 한국어 zipformer (속도·메모리)

한국어 KsponSpeech로 **한국어만** 학습된 모델입니다 [출처: k2-fsa/sherpa-onnx 모델 저장소].
whisper처럼 다국어를 나눠 갖지 않으므로, 같은 크기에서 한국어 성능이 더 나올 가능성이 있습니다 [미검증].

### 설치

```bash
source ~/ogtech_ai/venv/bin/activate
pip install sherpa-onnx numpy
```

`sherpa-onnx`는 aarch64 휠을 배포합니다 [미검증 — 실패하면 아래 소스 빌드].

```bash
# 휠이 없을 때만
pip install --no-binary :all: sherpa-onnx
```

### 모델 받기 — 오프라인(비스트리밍) 버전을 먼저 쓰세요

```bash
cd ~/ogtech_ai/stt
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-zipformer-korean-2024-06-24.tar.bz2
tar xvf sherpa-onnx-zipformer-korean-2024-06-24.tar.bz2
ls sherpa-onnx-zipformer-korean-2024-06-24/
# encoder-epoch-99-avg-1.int8.onnx  decoder-...onnx  joiner-...int8.onnx  tokens.txt
```

> **스트리밍 모델(`streaming-zipformer-korean-2024-06-16`)을 먼저 쓰지 마세요.**
> "한국어 스트리밍 모델이 빈 문자열만 반환한다"는 이슈가 올라와 있습니다
> ([k2-fsa/sherpa-onnx#2886](https://github.com/k2-fsa/sherpa-onnx/issues/2886)) [출처].
> 우리 사용 방식은 버튼을 누르는 동안만 녹음하는 **푸시투토크**라서 스트리밍이 꼭 필요하지 않습니다.
> 오프라인 모델로 먼저 정확도를 확인하고, 지연이 더 필요할 때 스트리밍을 재시도하세요.

### 교체

`config.py`:

```python
STT_ENGINE = "sherpa_onnx"
SHERPA_DIR = "~/ogtech_ai/stt/sherpa-onnx-zipformer-korean-2024-06-24"
```

### 알려진 함정

- CPU 전용입니다. 스레드 수(`SHERPA_THREADS`)가 성능을 좌우합니다. Xavier NX는 6코어이므로 4로 시작합니다.
- 파일 이름에 epoch/avg 숫자가 박혀 있습니다. 압축을 푼 뒤 실제 파일명을 확인하고
  `config.py`의 `SHERPA_ENCODER/DECODER/JOINER`를 맞추세요.
- 숫자·영어 고유명사에 약할 수 있습니다 [미검증]. 실제 발화 문장으로 확인하세요.

---

## 3안 — faster-whisper (정확도)

CTranslate2 백엔드라 같은 whisper 모델을 더 빠르게 돌립니다. 정확도가 부족할 때
`medium`까지 올려도 감당되는 유일한 선택지입니다.

### 설치

```bash
source ~/ogtech_ai/venv/bin/activate
pip install faster-whisper
python -c "import ctranslate2; print(ctranslate2.__version__)"
```

**이 단계가 aarch64에서 실패할 수 있습니다** [미검증]. CTranslate2가 aarch64 휠을 안 올린
버전이면 소스 빌드로 넘어가는데, 시간이 오래 걸립니다. 그 경우 판단:

- 배관 검증이 목적이면 → **1안으로 돌아가세요.** 여기 시간을 쓸 이유가 없습니다
- 정확도 때문에 꼭 필요하면 → `dusty-nv/jetson-containers`의 사전 빌드 컨테이너를 검토합니다

### 교체

`config.py`:

```python
STT_ENGINE = "faster_whisper"
FW_MODEL = "small"              # tiny | base | small | medium
FW_DEVICE = "cuda"              # 실패하면 "cpu"
FW_COMPUTE = "float16"          # cpu일 때는 "int8"
```

### 알려진 함정

- 첫 실행에서 모델을 인터넷에서 받습니다. **Jetson이 오프라인이면 실패합니다.**
  네트워크가 있을 때 미리 받아 두고 `~/.cache/huggingface`를 유지하세요.
  최종 제품은 오프라인 동작이 절대 조건입니다 (`docs/00_frozen_decisions.md` §3 안전 계약 9).
- `beam_size=1`, `vad_filter=False`로 시작하세요. 기본값(beam 5)은 느립니다.
- `device="cuda"`가 cuDNN 버전 때문에 죽는 경우가 흔합니다. 기능 확인은 `cpu`+`int8`로 먼저 합니다.

---

## 어떤 증상이면 어디로 가는가

```
경로 B가 2.0초를 넘는다
 └─ STT가 1.5초 이상 -> 2안(sherpa-onnx) 시도.  CPU만으로 더 빠를 가능성이 높음
 └─ STT는 1초 이하인데 총합 초과 -> 04_tts_candidates.md 로

받아쓰기가 틀린다
 └─ 녹음 wav를 사람이 들어서 알아들을 수 있나?
     ├─ 아니오 -> 마이크 문제.  게인/거리/전원.  STT 바꿔도 소용없음
     └─ 예    -> 2안(한국어 전용 학습) 시도 -> 그래도 안 되면 3안 medium

설치가 안 된다
 └─ 1안으로 돌아갑니다.  1안은 aarch64에서 실패 사례가 없습니다
```

## 기록할 것

각 안에 대해 [`05_test_log.md`](05_test_log.md)의 STT 표를 채웁니다.
**콜드 런과 웜 런을 구분해서** 적으세요. 첫 호출은 모델 로드가 섞여 있어 5~10배 느립니다.
