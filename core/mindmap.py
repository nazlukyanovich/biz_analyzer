"""Майндкарта бизнеса в Markdown (рендерится через markmap)."""

SEVERITY_LABEL = {"critical": "🔴 критично", "high": "🔴 высокий", "medium": "🟠 средний", "low": "🟡 низкий"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _mark(item: dict) -> str:
    return " (?)" if item.get("source") == "inferred" else ""


def build_mindmap(model: dict) -> str:
    c = model["company"]
    roles = {r["id"]: r for r in model.get("roles", [])}
    systems = {s["id"]: s["name"] for s in model.get("systems", [])}
    lines = [f"# {c['name']}"]

    lines.append("## О компании")
    lines.append(f"- {c['description']}")
    for key, label in [("industry", "Отрасль"), ("business_model", "Модель"), ("geography", "География"),
                       ("employees", "Команда"), ("revenue_range", "Оборот")]:
        if c.get(key):
            lines.append(f"- {label}: {c[key]}")
    if c.get("goals"):
        lines.append("- Цели")
        lines += [f"  - {g}" for g in c["goals"]]

    if model.get("products"):
        lines.append("## Продукты")
        for p in model["products"]:
            extra = f" — чек {p['avg_check']}" if p.get("avg_check") else ""
            lines.append(f"- {p['name']}{extra}")

    if model.get("customer_segments") or model.get("channels"):
        lines.append("## Клиенты и каналы")
        for seg in model.get("customer_segments", []):
            lines.append(f"- {seg['name']}")
            if seg.get("description"):
                lines.append(f"  - {seg['description']}")
        kinds = {"lead_source": "Источники заявок", "communication": "Связь с клиентом",
                 "sales_point": "Точки продаж", "delivery": "Доставка"}
        for kind, label in kinds.items():
            items = [ch for ch in model.get("channels", []) if ch["kind"] == kind]
            if items:
                lines.append(f"- {label}")
                for ch in items:
                    main = " ★ главный" if ch.get("is_main") else ""
                    vol = f" ({ch['leads_per_month']}/мес)" if ch.get("leads_per_month") else ""
                    lines.append(f"  - {ch['name']}{vol}{main}")

    lines.append("## Команда")
    for d in model.get("departments", []):
        lines.append(f"- {d['name']}")
        for r in roles.values():
            if r.get("department_id") == d["id"]:
                cnt = f" ×{r['headcount']}" if r.get("headcount") else ""
                owner = " (собственник)" if r.get("is_owner") else ""
                lines.append(f"  - {r['name']}{cnt}{owner}")
                for resp in r.get("responsibilities", []):
                    lines.append(f"    - {resp}")
    orphans = [r for r in roles.values() if not r.get("department_id")]
    for r in orphans:
        lines.append(f"- {r['name']}{' (собственник)' if r.get('is_owner') else ''}")

    if model.get("systems"):
        lines.append("## Инструменты")
        for s in model["systems"]:
            users = ", ".join(roles[r]["name"] for r in s.get("used_by_role_ids", []) if r in roles)
            lines.append(f"- {s['name']}" + (f" — {users}" if users else ""))

    lines.append("## Процессы")
    for p in model.get("processes", []):
        lines.append(f"- {p['name']}{_mark(p)}")
        if p.get("trigger"):
            lines.append(f"  - Старт: {p['trigger']}")
        for s in p["steps"]:
            if s["type"] in ("task", "decision", "wait"):
                who = roles.get(s.get("role_id"), {}).get("name")
                tools = ", ".join(systems[x] for x in s.get("system_ids", []) if x in systems)
                meta = " · ".join(x for x in [who, tools] if x)
                lines.append(f"  - {s['name']}{_mark(s)}" + (f" — {meta}" if meta else ""))
        if p.get("result"):
            lines.append(f"  - Результат: {p['result']}")

    if model.get("metrics"):
        lines.append("## Цифры")
        for m in model["metrics"]:
            val = f": {m['value']}" if m.get("value") else ""
            tracked = "" if m.get("is_tracked", True) else " ⚠ не считают"
            lines.append(f"- {m['name']}{val}{tracked}")

    issues = sorted(model.get("issues", []), key=lambda i: SEVERITY_ORDER[i["severity"]])
    if issues:
        lines.append("## Узкие места")
        for i in issues:
            lines.append(f"- {SEVERITY_LABEL[i['severity']]}: {i['description']}{_mark(i)}")

    return "\n".join(lines) + "\n"
