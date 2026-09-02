# 부록 B — 원시 로그

본문 [`01_main.md`](01_main.md)의 수치가 나온 터미널 출력 전문입니다.
가공하지 않았습니다. 요약 수치는 [`measurements.csv`](measurements.csv)에 있습니다.

수집 환경: `kit@kit-desktop`, Jetson Xavier NX, 2026-08-04.

---

## B.1 오디오 장치

### B.1.1 `plughw` EBUSY 실패

```
kit@kit-desktop:~/scripts$ bash 02_play.sh --tts
(robotic voice is EXPECTED -- espeak-ng is the baseline, not the final engine)
device : plughw:CARD=UACDemoV10,DEV=0
file   : /home/kit/scripts/test_rec/tts.wav
aplay: main:852: audio open error: Device or resource busy
FAIL:
  'Device or resource busy' -> pulseaudio -k
  'Invalid argument'        -> use plughw: not hw:
```

### B.1.2 점유 프로세스 확인 — `controlC*`만 잡혀 있음

```
kit@kit-desktop:~/scripts$ fuser -v /dev/snd/*
                     USER        PID ACCESS COMMAND
/dev/snd/controlC0:  kit        1783 F.... pulseaudio
/dev/snd/controlC1:  kit        1783 F.... pulseaudio
/dev/snd/controlC2:  kit        1783 F.... pulseaudio
kit@kit-desktop:~/scripts$ pgrep -a pulseaudio; pgrep -a pipewire
1783 /usr/bin/pulseaudio --daemonize=no --log-target=journal
```

### B.1.3 싱크 상태 — 전부 SUSPENDED

```
kit@kit-desktop:~/scripts$ pactl list sinks | grep -E 'Name:|State:'
	State: SUSPENDED
	Name: alsa_output.platform-3510000.hda.hdmi-stereo-extra1
	State: SUSPENDED
	Name: alsa_output.platform-sound.analog-stereo
	State: SUSPENDED
	Name: alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344C36313516-00.analog-stereo
```

### B.1.4 종합 확인 — 재현 실패, 정상 재생

```
kit@kit-desktop:~/scripts$ cd ~/scripts; echo "== sinks =="; pactl list short sinks; echo "== pcm holders =="; fuser -v /dev/snd/pcm* 2>&1; echo "== wav =="; ls -l test_rec/tts.wav; echo "== plughw =="; aplay -D plughw:CARD=UACDemoV10,DEV=0 test_rec/tts.wav; echo "exit=$?"; echo "== default =="; aplay -D default test_rec/tts.wav; echo "exit=$?"; echo "== tone =="; speaker-test -D default -c 2 -t sine -f 440 -l 1 >/dev/null 2>&1; echo "exit=$?"
== sinks ==
0	alsa_output.platform-3510000.hda.hdmi-stereo-extra1	module-alsa-card.c	s16le 2ch 44100Hz	SUSPENDED
2	alsa_output.platform-sound.analog-stereo	module-alsa-card.c	s16le 2ch 44100Hz	SUSPENDED
3	alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344C36313516-00.analog-stereo	module-alsa-card.cs16le 2ch 48000Hz	SUSPENDED
== pcm holders ==
== wav ==
-rw-rw-r-- 1 kit kit 251860  8월  4 14:04 test_rec/tts.wav
== plughw ==
Playing WAVE 'test_rec/tts.wav' : Signed 16 bit Little Endian, Rate 22050 Hz, Mono
exit=0
== default ==
Playing WAVE 'test_rec/tts.wav' : Signed 16 bit Little Endian, Rate 22050 Hz, Mono
exit=0
== tone ==
exit=0
```

---

## B.2 빌드

### B.2.1 CUDA 컴파일러 미발견 (nvcc가 PATH에 없음)

```
-- Found CUDAToolkit: /usr/local/cuda/include (found version "11.4.315")
-- CUDA Toolkit found
-- The CUDA compiler identification is unknown
CMake Error at ggml/src/ggml-cuda/CMakeLists.txt:59 (enable_language):
  No CMAKE_CUDA_COMPILER could be found.

  Tell CMake where to find the compiler by setting either the environment
  variable "CUDACXX" or the CMake cache entry CMAKE_CUDA_COMPILER to the full
  path to the compiler, or to the compiler name if it is in the PATH.


-- Configuring incomplete, errors occurred!
```

