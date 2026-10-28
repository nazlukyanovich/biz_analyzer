"""Проверка модели бизнеса: соответствие JSON-схеме + целостность ссылок между сущностями."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "business_model.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_errors(model: dict) -> list[str]:
    validator = Draft202012Validator(load_schema())
    errors = []
    for err in sorted(validator.iter_errors(model), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "(корень)"
        errors.append(f"{path}: {err.message}")
    return errors


def reference_errors(model: dict) -> list[str]:
    """Проверяет, что все *_id ссылаются на существующие сущности."""
    errors = []
    ids = {
        "role": {r["id"] for r in model.get("roles", [])},
        "department": {d["id"] for d in model.get("departments", [])},
        "system": {s["id"] for s in model.get("systems", [])},
        "channel": {c["id"] for c in model.get("channels", [])},
        "process": {p["id"] for p in model.get("processes", [])},
    }

    def check(value, kind, where):
        if value is not None and value not in ids[kind]:
            errors.append(f"{where}: нет {kind} с id '{value}'")

    for d in model.get("departments", []):
        check(d.get("head_role_id"), "role", f"departments/{d['id']}")
    for r in model.get("roles", []):
        check(r.get("department_id"), "department", f"roles/{r['id']}")
    for s in model.get("systems", []):
        for rid in s.get("used_by_role_ids", []):
            check(rid, "role", f"systems/{s['id']}")
    for seg in model.get("customer_segments", []):
        for cid in seg.get("channel_ids", []):
            check(cid, "channel", f"customer_segments/{seg['id']}")

    for p in model.get("processes", []):
        where = f"processes/{p['id']}"
        check(p.get("owner_role_id"), "role", where)
        step_ids = [s["id"] for s in p["steps"]]
        if len(step_ids) != len(set(step_ids)):
            errors.append(f"{where}: повторяющиеся id шагов")
        step_set = set(step_ids)
        if not any(s["type"] == "start" for s in p["steps"]):
            errors.append(f"{where}: нет стартового шага (type=start)")
        if not any(s["type"] == "end" for s in p["steps"]):
            errors.append(f"{where}: нет конечного шага (type=end)")
        for s in p["steps"]:
            sw = f"{where}/steps/{s['id']}"
            check(s.get("role_id"), "role", sw)
            for sid in s.get("system_ids", []):
                check(sid, "system", sw)
            for n in s.get("next", []):
                if n["step_id"] not in step_set:
                    errors.append(f"{sw}: next ведёт в несуществующий шаг '{n['step_id']}'")
            if s["type"] != "end" and not s.get("next"):
                errors.append(f"{sw}: шаг не ведёт никуда (нет next)")

    for m in model.get("metrics", []):
        check(m.get("process_id"), "process", f"metrics/{m['id']}")
    for i in model.get("issues", []):
        check(i.get("process_id"), "process", f"issues/{i['id']}")
        if i.get("step_id"):
            proc = next((p for p in model.get("processes", []) if p["id"] == i.get("process_id")), None)
            if proc is None or i["step_id"] not in {s["id"] for s in proc["steps"]}:
                errors.append(f"issues/{i['id']}: шаг '{i['step_id']}' не найден в процессе")
    return errors


def validate(model: dict) -> list[str]:
    errors = schema_errors(model)
    if errors:
        return errors  # без валидной структуры проверка ссылок бессмысленна
    return reference_errors(model)
