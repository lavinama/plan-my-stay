"""Write the contract examples and JSON Schemas to disk, so teammates can read them without running Python.

    .venv/bin/python scripts/export_contracts.py

Outputs (all under src/plan_my_stay/messages/):
    contract_examples/valid/<name>.json     one valid message per type
    contract_examples/invalid/<name>.json   {"expected_code": ..., "message": ...}
    schema/<type>.payload.json              JSON Schema of each payload
    schema/envelope.json                    JSON Schema of the envelope
tests/messages/test_contracts.py fails if the example files drift from examples.py.
"""

import json
from pathlib import Path

from plan_my_stay.messages import PAYLOAD_MODELS, examples
from plan_my_stay.messages.envelope import MessageHead

ROOT = Path(__file__).resolve().parent.parent / "src" / "plan_my_stay" / "messages"


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    for name, raw in examples.valid_examples().items():
        _write(ROOT / "contract_examples" / "valid" / f"{name}.json", raw)
    for name, (raw, code) in examples.invalid_examples().items():
        _write(ROOT / "contract_examples" / "invalid" / f"{name}.json", {"expected_code": code.value, "message": raw})
    for msg_type, model in PAYLOAD_MODELS.items():
        _write(ROOT / "schema" / f"{msg_type.value}.payload.json", model.model_json_schema())
    _write(ROOT / "schema" / "envelope.json", MessageHead.model_json_schema(by_alias=True))
    print(f"wrote contracts under {ROOT}")


if __name__ == "__main__":
    main()