### B.2.2 nvcc 확인 및 PATH 추가

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ls -l /usr/local/cuda/bin/nvcc; which nvcc
-rwxr-xr-x 1 root root 4715896 10월 24  2022 /usr/local/cuda/bin/nvcc
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc && echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc && source ~/.bashrc
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ nvcc --version
nvcc: NVIDIA (R) Cuda compiler driver
Copyright (c) 2005-2022 NVIDIA Corporation
Built on Sun_Oct_23_22:16:07_PDT_2022
Cuda compilation tools, release 11.4, V11.4.315
Build cuda_11.4.r11.4/compiler.31964100_0
```

### B.2.3 ARM 플래그가 무시된 구성 (`GGML_NATIVE` 기본 ON)

`-DGGML_CPU_ARM_ARCH=armv8.2-a+fp16+dotprod`를 줬는데도 native 경로가 이겼습니다.

```
CMake Warning at ggml/src/ggml-cpu/CMakeLists.txt:138 (message):
  ARM -march/-mcpu not found, -mcpu=native will be used

-- Performing Test GGML_MACHINE_SUPPORTS_dotprod - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_nodotprod - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_i8mm - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_noi8mm - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_sve - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_nosve - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_sme - Failed
-- Performing Test GGML_MACHINE_SUPPORTS_nosme - Failed
-- Checking for ARM features using flags:
--   -mcpu=native
-- Performing Test HAVE_DOTPROD - Failed
-- Performing Test HAVE_SVE - Failed
-- Performing Test HAVE_MATMUL_INT8 - Failed
-- Performing Test HAVE_FMA - Success
-- Performing Test HAVE_FP16_VECTOR_ARITHMETIC - Failed
-- Performing Test HAVE_SME - Failed
-- Adding CPU backend variant ggml-cpu: -mcpu=native
```

`dotprod`와 `nodotprod`가 **둘 다 실패**한 것이 단서입니다. 서로 반대인 두 옵션이 동시에
실패한다는 것은 기능 지원 여부가 아니라 `-mcpu=native` 자체가 Carmel을 인식하지 못한다는 뜻입니다.

### B.2.4 `GGML_NATIVE=OFF` 동반 시 성공

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ cd ~/safeaid_ai/stt/whisper.cpp && rm -rf build && cmake -B build -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=72 -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+fp16+dotprod
...
-- Checking for ARM features using flags:
--   -march=armv8.2-a+fp16+dotprod
-- Performing Test HAVE_DOTPROD - Success
-- Performing Test HAVE_SVE - Failed
-- Performing Test HAVE_MATMUL_INT8 - Failed
-- Performing Test HAVE_FMA - Success
-- Performing Test HAVE_FP16_VECTOR_ARITHMETIC - Success
-- Performing Test HAVE_SME - Failed
-- Adding CPU backend variant ggml-cpu: -march=armv8.2-a+fp16+dotprod
-- Found CUDAToolkit: /usr/local/cuda/targets/aarch64-linux/include (found version "11.4.315")
-- CUDA Toolkit found
-- The CUDA compiler identification is NVIDIA 11.4.315 with host compiler GNU 9.4.0
-- Check for working CUDA compiler: /usr/local/cuda/bin/nvcc - skipped
-- Using CMAKE_CUDA_ARCHITECTURES=72 CMAKE_CUDA_ARCHITECTURES_NATIVE=72-real
-- Could NOT find NCCL (missing: NCCL_LIBRARY NCCL_INCLUDE_DIR)
-- Warning: NCCL not found, performance for multiple CUDA GPUs will be suboptimal
-- CUDA host compiler is GNU 9.4.0
-- Including CUDA backend
-- ggml version: 0.18.0
-- ggml commit:  64d57d3d
-- Configuring done (17.9s)
-- Generating done (0.6s)
-- Build files have been written to: /home/kit/safeaid_ai/stt/whisper.cpp/build
```

### B.2.5 모델 다운로드 — curl 버전 문제

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ bash ./models/download-ggml-model.sh small
Downloading ggml model small from 'https://huggingface.co/ggerganov/whisper.cpp' ...
curl: option --retry-all-errors: is unknown
curl: try 'curl --help' or 'curl --manual' for more information
Failed to download ggml model small
Please try again later or download the original Whisper model files and convert them yourself.

kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ cd ~/safeaid_ai/stt/whisper.cpp && sed -i 's/ --retry-all-errors//' models/download-ggml-model.sh && bash ./models/download-ggml-model.sh small
Downloading ggml model small from 'https://huggingface.co/ggerganov/whisper.cpp' ...
100  465M  100  465M    0     0  9631k      0  0:00:49  0:00:49 --:--:-- 10.5M
Done! Model 'small' saved in '/home/kit/safeaid_ai/stt/whisper.cpp/models/ggml-small.bin'
```

### B.2.6 VAD 모델

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ bash ./models/download-vad-model.sh silero-v5.1.2
Downloading ggml model silero-v5.1.2 from 'https://huggingface.co/ggml-org/whisper-vad' ...
ggml-silero-v5.1.2.bin  100%[=====>] 864.35K  1.69MB/s    in 0.5s
Done! Model 'silero-v5.1.2' saved in '/home/kit/safeaid_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin'
```

---

