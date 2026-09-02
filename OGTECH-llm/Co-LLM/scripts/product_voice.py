#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OGTECH 제품 음성 1회 실행기.

마이크 또는 --text 입력을 안전 라우터에 보내고, 열거형 지도 명령을 실행한 뒤,
검수 카드·코드 계산값만 TTS로 읽는다. STT와 TTS는 항상 순차 로드·언로드한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from queue import Full, Queue
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
import engines as E  # noqa: E402
from product_assistant import (  # noqa: E402
    MapApiClient,
    MapApiError,
    ProductAssistant,
    VerifiedResponseStore,
)
from pipeline_gate import exclusive_pipeline  # noqa: E402
from tts_pipeline import TtsPipeline  # noqa: E402

# 시연 하네스(OGTECH-llm/harness): STT 사전 → 정본 규칙 → 시연 오버레이 → LLM 의도({라벨, 지도 동작}) → guard.
# 하네스 구성이 없거나 깨지면 기존 ProductAssistant 경로로 그대로 동작한다.
_LLM_ROOT = str(Path(__file__).resolve().parents[2])
if _LLM_ROOT not in sys.path:
    sys.path.append(_LLM_ROOT)  # 끝에 붙인다 — Co-LLM/config.py 등 기존 모듈을 가리지 않도록
try:
    from harness import DemoAssistant, build_harness  # noqa: E402

    HARNESS = build_harness()  # OGTECH-llm/config/harness_policy.json
    HARNESS_ERROR = None
except Exception as exc:  # noqa: BLE001 — 하네스 실패가 음성 경로를 막으면 안 된다
    HARNESS = None
    HARNESS_ERROR = f"{type(exc).__name__}: {exc}"


_SYNTHESIS_DONE = object()


def _queue_put(queue, item, stop_event) -> bool:
    """소비 루프가 죽어 큐가 가득 차도 영구 블록하지 않는다. 중단 신호면 False."""
    while not stop_event.is_set():
        try:
            queue.put(item, timeout=0.5)
            return True
        except Full:
            continue
    return False


def _produce_sentences(pipeline, text, output_wav, queue, timing, stop_event):
    started = time.monotonic()
    try:
        for result in pipeline.synthesize_sentences(text, output_wav):
            if stop_event.is_set() or not _queue_put(queue, result, stop_event):
                return
    except BaseException as exc:  # 메인 스레드에서 같은 오류로 종료한다.
        _queue_put(queue, exc, stop_event)
    finally:
        timing["tts_s"] = time.monotonic() - started
        _queue_put(queue, _SYNTHESIS_DONE, stop_event)


def _build_assistant(client: MapApiClient):
    store = VerifiedResponseStore(C.LAST_VERIFIED_RESPONSE_PATH)
    if HARNESS is not None:
        return DemoAssistant(
            client,
            router=HARNESS.router,
            polisher=HARNESS.polisher,
            classifier=E.classify_scenario,  # intent 비활성 시에만 쓰는 구 분류기
            response_store=store,
        )
    print(f"[WARN] 시연 하네스 없이 실행: {HARNESS_ERROR}", file=sys.stderr)
    return ProductAssistant(client, classifier=E.classify_scenario, response_store=store)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 제품 음성 실행기")
    parser.add_argument("--text", help="마이크 대신 사용할 한국어 문장")
    parser.add_argument("--input-wav", help="녹음 대신 STT에 넣을 로컬 WAV")
    parser.add_argument(
        "--release-monotonic-ns",
        type=int,
        help="물리 버튼 release 직후의 time.monotonic_ns 값",
    )
    parser.add_argument("--seconds", type=float, default=C.REC_SECONDS, help="녹음 시간")
    parser.add_argument("--repeat", type=int, default=1, help="실행 횟수")
    parser.add_argument("--stt", default=C.STT_ENGINE, help="STT 엔진")
    parser.add_argument(
        "--tts-order",
        default=",".join(C.TTS_ENGINE_ORDER),
        help="TTS 우선순위. 기본 melotts,piper,espeak",
    )
    parser.add_argument("--map-url", default=C.MAP_API_URL, help="로컬 지도 API")
    parser.add_argument("--no-tts", action="store_true", help="분류·지도·문장 확정까지만 실행")
    parser.add_argument("--no-play", action="store_true", help="WAV를 만들고 재생하지 않음")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로도 출력")
    return parser.parse_args()


