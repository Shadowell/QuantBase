from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_LAYOUT = ROOT / "frontend" / "src" / "components" / "MainLayout.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"


def test_settings_dialog_can_add_llm_models_from_ui():
    main_layout = MAIN_LAYOUT.read_text(encoding="utf-8")
    client = CLIENT.read_text(encoding="utf-8")

    assert "addLLMModel" in main_layout
    assert "新增模型" in main_layout
    assert "确认新增" in main_layout
    assert "llmConfig.models" in main_layout
    assert "llmConfig.freeTierModels" in main_layout
    assert "freeTierModels?: string[]" in client
    assert "postReq('/settings/llm-models'" in client