## B.3 영어 샘플 기준선 (jfk.wav, 11초)

### B.3.1 최초 실행 — 클럭 미고정, 기본 옵션

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ cd ~/safeaid_ai/stt/whisper.cpp && time ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav
ggml_cuda_init: found 1 CUDA devices (Total VRAM: 6832 MiB):
  Device 0: Xavier, compute capability 7.2, VMM: yes, VRAM: 6832 MiB
whisper_init_from_file_with_params_no_state: loading model from 'models/ggml-small.bin'
whisper_init_with_params_no_state: use gpu    = 1
whisper_init_with_params_no_state: flash attn = 1
whisper_init_with_params_no_state: gpu_device = 0
whisper_init_with_params_no_state: dtw        = 0
whisper_init_with_params_no_state: devices    = 2
whisper_init_with_params_no_state: backends   = 2
whisper_model_load: loading model
whisper_model_load: n_vocab       = 51865
whisper_model_load: n_audio_ctx   = 1500
whisper_model_load: n_audio_state = 768
whisper_model_load: n_audio_head  = 12
whisper_model_load: n_audio_layer = 12
whisper_model_load: n_text_ctx    = 448
whisper_model_load: n_text_state  = 768
whisper_model_load: n_text_head   = 12
whisper_model_load: n_text_layer  = 12
whisper_model_load: n_mels        = 80
whisper_model_load: ftype         = 1
whisper_model_load: qntvr         = 0
whisper_model_load: type          = 3 (small)
whisper_model_load: adding 1608 extra tokens
whisper_model_load: n_langs       = 99
whisper_model_load:        CUDA0 total size =   487.01 MB
whisper_model_load: model size    =  487.01 MB
whisper_backend_init_gpu: device 0: CUDA0 (type: 2)
whisper_backend_init_gpu: found GPU device 0: CUDA0 (type: 2, cnt: 0)
whisper_backend_init_gpu: using CUDA0 backend
whisper_init_state: kv self size  =   18.87 MB
whisper_init_state: kv cross size =   56.62 MB
whisper_init_state: kv pad  size  =    4.72 MB
whisper_init_state: compute buffer (conv)   =   23.38 MB
whisper_init_state: compute buffer (encode) =   33.85 MB
whisper_init_state: compute buffer (cross)  =    6.20 MB
whisper_init_state: compute buffer (decode) =   98.21 MB
read_audio_data: reading audio data from 'samples/jfk.wav' ...
read_audio_data: trying to decode with miniaudio

system_info: n_threads = 4 / 6 | WHISPER : COREML = 0 | OPENVINO = 0 | CUDA : CPU : NEON = 1 | ARM_FMA = 1 | FP16_VA = 1 | DOTPROD = 1 | OPENMP = 1 | REPACK = 1 |

main: processing 'samples/jfk.wav' (176000 samples, 11.0 sec), 4 threads, 1 processors, 5 beams + best of 5, lang = en, task = transcribe, timestamps = 1 ...


[00:00:00.000 --> 00:00:11.000]   And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.

whisper_print_timings:     load time =  3168.22 ms
whisper_print_timings:     fallbacks =   0 p /   0 h
whisper_print_timings:      mel time =   105.95 ms
whisper_print_timings:   sample time =   265.07 ms /   144 runs (     1.84 ms per run)
whisper_print_timings:   encode time = 18173.26 ms /     1 runs ( 18173.26 ms per run)
whisper_print_timings:   decode time =     0.00 ms /     1 runs (     0.00 ms per run)
whisper_print_timings:   batchd time =   742.65 ms /   142 runs (     5.23 ms per run)
whisper_print_timings:   prompt time =     0.00 ms /     1 runs (     0.00 ms per run)
whisper_print_timings:    total time = 22756.75 ms

real	0m24.606s
user	0m3.969s
sys	0m3.507s
```

`user + sys = 7.5초`인데 벽시계가 24.6초 — CPU 대기가 아니라 GPU가 느린 상태입니다.

### B.3.2 전력 모드 확인 및 클럭 고정

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ sudo nvpmodel -q
NV Power Mode: MODE_15W_6CORE
2
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ sudo nvpmodel -m 2 && sudo jetson_clocks && sudo nvpmodel -q
NV Power Mode: MODE_15W_6CORE
2
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ cd ~/safeaid_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 2>&1 | grep -E 'encode time|total time'
whisper_print_timings:   encode time =  2932.86 ms /     1 runs (  2932.86 ms per run)
whisper_print_timings:    total time = 10258.42 ms
```

