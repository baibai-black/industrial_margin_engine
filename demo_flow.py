from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from margin_engine.runtime import MarginRuntime  # noqa: E402


def main() -> int:
    runtime = MarginRuntime()
    print("== Reset demo data ==")
    print(json.dumps(runtime.reset_demo_data(), ensure_ascii=False, indent=2))

    print("\n== Snapshot before market shock ==")
    print(json.dumps(runtime.get_snapshot("ACC20001"), ensure_ascii=False, indent=2))

    print("\n== Apply TSLA market shock ==")
    shock_result = runtime.emit_event(
        {
            "event_type": "MARKET_SHOCK",
            "priority": "P0",
            "scope": "UNDERLYING",
            "source": "demo_flow",
            "underlyings": ["TSLA"],
            "payload": {
                "spot_move_pct": -0.12,
                "iv_move_abs": 0.10,
                "reason": "demo stress move"
            },
        }
    )
    print(json.dumps(shock_result, ensure_ascii=False, indent=2))

    print("\n== Snapshot after market shock ==")
    print(json.dumps(runtime.get_snapshot("ACC20001"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