def run_once(args: argparse.Namespace, index: int) -> dict[str, object]:
    C.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    input_wav = (
        Path(args.input_wav)
        if args.input_wav
        else C.RESULT_DIR / f"product_rec_{index:04d}.wav"
    )
    output_wav = C.RESULT_DIR / f"product_say_{index:04d}.wav"
    memory_before = E.mem_available_mb()
    if 0 <= memory_before < C.MEM_GATE_MB:
        raise RuntimeError(
            f"MemAvailable {memory_before} MB로 1 GB 게이트를 통과하지 못했습니다"
        )

    if args.text is None and args.input_wav is None:
        print(f"[REC ] {args.seconds:.1f}초 녹음 — 지금 말하세요")
        E.record(input_wav, seconds=args.seconds)
        release_time = time.monotonic()
        with E.make_stt(args.stt) as stt:
            started = time.monotonic()
            heard = stt.transcribe(input_wav)
            stt_s = time.monotonic() - started
    elif args.input_wav is not None:
        if not input_wav.is_file():
            raise FileNotFoundError(f"입력 WAV가 없습니다: {input_wav}")
        now = time.monotonic()
        release_time = (
            now
            if args.release_monotonic_ns is None
            else args.release_monotonic_ns / 1_000_000_000.0
        )
        if release_time > now + 0.1 or now - release_time > 60.0:
            raise ValueError("버튼 release monotonic 시각이 현재 프로세스와 맞지 않습니다")
        with E.make_stt(args.stt) as stt:
            started = time.monotonic()
            heard = stt.transcribe(input_wav)
            stt_s = time.monotonic() - started
    else:
        heard = args.text.strip()
        release_time = time.monotonic()
        stt_s = 0.0
    if not heard:
        raise RuntimeError("음성을 인식하지 못했습니다. 원본 녹음을 확인하세요")

    client = MapApiClient(args.map_url, timeout_s=C.MAP_API_TIMEOUT_S)
    assistant = _build_assistant(client)
    started = time.monotonic()
    result = assistant.handle_text(heard)
    route_s = time.monotonic() - started
    trace = dict(getattr(getattr(assistant, "router", None), "last_trace", None) or {})

    print(f'[STT ] {stt_s:.3f} s  "{heard}"')
    print(
        f"[ROUTE] {result.decision.scenario_id} · {result.decision.source} · "
        f"map={result.decision.map_action or '-'} · {route_s:.3f} s"
    )
    if trace:
        print(f"[HARN] {trace.get('stage')} · {trace.get('reason')} · 입력 {trace.get('normalized')!r}")
    print(f"[CARD] {result.source_id}")
    print(f"[SAY ] {result.speech}")

    tts_s = 0.0
    first_audio_s: float | None = None
    tts_engine = "disabled"
    degraded = False
    if not args.no_tts:
        order = tuple(item.strip() for item in args.tts_order.split(",") if item.strip())
        pipeline = TtsPipeline(engine_order=order)
        sentence_queue = Queue(maxsize=2)
        stop_event = threading.Event()
        timing: dict[str, float] = {}
        producer = threading.Thread(
            target=_produce_sentences,
            args=(pipeline, result.speech, output_wav, sentence_queue, timing, stop_event),
            name="ogtech-tts-producer",
            daemon=True,
        )
        producer.start()
        engines_used: list[str] = []
        segment_count = 0
        try:
            while True:
                item = sentence_queue.get()
                if item is _SYNTHESIS_DONE:
                    break
                if isinstance(item, BaseException):
                    raise item
                speech = item
                segment_count += 1
                if first_audio_s is None:
                    first_audio_s = time.monotonic() - release_time
                if speech.engine not in engines_used:
                    engines_used.append(speech.engine)
                degraded = degraded or speech.degraded
                quality = "DEGRADED" if speech.degraded else "OK"
                print(
                    f"[TTS {segment_count}] {speech.engine} · "
                    f"{speech.metrics.duration_s:.2f} s · {quality}"
                )
                if speech.errors:
                    print("[TTS ] 폴백 사유: " + " | ".join(speech.errors))
                if not args.no_play:
                    E.play(speech.path)
        finally:
            # 재생 실패로 여기서 빠져나가도 생산 스레드가 put 에 영구 블록하지 않게 한다.
            stop_event.set()
            producer.join()
        tts_s = timing.get("tts_s", 0.0)
        tts_engine = ",".join(engines_used)
        print(f"[TTS ] {segment_count}문장 합성 {tts_s:.3f} s")

    return {
        "scenario_id": result.decision.scenario_id,
        "map_action": result.decision.map_action,
        "decision_source": result.decision.source,
        "harness_stage": trace.get("stage"),
        "source_id": result.source_id,
        "stt_s": round(stt_s, 3),
        "route_s": round(route_s, 3),
        "tts_s": round(tts_s, 3),
        "first_audio_s": None if first_audio_s is None else round(first_audio_s, 3),
        "tts_engine": tts_engine,
        "tts_degraded": degraded,
        "speech": result.speech,
    }


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        print("--repeat는 1 이상이어야 합니다", file=sys.stderr)
        return 2
    if args.text is not None and args.input_wav is not None:
        print("--text와 --input-wav는 함께 사용할 수 없습니다", file=sys.stderr)
        return 2
    if args.release_monotonic_ns is not None and args.input_wav is None:
        print("--release-monotonic-ns는 --input-wav와 함께 사용해야 합니다", file=sys.stderr)
        return 2
    try:
        # 시작 전에 로컬 주소 검증을 끝내 외부로 발화가 나갈 가능성을 차단한다.
        MapApiClient(args.map_url, timeout_s=C.MAP_API_TIMEOUT_S)
        rows = []
        for index in range(1, args.repeat + 1):
            if index > 1 and args.text is None:
                input("다음 발화를 시작하려면 Enter를 누르세요...")
            with exclusive_pipeline():
                row = run_once(args, index)
            rows.append(row)
            if args.json:
                print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    except (MapApiError, RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1

    maxima = [row["first_audio_s"] for row in rows if row["first_audio_s"] is not None]
    if maxima:
        print(f"[MAX ] 버튼 해제 상당 시점부터 WAV 준비까지 {max(maxima):.3f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