### B.3.3 웜 런 2회 — `load time`이 캐시로 줄지 않음

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ cd ~/safeaid_ai/stt/whisper.cpp && for i in 1 2; do echo "--- run$i"; ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 2>&1 | grep -E 'load time|mel time|sample time|encode time|batchd time|total time'; done
--- run1
whisper_print_timings:     load time =  1882.76 ms
whisper_print_timings:      mel time =    48.13 ms
whisper_print_timings:   sample time =    61.20 ms /     1 runs (    61.20 ms per run)
whisper_print_timings:   encode time =  2733.64 ms /     1 runs (  2733.64 ms per run)
whisper_print_timings:   batchd time =    28.54 ms /     3 runs (     9.51 ms per run)
whisper_print_timings:    total time =  5223.13 ms
--- run2
whisper_print_timings:     load time =  1894.88 ms
whisper_print_timings:      mel time =    37.66 ms
whisper_print_timings:   sample time =    61.76 ms /     1 runs (    61.76 ms per run)
whisper_print_timings:   encode time =  2443.39 ms /     1 runs (  2443.39 ms per run)
whisper_print_timings:   batchd time =    27.22 ms /     3 runs (     9.07 ms per run)
whisper_print_timings:    total time =  4922.97 ms
```

### B.3.4 축별 조작 4건

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 -nfa 2>&1 | grep -E 'load time|encode time|total time'
whisper_print_timings:     load time =  1965.40 ms
whisper_print_timings:   encode time =  3284.77 ms /     1 runs (  3284.77 ms per run)
whisper_print_timings:    total time =  6237.45 ms

kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 -ac 600 2>&1 | grep -E 'load time|encode time|total time'
whisper_print_timings:     load time =  2006.46 ms
whisper_print_timings:   encode time =  2547.30 ms /     1 runs (  2547.30 ms per run)
whisper_print_timings:    total time =  5180.52 ms

kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-base.bin -f samples/jfk.wav -bo 1 -bs 1 -ac 600 2>&1 | grep -E 'load time|encode time|total time'
whisper_print_timings:     load time =  1669.55 ms
whisper_print_timings:   encode time =  2134.93 ms /     1 runs (  2134.93 ms per run)
whisper_print_timings:    total time =  4172.19 ms

kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-base.bin -f samples/jfk.wav -bo 1 -bs 1 -ac 600 -ng 2>&1 | grep -E 'load time|encode time|total time'
whisper_print_timings:     load time =   381.93 ms
whisper_print_timings:   encode time =  1305.15 ms /     1 runs (  1305.15 ms per run)
whisper_print_timings:    total time =  2219.28 ms
```

---

## B.4 한국어 벤치 — 1차 (오염됨, 참고용)

> **주의**: 이 5건은 (a) 이전 세션의 `jetson_clocks`가 유지된 상태였고
> (b) `tegrastats --logfile` 이어쓰기로 전력 샘플이 누적됐습니다.
> **지연·정확도는 유효, 전력은 무효**입니다.

```
  RESULT  base_cpu     gate 6/6   median total 1922 ms   3.05 mWh/query
      idle 4330 mW  active 7490 mW  peak 8940 mW   CPU-therm 41.5C
  #1 9531.58 / #2 2012.00 / #3 1935.06 / #4 1908.94 / #5 1846.46 / #6 1850.13
  heard: 여기가 어디야 / 해치기 전에 돌아갈 수 있어 / 오늘 밤 얼마나 추워져 /
         이 물을 마셔도 돼 / 텐트 어디에 저야돼 / 이 버섯 먹어도 돼

  RESULT  small_cpu    gate 6/6   median total 5857 ms   5.37 mWh/query
      idle 4381 mW  active 7388 mW  peak 8940 mW   CPU-therm 43.5C
  #1 6204.99 / #2 5925.32 / #3 5787.71 / #4 5720.35 / #5 5765.81 / #6 6289.80
  heard: 여기가 어디야? / 해지기 전에 돌아갈 수 있어 / 오늘 밤 얼마나 추워져 /
         이불 마셔도 돼 / 텐트 어디에 춰야 돼 / 이 버섯 먹어도 돼

  RESULT  base_gpu     gate 6/6   median total 3762 ms   3.24 mWh/query
      idle 4675 mW  active 7005 mW  peak 10891 mW  CPU-therm 42.5C
  #1 8139.92 / #2 3800.61 / #3 3891.56 / #4 3710.68 / #5 3723.88 / #6 3670.16

  RESULT  small_gpu    gate 6/6   median total 4223 ms   2.79 mWh/query
      idle 4623 mW  active 6702 mW  peak 10891 mW  CPU-therm 43.0C
  #1 4249.62 / #2 4505.84 / #3 4195.91 / #4 4172.31 / #5 4295.88 / #6 4160.68

  RESULT  base_cpu_clk gate 6/6   median total 1774 ms   1.92 mWh/query
      idle 4588 mW  active 6755 mW  peak 10891 mW  CPU-therm 44.0C
  #1 8232.47 / #2 1993.10 / #3 1784.27 / #4 1763.18 / #5 1718.35 / #6 1690.64
  Error: /root/.jetsonclocks_conf.txt file not found !
```

