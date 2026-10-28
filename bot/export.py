"""Ответы интервью → Markdown для чтения и ручного разбора через Claude."""


def to_markdown(export: dict, user: str = "") -> str:
    lines = [f"# Ответы на анкету{f' — {user}' if user else ''}", ""]
    lines.append(f"Начато: {export['started_at']}, завершено: {export['finished_at']}")
    lines.append("")
    block = None
    for item in export["items"]:
        if item["block"] != block:
            block = item["block"]
            lines += [f"## {block}", ""]
        marks = []
        if item["skipped"]:
            marks.append("пропущен")
        if item["needs_review"]:
            marks.append("ответ всё ещё размытый")
        suffix = f" _({', '.join(marks)})_" if marks else ""
        lines.append(f"**{item['question']}**{suffix}")
        lines.append("")
        answers = item["answers"]
        if answers:
            lines.append(answers[0])
            for q, a in zip(item["clarifications"], answers[1:]):
                lines += ["", f"> Уточнение: {q}", "", a]
        for f in item["followups"]:
            lines += ["", f"> {f['q']}", "", f["a"]]
        lines.append("")
    return "\n".join(lines)
