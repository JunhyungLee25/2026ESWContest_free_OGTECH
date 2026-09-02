
from __future__ import annotations

import json
from pathlib import Path
import unittest

from _support import ROOT  # noqa: F401

from harness import CONFIG_DIR, load_policy
from harness.device_state import estimate_tokens
from harness.guard import ALL_ACTIONS, MAP_ACTIONS_LLM, validate_intent
from harness.intent import load_fewshot
from harness.normalize import load_lexicon
from harness.paths import ensure_co_llm_on_path

ensure_co_llm_on_path()
from ogtech_core import SCENARIO_IDS, RuleRouter  # noqa: E402


class ConfigAssetsTest(unittest.TestCase):
    def test_policy_loads_and_allow_actions_are_map_actions(self) -> None:
        policy = load_policy()
        self.assertEqual(policy["profile"], "demo")
        self.assertTrue(set(policy["intent"]["allow_actions"]).issubset(set(MAP_ACTIONS_LLM)))
        self.assertIn(policy["polish"]["mode"], {"off", "shadow", "speak"})
        self.assertEqual(policy["polish"]["mode"], "off", "시연 프로필은 off — --parallel 1에서 intent 프리픽스 KV 캐시를 보존(2026-08-30 결정)")
        self.assertFalse(policy["intent"]["allow_life_status_readout"])
        self.assertLessEqual(policy["llm"]["timeout_s"], 2.0)
        self.assertEqual(policy["llm"]["temperature"], 0.0)
        self.assertTrue(policy["llm"]["url"].startswith("http://127.0.0.1"))

    def test_intent_schema_enums_match_code(self) -> None:
        schema = json.loads((CONFIG_DIR / "schema_intent.json").read_text(encoding="utf-8"))
        self.assertTrue(schema["strict"])
        props = schema["schema"]["properties"]
        self.assertEqual(tuple(props["scenario_id"]["enum"]), tuple(SCENARIO_IDS))
        self.assertEqual(tuple(props["action"]["enum"]), tuple(ALL_ACTIONS))
        self.assertEqual(schema["schema"]["required"], ["scenario_id", "action"])
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertEqual(set(props), {"scenario_id", "action"}, "좌표·거리·방위·confidence 필드는 스키마에 없어야 한다")
        self.assertNotIn('"number"', json.dumps(schema))
        self.assertNotIn('"integer"', json.dumps(schema))

    def test_classify_schema_matches_legacy_contract(self) -> None:
        schema = json.loads((CONFIG_DIR / "schema_classify.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(schema["schema"]["properties"]["scenario_id"]["enum"]), tuple(SCENARIO_IDS))
        self.assertEqual(schema["schema"]["required"], ["scenario_id"])

    def test_polish_schema_has_no_numeric_fields(self) -> None:
        schema = json.loads((CONFIG_DIR / "schema_polish.json").read_text(encoding="utf-8"))
        lines = schema["schema"]["properties"]["lines"]
        self.assertEqual((lines["minItems"], lines["maxItems"]), (2, 4))
        self.assertLessEqual(lines["items"]["maxLength"], 40)
        self.assertNotIn('"number"', json.dumps(schema))
        self.assertNotIn('"integer"', json.dumps(schema))

    def test_system_prompt_names_every_label_and_action(self) -> None:
        prompt = (CONFIG_DIR / "system_prompt_ko.txt").read_text(encoding="utf-8")
        for label in SCENARIO_IDS:
            self.assertIn(label + "=", prompt)
        for action in ALL_ACTIONS:
            self.assertIn(action, prompt)

    def test_fewshot_rows_are_valid_and_prefix_is_bounded(self) -> None:
        rows = load_fewshot(CONFIG_DIR / "fewshot_intent.jsonl")
        self.assertGreaterEqual(len(rows), 12)
        for row in rows:
            self.assertIn(row["assistant"]["scenario_id"], SCENARIO_IDS)
            self.assertIn(row["assistant"]["action"], ALL_ACTIONS)
            verdict = validate_intent(
                row["assistant"],
                pending_confirmation=bool(row.get("pending")),
                allow_actions=frozenset(MAP_ACTIONS_LLM),
            )
            self.assertIn(verdict.reason, {
                "validated_llm_map_action", "validated_llm_label", "validated_llm_assistant_action",
                "llm_life_label_blocked", "llm_unknown",
            })
        from harness.intent import IntentResolver
        from _support import StubLlmClient

        resolver = IntentResolver(
            StubLlmClient({"scenario_id": "unknown", "action": "none"}),
            system_prompt=(CONFIG_DIR / "system_prompt_ko.txt").read_text(encoding="utf-8"),
            fewshot=rows,
            schema={},
            allow_actions=frozenset(MAP_ACTIONS_LLM),
        )
        prefix = "\n".join(m["content"] for m in resolver.prefix_messages())
        # 휴리스틱 추정(보수적). 실제 토큰 수는 Jetson에서 eval/latency_bench.py가 /tokenize로 잰다.
        self.assertLessEqual(estimate_tokens(prefix), 1400, "프리픽스가 커지면 cold prefill이 길어진다(워밍업 필수) — 실측은 latency_bench")

    def test_lexicon_and_overlay_load(self) -> None:
        rules = load_lexicon()
        self.assertGreaterEqual(len(rules), 3)
        overlay = RuleRouter(CONFIG_DIR / "keyword_rules_demo.yaml")
        self.assertEqual(overlay.refuse_patterns, [], "refuse는 정본 규칙만 결정한다")
        for group in overlay.map_rules + overlay.assistant_rules + overlay.scenario_rules:
            self.assertIn(group["scenario_id"], SCENARIO_IDS)

    def test_demo_script_structure(self) -> None:
        script = json.loads((CONFIG_DIR / "demo_script.json").read_text(encoding="utf-8"))
        ids = [turn["id"] for turn in script["turns"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 10)
        for turn in script["turns"]:
            expect = turn["expect"]
            self.assertIn(expect["scenario_id"], SCENARIO_IDS)
            if expect.get("map_action") is not None:
                self.assertIn(expect["map_action"], MAP_ACTIONS_LLM)
            self.assertIsInstance(turn.get("variants"), list)
        texts = [turn["text"] for turn in script["proactive"]]
        self.assertIn("목적지에 도착하였습니다.", texts)
        self.assertIn("베이스캠프에 도착하였습니다.", texts)

    def test_llama_args_keep_frozen_options(self) -> None:
        text = (CONFIG_DIR / "llama_server.args").read_text(encoding="utf-8")
        for flag in ("--flash-attn on", "--cache-type-k q8_0", "--cache-type-v q8_0", "--cache-reuse 256", "--mlock", "-b 128", "-ub 128"):
            self.assertIn(flag, text)


if __name__ == "__main__":
    unittest.main()