마지막 줄이 `--restore` 실패입니다. `--store`를 먼저 하지 않아 복원 지점이 없었고,
그 결과 보드가 클럭 고정 상태로 남았습니다.

---

## B.5 한국어 벤치 — 2차 (깨끗한 측정)

### B.5.1 `base_cpu_noclk` — 클럭 미고정 대조군

```
kit@kit-desktop:~/scripts$ cd ~/scripts && bash 05_bench.sh base_cpu_noclk ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 600 -bo 1 -bs 1
label  : base_cpu_noclk
model  : ggml-base.bin
args   : -ng -ac 600 -bo 1 -bs 1
clocks : as-is

sudo is needed for tegrastats. Caching credentials...
[1/4] idle baseline 15s -- do not touch the machine
[2/4] batch inference (6 utterances) with power logging
  #1 O  total 6452.13 ms  (load 188.30 / encode 1233.22)
      said : 여기가 어디야
      heard: 여기가 어디야
  #2 O  total 1590.72 ms  (load 188.91 / encode 1202.77)
      said : 해 지기 전에 돌아갈 수 있어
      heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 1539.90 ms  (load 186.69 / encode 1180.55)
      said : 오늘 밤 얼마나 추워져
      heard: 오늘 밤 얼마나 추워져
  #4 O  total 1573.24 ms  (load 186.51 / encode 1211.93)
      said : 이 물 마셔도 돼
      heard: 이 물을 마셔도 돼
  #5 O  total 1595.25 ms  (load 186.37 / encode 1206.97)
      said : 텐트 어디에 쳐야 돼
      heard: 텐트 어디에 저야돼
  #6 O  total 1545.12 ms  (load 186.19 / encode 1180.74)
      said : 이 버섯 먹어도 돼
      heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:40.0C GPU-therm:39.0C AUX-therm:38.0C AO-therm:38.5C PMIC-Die:50.0C thermal_fan_est:39.2C
[4/4] power
  idle   4036 mW   active 7149 mW   peak 8128 mW
  wall   16.0 s over 6 queries
  energy 2.31 mWh / query   (active - idle)

  RESULT  base_cpu_noclk gate 6/6   median total 1582 ms   2.31 mWh/query
```

### B.5.2 `base_cpu_burst` — 버스트 클럭

```
kit@kit-desktop:~/scripts$ cd ~/scripts && CLOCKS=1 bash 05_bench.sh base_cpu_burst ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 600 -bo 1 -bs 1
label  : base_cpu_burst
clocks : burst (jetson_clocks on/off)

[2/4] batch inference (6 utterances) with power logging
  #1 O  total 6794.40 ms  (load 189.03 / encode 1188.28)   heard: 여기가 어디야
  #2 O  total 1641.56 ms  (load 185.36 / encode 1254.02)   heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 1585.95 ms  (load 191.30 / encode 1225.53)   heard: 오늘 밤 얼마나 추워져
  #4 O  total 1543.94 ms  (load 197.32 / encode 1174.07)   heard: 이 물을 마셔도 돼
  #5 O  total 1567.35 ms  (load 187.83 / encode 1179.20)   heard: 텐트 어디에 저야돼
  #6 O  total 1512.46 ms  (load 190.20 / encode 1157.62)   heard: 이 버섯 먹어도 돼
Restoring system configuration from /root/.jetsonclocks_conf.txt
[3/4] thermal
  CPU-therm:41.0C GPU-therm:39.5C AUX-therm:38.5C AO-therm:39.0C PMIC-Die:50.0C thermal_fan_est:39.5C
[4/4] power
  idle   4067 mW   active 7164 mW   peak 8249 mW
  wall   16.4 s over 6 queries
  energy 2.35 mWh / query   (active - idle)

  RESULT  base_cpu_burst gate 6/6   median total 1577 ms   2.35 mWh/query
```

### B.5.3 `base_cpu_ac300` — 환각 발생

```
kit@kit-desktop:~/scripts$ cd ~/scripts && CLOCKS=1 bash 05_bench.sh base_cpu_ac300 ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 300 -bo 1 -bs 1
  #1 O  total 12602.26 ms  (load 183.24 / encode 577.98)
      heard: 여기 가 오디야Er
  #2 O  total   984.80 ms  (load 188.94 / encode 591.50)
      heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 10774.75 ms  (load 190.01 / encode 593.05)
      heard: ()] 오늘 밤 얼마 하나 추워줘 builder dayacular 벅 squares.. hm-hein melt Anda!
  #4 O  total   954.16 ms  (load 182.19 / encode 586.84)
      heard: 이 물을 마셔도 돼
  #5 O  total   982.19 ms  (load 188.30 / encode 599.39)
      heard: 텐트 어디에 쳐야 돼
  #6 O  total   956.00 ms  (load 188.37 / encode 603.71)
      heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:42.0C GPU-therm:41.0C AUX-therm:40.0C AO-therm:40.5C PMIC-Die:50.0C thermal_fan_est:40.5C
[4/4] power
  idle   4087 mW   active 7556 mW   peak 8249 mW
  wall   28.9 s over 6 queries
  energy 4.64 mWh / query   (active - idle)

  RESULT  base_cpu_ac300 gate 6/6   median total 983 ms   4.64 mWh/query
```

