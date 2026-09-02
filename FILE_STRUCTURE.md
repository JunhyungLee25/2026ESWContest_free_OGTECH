# 전체 파일 구조

저장소에 포함된 파일 272개를 경로 순서대로 정리했습니다.

```text
2026ESWContest_free_OGTECH/
├─ .github/
│  └─ workflows/
│     └─ repository-tests.yml
├─ assets/
│  ├─ src/
│  │  ├─ d0_system_overview.drawio.xml
│  │  ├─ d1_dual_power_layers.svg
│  │  ├─ d2_response_path.svg
│  │  ├─ d3_backend_modules.svg
│  │  ├─ d4_layer_structure.svg
│  │  └─ README.md
│  ├─ .gitkeep
│  ├─ 01_basecamp_start.png
│  ├─ 02_destination_route.png
│  ├─ 03_destination_arrived.png
│  ├─ 04_daylight_warning.png
│  ├─ 05_basecamp_return_route.png
│  ├─ 06_basecamp_arrived.png
│  ├─ 07_checkpoint_saved.png
│  ├─ 08_night_mode.png
│  ├─ 09_video_screen_jetson.png
│  ├─ d0_system_overview.png
│  ├─ d1_dual_power_layers.png
│  ├─ d2_response_path.png
│  ├─ d3_backend_modules.png
│  └─ d4_layer_structure.png
├─ OGTECH-backend/
│  ├─ config/
│  │  ├─ keyword_rules.yaml
│  │  └─ survival_cards.json
│  ├─ core/
│  │  ├─ __init__.py
│  │  └─ ogtech_core.py
│  ├─ tests/
│  │  ├─ test_app.py
│  │  └─ test_vendor_sync.py
│  ├─ .gitattributes
│  ├─ .gitignore
│  ├─ app.py
│  └─ README.md
├─ OGTECH-embedded/
│  ├─ Core/
│  │  ├─ Inc/
│  │  │  ├─ jetson_link.h
│  │  │  ├─ main.h
│  │  │  ├─ sensor_hub.h
│  │  │  └─ stm32h7xx_it.h
│  │  └─ Src/
│  │     ├─ jetson_link.c
│  │     ├─ main.c
│  │     ├─ sensor_hub.c
│  │     ├─ stm32h7xx_hal_msp.c
│  │     └─ stm32h7xx_it.c
│  ├─ tests/
│  │  └─ test_stm32_jetson.py
│  └─ README.md
├─ OGTECH-frontend/
│  ├─ MAP/
│  │  ├─ jetson/
│  │  │  ├─ user/
│  │  │  │  ├─ ogtech-kiosk.service
│  │  │  │  └─ ogtech-map.service
│  │  │  ├─ kiosk_fullscreen_guard.py
│  │  │  ├─ map.env.example
│  │  │  ├─ ogtech-kiosk.service
│  │  │  ├─ ogtech-map.service
│  │  │  ├─ ogtech-power-manager.service
│  │  │  ├─ power_control.py
│  │  │  ├─ start-kiosk.sh
│  │  │  └─ start-map.sh
│  │  ├─ kiosk/
│  │  │  ├─ auto_demo_ssh.sh
│  │  │  ├─ build_video_map_data.py
│  │  │  ├─ daylight_detail.wav
│  │  │  ├─ destination_arrived.wav
│  │  │  ├─ destination_confirmed.wav
│  │  │  ├─ destination_set.wav
│  │  │  ├─ poi_catalog.json
│  │  │  ├─ README.md
│  │  │  ├─ return_to_base.wav
│  │  │  ├─ select.html
│  │  │  ├─ styles.css
│  │  │  ├─ tts_unavailable.wav
│  │  │  ├─ uart_server.py
│  │  │  ├─ video_app.js
│  │  │  ├─ VIDEO_DEMO_2026-08-09.md
│  │  │  ├─ video_map.js
│  │  │  ├─ video_styles.css
│  │  │  └─ video.html
│  │  ├─ runtime/
│  │  │  └─ .gitkeep
│  │  ├─ sample_data/
│  │  │  ├─ air530_replay.nmea
│  │  │  ├─ ATTRIBUTION.md
│  │  │  └─ konkuk_walk.graphml
│  │  ├─ static/
│  │  │  ├─ app.js
│  │  │  ├─ index.html
│  │  │  └─ styles.css
│  │  ├─ TEST_images/
│  │  │  ├─ 01_basecamp_start.png
│  │  │  ├─ 02_destination_route.png
│  │  │  ├─ 03_destination_arrived.png
│  │  │  ├─ 04_daylight_warning.png
│  │  │  ├─ 05_basecamp_return_route.png
│  │  │  ├─ 06_basecamp_arrived.png
│  │  │  ├─ 07_checkpoint_saved.png
│  │  │  └─ 08_night_mode.png
│  │  ├─ tests/
│  │  │  ├─ test_co_alarm.py
│  │  │  ├─ test_gps_api.py
│  │  │  ├─ test_gps_service.py
│  │  │  ├─ test_map_engine.py
│  │  │  ├─ test_navigation_service.py
│  │  │  ├─ test_position_history.py
│  │  │  ├─ test_power_control.py
│  │  │  ├─ test_pressure_contract.py
│  │  │  ├─ test_rtc_navigation_regression.py
│  │  │  ├─ test_solar_service.py
│  │  │  ├─ test_telemetry_service.py
│  │  │  ├─ ui_product_qa.js
│  │  │  └─ ui_video_qa.js
│  │  ├─ .gitignore
│  │  ├─ app.py
│  │  ├─ co_alarm.py
│  │  ├─ GPS_BRINGUP.md
│  │  ├─ gps_service.py
│  │  ├─ map_engine.py
│  │  ├─ MAP_USAGE.md
│  │  ├─ navigation_service.py
│  │  ├─ position_history.py
│  │  ├─ README.md
│  │  ├─ requirements.txt
│  │  ├─ SCRIPT_REVIEW.md
│  │  ├─ solar_service.py
│  │  ├─ speech_service.py
│  │  └─ STM32_JETSON_SETUP.md
│  ├─ tests/
│  │  └─ test_server.py
│  ├─ .gitattributes
│  ├─ .gitignore
│  ├─ README.md
│  └─ server.py
├─ OGTECH-llm/
│  ├─ Co-LLM/
│  │  ├─ assets/
│  │  │  └─ audio/
│  │  │     ├─ daylight_detail.wav
│  │  │     ├─ destination_arrived.wav
│  │  │     ├─ destination_confirmed.wav
│  │  │     ├─ return_to_base.wav
│  │  │     └─ tts_unavailable.wav
│  │  ├─ config/
│  │  │  ├─ fixed_audio.json
│  │  │  ├─ keyword_rules.yaml
│  │  │  ├─ survival_cards.json
│  │  │  └─ wake_voice.json
│  │  ├─ docs/
│  │  │  ├─ 00_frozen_decisions.md
│  │  │  ├─ 00_OUTLINE.md
│  │  │  ├─ 01_main.md
│  │  │  ├─ 02_appendix_reproduction.md
│  │  │  ├─ 03_appendix_raw_log.md
│  │  │  ├─ 07_hardware_acceptance_harness.md
│  │  │  ├─ 08_05_log.txt
│  │  │  ├─ decision_matrix.csv
│  │  │  └─ measurements.csv
│  │  ├─ eval/
│  │  │  ├─ results/
│  │  │  │  └─ video_scenario_20.json
│  │  │  ├─ cases_classify.jsonl
│  │  │  ├─ cases_refuse.jsonl
│  │  │  ├─ run_eval.py
│  │  │  ├─ run_hardware_acceptance.py
│  │  │  ├─ run_video_scenario.py
│  │  │  └─ voice_cases.json
│  │  ├─ jetson/
│  │  │  ├─ user/
│  │  │  │  ├─ ogtech-device-monitor.service
│  │  │  │  ├─ ogtech-physical-voice.service
│  │  │  │  └─ ogtech-wake-voice.service
│  │  │  ├─ audio.env.example
│  │  │  ├─ ogtech-device-monitor.service
│  │  │  └─ ogtech-physical-voice.service
│  │  ├─ scripts/
│  │  │  ├─ 00_check_audio.sh
│  │  │  ├─ 01_record.sh
│  │  │  ├─ 02_play.sh
│  │  │  ├─ 03_echo.sh
│  │  │  ├─ 04_record_set.sh
│  │  │  ├─ 05_bench.sh
│  │  │  ├─ 06_demo.sh
│  │  │  ├─ 07_product_voice.sh
│  │  │  ├─ 08_device_monitor.sh
│  │  │  ├─ 09_physical_voice.sh
│  │  │  ├─ 10_wake_voice.sh
│  │  │  ├─ device_monitor.py
│  │  │  ├─ engines.py
│  │  │  ├─ LLM_USAGE.md
│  │  │  ├─ ogtech_core.py
│  │  │  ├─ physical_voice.py
│  │  │  ├─ pipeline_gate.py
│  │  │  ├─ product_assistant.py
│  │  │  ├─ product_voice.py
│  │  │  ├─ stt_prompt_s.txt
│  │  │  ├─ stt_prompt.sh
│  │  │  ├─ stt_prompt.txt
│  │  │  ├─ tts_pipeline.py
│  │  │  ├─ voice_loop.py
│  │  │  └─ wake_voice.py
│  │  ├─ tests/
│  │  │  ├─ fixtures/
│  │  │  │  └─ hardware_acceptance_sample.jsonl
│  │  │  ├─ __init__.py
│  │  │  ├─ test_device_monitor.py
│  │  │  ├─ test_engines_tts.py
│  │  │  ├─ test_eval_contract.py
│  │  │  ├─ test_hardware_acceptance.py
│  │  │  ├─ test_physical_voice.py
│  │  │  ├─ test_product_assistant.py
│  │  │  ├─ test_tts_pipeline.py
│  │  │  ├─ test_voice_loop_safety.py
│  │  │  ├─ test_voice_router.py
│  │  │  └─ test_wake_voice.py
│  │  ├─ .gitignore
│  │  ├─ 01_hardware_check.md
│  │  ├─ 02_install_a_to_z.md
│  │  ├─ 03_stt_candidates.md
│  │  ├─ 04_tts_candidates.md
│  │  ├─ 05_test_log.md
│  │  ├─ config.py
│  │  └─ README.md
│  ├─ config/
│  │  ├─ demo_script.json
│  │  ├─ fewshot_intent.jsonl
│  │  ├─ harness_policy.json
│  │  ├─ keyword_rules_demo.yaml
│  │  ├─ llama_server.args
│  │  ├─ polish_forbidden.json
│  │  ├─ README.md
│  │  ├─ schema_classify.json
│  │  ├─ schema_intent.json
│  │  ├─ schema_polish.json
│  │  ├─ stt_lexicon.json
│  │  ├─ system_prompt_ko.txt
│  │  └─ system_prompt_polish_ko.txt
│  ├─ docs2/
│  │  ├─ 00_README_index.md
│  │  ├─ 01_domain_transition_overview.md
│  │  ├─ 02_wilderness_case_studies.md
│  │  ├─ 03_beginner_risk_taxonomy.md
│  │  ├─ 04_feature_spec_draft.md
│  │  ├─ 05_llm_harness_redesign.md
│  │  ├─ 06_power_budget_battery.md
│  │  ├─ 07_hardware_feasibility.md
│  │  ├─ 10_parts_selection_bom.md
│  │  ├─ 11_attachment_pdf_demo_video_spec.md
│  │  ├─ 12_attachment_feature_verification_log.md
│  │  └─ 13_demo_harness_design.md
│  ├─ eval/
│  │  ├─ latency_bench.py
│  │  ├─ run_demo_script.py
│  │  └─ run_intent_eval.py
│  ├─ harness/
│  │  ├─ __init__.py
│  │  ├─ classify.py
│  │  ├─ demo_assistant.py
│  │  ├─ demo_router.py
│  │  ├─ device_state.py
│  │  ├─ fake_map.py
│  │  ├─ guard.py
│  │  ├─ intent.py
│  │  ├─ llm_client.py
│  │  ├─ mock_llm_server.py
│  │  ├─ normalize.py
│  │  ├─ paths.py
│  │  ├─ polish.py
│  │  ├─ preflight.py
│  │  └─ README.md
│  ├─ results/
│  │  ├─ demo_script_jetson.json
│  │  ├─ demo_script_mock_empty.json
│  │  ├─ demo_script_mock_garbage.json
│  │  ├─ demo_script_mock_http500.json
│  │  ├─ demo_script_mock_ok_20.json
│  │  ├─ demo_script_mock_timeout.json
│  │  ├─ demo_script_rules_only_20.json
│  │  ├─ intent_eval_jetson.json
│  │  ├─ intent_eval_mock.json
│  │  ├─ latency_intent_jetson.json
│  │  ├─ latency_intent_mock.json
│  │  └─ versions.md
│  ├─ runner/
│  │  ├─ user/
│  │  │  └─ ogtech-llm-server.service
│  │  ├─ llm.env.example
│  │  ├─ ogtech-llm-server.service
│  │  ├─ start_llama_server.sh
│  │  └─ warmup_llm.sh
│  ├─ tests/
│  │  ├─ _support.py
│  │  ├─ test_config_assets.py
│  │  ├─ test_demo_router.py
│  │  ├─ test_demo_script.py
│  │  ├─ test_device_state.py
│  │  ├─ test_guard.py
│  │  ├─ test_mock_llm_server.py
│  │  ├─ test_normalize.py
│  │  ├─ test_polish.py
│  │  └─ test_product_rules_regression.py
│  ├─ .gitattributes
│  ├─ .gitignore
│  └─ README.md
├─ .gitattributes
├─ .gitignore
├─ FILE_STRUCTURE.md
└─ README.md
```
