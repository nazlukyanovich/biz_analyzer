"""CLI: модель бизнеса (JSON) → майндкарта, BPMN-файлы и HTML-отчёт.

Пример:
    python -m core.build examples/flower_delivery.json -o out/flower_delivery
"""
import argparse
import json
import sys
from pathlib import Path

from .bpmn import process_to_bpmn
from .mindmap import build_mindmap
from .report import build_report
from .validate import validate


def build(model_path: Path, out_dir: Path) -> Path:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    errors = validate(model)
    if errors:
        print("Модель не прошла проверку:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "processes").mkdir(exist_ok=True)

    mindmap_md = build_mindmap(model)
    (out_dir / "mindmap.md").write_text(mindmap_md, encoding="utf-8")

    bpmn = {}
    for p in model["processes"]:
        xml = process_to_bpmn(p, model)
        bpmn[p["id"]] = xml
        (out_dir / "processes" / f"{p['id']}.bpmn").write_text(xml, encoding="utf-8")

    report_path = out_dir / "report.html"
    report_path.write_text(build_report(model, mindmap_md, bpmn), encoding="utf-8")
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Сборка отчёта по модели бизнеса")
    parser.add_argument("model", type=Path, help="JSON-файл модели бизнеса")
    parser.add_argument("-o", "--out", type=Path, default=Path("out"), help="Папка для результата")
    args = parser.parse_args()
    report = build(args.model, args.out)
    print(f"Готово: {report}")


if __name__ == "__main__":
    main()