### B.5.4 `base_gpu_burst` — GPU 대조군

```
kit@kit-desktop:~/scripts$ cd ~/scripts && CLOCKS=1 bash 05_bench.sh base_gpu_burst ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ac 600 -bo 1 -bs 1
  #1 O  total 26049.17 ms  (load 5495.86 / encode 16486.56)   heard: 여기가 어디야?
  #2 O  total  3940.08 ms  (load 1634.56 / encode 2127.97)    heard: 해치기 전에 돌아갈 수 있어
  #3 O  total  3790.78 ms  (load 1598.22 / encode 2044.18)    heard: 오늘 밤 얼마나 추워져
  #4 O  total  3819.27 ms  (load 1577.72 / encode 2087.25)    heard: 이 물을 마셔도 돼
  #5 O  total  3773.20 ms  (load 1565.63 / encode 2037.23)    heard: 텐트 어디에 저야돼
  #6 O  total  3742.59 ms  (load 1582.18 / encode 2014.89)    heard: 이 버섯 먹어도 돼
Restoring system configuration from /root/.jetsonclocks_conf.txt
[3/4] thermal
  CPU-therm:41.5C GPU-therm:41.0C AUX-therm:39.5C AO-therm:40.5C PMIC-Die:50.0C thermal_fan_est:40.4C
[4/4] power
  idle   4101 mW   active 5415 mW   peak 11257 mW
  wall   48.4 s over 6 queries
  energy 2.94 mWh / query   (active - idle)

  RESULT  base_gpu_burst gate 6/6   median total 3805 ms   2.94 mWh/query
```

---

## B.6 반복 루프 대책

### B.6.1 `base_cpu_nf` — fallback 비활성

```
kit@kit-desktop:~/scripts$ cd ~/scripts && bash 05_bench.sh base_cpu_nf ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 600 -bo 1 -bs 1 -nf
  #1 O  total 3785.85 ms  (load 183.16 / encode 1205.63)
      heard: 여기가 어디야? 여기가 어디야? 여기가 어디야? ... (44회 반복)
  #2 O  total 1582.12 ms  (load 185.35 / encode 1198.14)   heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 1554.54 ms  (load 196.04 / encode 1189.69)   heard: 오늘 밤 얼마나 추워져
  #4 O  total 1533.95 ms  (load 182.86 / encode 1166.52)   heard: 이 물을 마셔도 돼
  #5 O  total 1584.40 ms  (load 182.69 / encode 1177.29)   heard: 텐트 어디에 저야돼
  #6 O  total 1550.84 ms  (load 189.90 / encode 1191.28)   heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:39.0C GPU-therm:38.5C AUX-therm:37.0C AO-therm:38.0C PMIC-Die:50.0C thermal_fan_est:38.2C
[4/4] power
  idle   4044 mW   active 7063 mW   peak 8209 mW
  wall   13.3 s over 6 queries
  energy 1.86 mWh / query   (active - idle)

  RESULT  base_cpu_nf  gate 6/6   median total 1568 ms   1.86 mWh/query
```

### B.6.2 `base_cpu_ac450nf` — 정확도 1위

```
kit@kit-desktop:~/scripts$ cd ~/scripts && bash 05_bench.sh base_cpu_ac450nf ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf
  #1 O  total 3362.68 ms  (load 183.34 / encode 871.70)
      heard: 여기가 어디야? 여기가 어디야? ... (44회 반복)
  #2 O  total 1223.65 ms  (load 182.76 / encode 847.44)   heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 1234.59 ms  (load 186.75 / encode 882.53)   heard: 오늘 밤 얼마나 추워져
  #4 O  total 1225.95 ms  (load 187.52 / encode 863.79)   heard: 이 물을 마셔도 돼
  #5 O  total 1274.10 ms  (load 197.18 / encode 883.93)   heard: 텐트 어디에 쳐야 돼
  #6 O  total 1253.56 ms  (load 192.97 / encode 877.61)   heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:39.0C GPU-therm:39.0C AUX-therm:37.0C AO-therm:38.5C PMIC-Die:50.0C thermal_fan_est:38.5C
[4/4] power
  idle   4089 mW   active 6934 mW   peak 8087 mW
  wall   11.2 s over 6 queries
  energy 1.48 mWh / query   (active - idle)

  RESULT  base_cpu_ac450nf gate 6/6   median total 1244 ms   1.48 mWh/query
```

