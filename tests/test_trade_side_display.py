from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TRADE_SIDE = ROOT / "frontend" / "src" / "utils" / "tradeSide.ts"


def _class_for_label(source: str, label: str) -> str:
    match = re.search(
        rf"label:\s*'{label}',\s*className:\s*'([^']+)'",
        source,
    )
    assert match, f"missing display mapping for {label}"
    return match.group(1)


def test_contract_close_sides_use_distinct_exit_colors():
    text = TRADE_SIDE.read_text(encoding="utf-8")

    assert _class_for_label(text, "开多") == "text-up"
    assert _class_for_label(text, "开空") == "text-down"
    assert _class_for_label(text, "平多") not in {"text-up", "text-down"}
    assert _class_for_label(text, "平空") not in {"text-up", "text-down"}
    assert _class_for_label(text, "平多") != _class_for_label(text, "平空")