### B.6.3 진단 — `ko_1.wav` 단독, VAD 없음

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-base.bin -f ~/scripts/test_rec/ko_1.wav -l ko -nt -ng -ac 450 -bo 1 -bs 1 -nf -d 2500
...
whisper_model_load:          CPU total size =   147.37 MB
whisper_backend_init_gpu: no GPU found
...
main: processing '/home/kit/scripts/test_rec/ko_1.wav' (80000 samples, 5.0 sec), 4 threads, 1 processors, 1 beams + best of 1, lang = ko, task = transcribe, timestamps = 0 ...


 여기가 어디야? 여기가 어디야? 여기가 어디야? (…44회…) 여기가 어디야?
whisper_print_timings:     load time =   193.30 ms
whisper_print_timings:     fallbacks =   0 p /   0 h
whisper_print_timings:      mel time =    26.69 ms
whisper_print_timings:   sample time =   288.71 ms /     1 runs (   288.71 ms per run)
whisper_print_timings:   encode time =   849.89 ms /     1 runs (   849.89 ms per run)
whisper_print_timings:   decode time =  1820.97 ms /   219 runs (     8.31 ms per run)
whisper_print_timings:   batchd time =    22.33 ms /     4 runs (     5.58 ms per run)
whisper_print_timings:    total time =  3263.08 ms
```

`-d 2500`은 적용되지 않았습니다 — `80000 samples, 5.0 sec`로 전체가 처리됐습니다.
`fallbacks = 0 p / 0 h`이므로 temperature fallback이 아닙니다.

### B.6.4 진단 — VAD 적용

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-base.bin -f ~/scripts/test_rec/ko_1.wav -l ko -nt -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm models/ggml-silero-v5.1.2.bin
...
whisper_full: VAD is enabled, processing speech segments only
whisper_vad_init_from_file_with_params: loading VAD model from 'models/ggml-silero-v5.1.2.bin'
whisper_vad_init_with_params: model type: silero-16k
whisper_vad_init_with_params: model version: 5.1.2
whisper_vad_init_with_params:          CPU total size =     0.88 MB
whisper_vad_segments_from_samples: detecting speech timestamps in 80000 samples
whisper_vad_detect_speech_no_reset: n_chunks: 157
whisper_vad_detect_speech_no_reset: vad time = 124.70 ms processing 80000 samples
whisper_vad_segments_from_probs: Final speech segments after filtering: 1
whisper_vad_segments_from_probs: VAD segment 0: start = 0.83, end = 1.98 (duration: 1.15)
whisper_vad: total duration of speech segments: 1.15 seconds
whisper_vad: Reduced audio from 80000 to 18400 samples (77.0% reduction)

 여기가 어디야?
whisper_print_timings:     load time =   191.32 ms
whisper_print_timings:     fallbacks =   0 p /   0 h
whisper_print_timings:      mel time =    18.08 ms
whisper_print_timings:   sample time =     9.96 ms /     1 runs (     9.96 ms per run)
whisper_print_timings:   encode time =   722.10 ms /     1 runs (   722.10 ms per run)
whisper_print_timings:   decode time =    41.63 ms /     5 runs (     8.33 ms per run)
whisper_print_timings:   batchd time =    21.94 ms /     4 runs (     5.48 ms per run)
whisper_print_timings:    total time =  1201.78 ms
```

**219 runs → 5 runs.** 디코더 반복이 원인임이 확정됩니다.

### B.6.5 진단 — 디코더 측 완화책은 무효

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ ./build/bin/whisper-cli -m models/ggml-base.bin -f ~/scripts/test_rec/ko_1.wav -l ko -nt -ng -ac 450 -bo 1 -bs 1 -nf -mc 0 -sns
...
 여기가 어디야? 여기가 어디야? (…44회…)
whisper_print_timings:     load time =   184.21 ms
whisper_print_timings:     fallbacks =   0 p /   0 h
whisper_print_timings:      mel time =    24.91 ms
whisper_print_timings:   sample time =   341.71 ms /     1 runs (   341.71 ms per run)
whisper_print_timings:   encode time =   766.45 ms /     1 runs (   766.45 ms per run)
whisper_print_timings:   decode time =  1851.71 ms /   219 runs (     8.46 ms per run)
whisper_print_timings:   batchd time =    21.39 ms /     4 runs (     5.35 ms per run)
whisper_print_timings:    total time =  3250.04 ms
```

`-mc 0 -sns`로도 219 runs 그대로입니다. **입력에서 무음을 제거하는 것만 유효합니다.**

---

## B.7 최종 후보

### B.7.1 `base_cpu_vad` — 후보 A

```
kit@kit-desktop:~/scripts$ cd ~/scripts && bash 05_bench.sh base_cpu_vad ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm ~/safeaid_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin
label  : base_cpu_vad
args   : -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm /home/kit/safeaid_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin
clocks : as-is

[2/4] batch inference (6 utterances) with power logging
  #1 O  total 1407.60 ms  (load 182.35 / encode 954.22)   heard: 여기가 어디야?
  #2 O  total 1495.19 ms  (load 178.71 / encode 955.10)   heard: 해치기 전에 돌아갈 수 있어
  #3 O  total 1413.14 ms  (load 179.80 / encode 941.79)   heard: 오늘 밤 얼마나 추워져?
  #4 O  total 1353.21 ms  (load 189.53 / encode 891.13)   heard: 입을 마셔도 돼
  #5 O  total 1361.85 ms  (load 178.93 / encode 869.73)   heard: 텐트 어디에 쳐야 돼
  #6 O  total 1290.49 ms  (load 179.94 / encode 839.15)   heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:36.5C GPU-therm:35.5C AUX-therm:34.5C AO-therm:35.5C PMIC-Die:50.0C thermal_fan_est:35.4C
[4/4] power
  idle   4003 mW   active 6634 mW   peak 7518 mW
  wall   10.0 s over 6 queries
  energy 1.22 mWh / query   (active - idle)

  RESULT  base_cpu_vad gate 6/6   median total 1385 ms   1.22 mWh/query
```

**최댓값 1495 ms, 편차폭 205 ms.** 꼬리가 사라졌습니다.

### B.7.2 `base_cpu_vad_pad` — `-vp 200` 기각

```
kit@kit-desktop:~/scripts$ cd ~/scripts && bash 05_bench.sh base_cpu_vad_pad ~/safeaid_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm ~/safeaid_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin -vp 200
  #1 O  total 1309.38 ms  (load 179.48 / encode 865.92)   heard: 여기가 어디야?
  #2 O  total 1377.66 ms  (load 183.76 / encode 851.64)   heard: 해치기 전에 돌아갈 수 있어.
  #3 O  total 3445.92 ms  (load 182.21 / encode 841.54)
      heard: 오늘 밤 얼마나 추워져 오늘 밤 얼마나 추워져 (…38회 반복…)
  #4 O  total 1335.94 ms  (load 186.27 / encode 876.55)   heard: 이 물을 마셔도 돼
  #5 O  total 1344.56 ms  (load 177.71 / encode 870.93)   heard: 텐트 어디에 쳐야 돼
  #6 O  total 1313.91 ms  (load 185.96 / encode 861.78)   heard: 이 버섯 먹어도 돼
[3/4] thermal
  CPU-therm:37.0C GPU-therm:36.0C AUX-therm:35.0C AO-therm:35.5C PMIC-Die:50.0C thermal_fan_est:36.0C
[4/4] power
  idle   4025 mW   active 7009 mW   peak 8371 mW
  wall   11.8 s over 6 queries
  energy 1.63 mWh / query   (active - idle)

  RESULT  base_cpu_vad_pad gate 6/6   median total 1340 ms   1.63 mWh/query
```

`#4`는 교정됐으나 `#3`에서 반복이 재발했습니다.
**패딩이 세그먼트 가장자리에 무음을 되돌려 놓은 것**입니다.

---

## B.8 측정 하네스 오염 흔적

### B.8.1 `jetson_clocks --restore` 실패

```
Error: /root/.jetsonclocks_conf.txt file not found !
```

### B.8.2 로그 파일 권한 문제

```
05_bench.sh: line 60: /home/kit/scripts/test_rec/_tg_idle.log: Permission denied
05_bench.sh: line 61: /home/kit/scripts/test_rec/_tg_run.log: Permission denied
```

`tegrastats`가 root로 파일을 만들어, 일반 권한 truncate가 실패했습니다.
결과적으로 이전 실행의 전력 샘플이 그대로 누적됐습니다.

### B.8.3 `--store` Y/N 프롬프트

```
  storing current clock state for restore...
File /root/.jetsonclocks_conf.txt already exists. Can I overwrite it? Y/N:
```

`[ -f /root/... ]`를 일반 권한으로 평가하면 `/root`를 stat할 수 없어 항상 "없음"이 됩니다.
`sudo test -f`로 고쳤습니다.

### B.8.4 클럭 스냅샷 저장

```
kit@kit-desktop:~/safeaid_ai/stt/whisper.cpp$ sudo jetson_clocks --store && cat /root/.jetsonclocks_conf.txt | head -3
Storing system configuration in /root/.jetsonclocks_conf.txt
cat: /root/.jetsonclocks_conf.txt: Permission denied
```

저장은 성공했습니다. `cat`에 `sudo`가 없어 읽기만 막힌 것입니다.
