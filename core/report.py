"""Сборка HTML-отчёта для клиента: майндкарта + BPMN по процессам + узкие места.

Страница самодостаточная: данные встроены, библиотеки (markmap, bpmn-js) грузятся с CDN.
"""
import html
import json
from datetime import datetime

SEVERITY = {
    "critical": ("Критично", "sev-critical"),
    "high": ("Высокий", "sev-high"),
    "medium": ("Средний", "sev-medium"),
    "low": ("Низкий", "sev-low"),
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ISSUE_TYPE = {
    "bottleneck": "Узкое место", "manual_work": "Ручная работа", "handoff_loss": "Потери при передаче",
    "no_tracking": "Нет учёта", "single_point_of_failure": "Держится на одном человеке",
    "owner_dependency": "Зависит от собственника", "duplicate_data": "Двойной ввод данных",
    "long_wait": "Долгое ожидание", "no_follow_up": "Нет работы после продажи", "other": "Другое",
}

BPMN_JS = "https://unpkg.com/bpmn-js@17/dist"
MARKMAP = "https://cdn.jsdelivr.net/npm/markmap-autoloader@0.18"

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Разбор бизнеса</title>
<link rel="stylesheet" href="__BPMN_JS__/assets/diagram-js.css">
<link rel="stylesheet" href="__BPMN_JS__/assets/bpmn-js.css">
<style>
:root {
  --bg: #f6f5f2; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6a66; --line: #e4e2dc;
  --accent: #2f5d50; --accent-soft: #e3ece8;
  --crit: #c62828; --crit-bg: #fdecea; --high: #c62828; --high-bg: #fdecea;
  --med: #b45309; --med-bg: #fff4e5; --low: #8a6d00; --low-bg: #fffbe0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --card: #1f1f1d; --ink: #ecebe7; --muted: #a09f9a; --line: #33322f;
    --accent: #7fb8a6; --accent-soft: #23302c;
    --crit-bg: #3a1d1c; --high-bg: #3a1d1c; --med-bg: #3a2a14; --low-bg: #35300f;
    --crit: #ff8a80; --high: #ff8a80; --med: #ffb366; --low: #e6cf5c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 32px 16px 64px; }
header { margin-bottom: 28px; }
.eyebrow { color: var(--accent); font-weight: 600; font-size: 13px; letter-spacing: .06em; text-transform: uppercase; }
h1 { font-size: 32px; line-height: 1.15; margin: 6px 0 8px; }
.lead { color: var(--muted); max-width: 760px; margin: 0; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 24px 0 8px; }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.stat b { display: block; font-size: 26px; line-height: 1.1; }
.stat span { color: var(--muted); font-size: 13px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; margin-top: 20px; }
h2 { font-size: 20px; margin: 0 0 4px; }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.markmap { width: 100%; height: 620px; }
.markmap > svg { width: 100%; height: 100%; }
.markmap svg { color: var(--ink); }
.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.tab { border: 1px solid var(--line); background: transparent; color: var(--ink); border-radius: 999px;
  padding: 6px 14px; font: inherit; font-size: 14px; cursor: pointer; }
.tab[aria-selected="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.proc-meta { color: var(--muted); font-size: 14px; margin: 0 0 10px; }
.canvas { height: 520px; border: 1px solid var(--line); border-radius: 10px; background: #fff; overflow: hidden; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: var(--muted); margin-top: 10px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; border: 1px solid; }
.issues { display: grid; gap: 10px; }
.issue { border: 1px solid var(--line); border-left-width: 4px; border-radius: 10px; padding: 12px 14px; }
.issue h3 { font-size: 15px; margin: 0 0 4px; }
.issue p { margin: 0; color: var(--muted); font-size: 14px; }
.tag { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; margin-right: 6px; }
.sev-critical { border-left-color: var(--crit); } .sev-critical .tag.sev { background: var(--crit-bg); color: var(--crit); }
.sev-high { border-left-color: var(--high); } .sev-high .tag.sev { background: var(--high-bg); color: var(--high); }
.sev-medium { border-left-color: var(--med); } .sev-medium .tag.sev { background: var(--med-bg); color: var(--med); }
.sev-low { border-left-color: var(--low); } .sev-low .tag.sev { background: var(--low-bg); color: var(--low); }
.tag.type { background: var(--accent-soft); color: var(--accent); }
.inferred { font-size: 12px; color: var(--muted); }
ul.questions { margin: 0; padding-left: 20px; }
footer { color: var(--muted); font-size: 13px; margin-top: 28px; }
@media (max-width: 640px) { h1 { font-size: 26px; } .markmap { height: 460px; } .canvas { height: 420px; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">Разбор бизнеса</div>
    <h1>__COMPANY__</h1>
    <p class="lead">__DESCRIPTION__</p>
    <div class="stats">__STATS__</div>
  </header>

  <section>
    <h2>Карта бизнеса</h2>
    <p class="hint">Колесо мыши — масштаб, перетаскивание — перемещение, клик по узлу — свернуть или развернуть ветку. Пометка (?) — достроено на основе ответов, требует подтверждения.</p>
    <div class="markmap"><script type="text/template">__MINDMAP__</script></div>
  </section>

  <section>
    <h2>Бизнес-процессы (BPMN)</h2>
    <p class="hint">Дорожки — исполнители. Схему можно двигать мышью и масштабировать колесом.</p>
    <div class="tabs" role="tablist">__TABS__</div>
    <p class="proc-meta" id="proc-meta"></p>
    <div class="canvas" id="canvas"></div>
    <div class="legend">
      <span><i style="background:#ffcdd2;border-color:#c62828"></i>Критичное узкое место</span>
      <span><i style="background:#ffe0b2;border-color:#ef6c00"></i>Проблемный шаг</span>
      <span><i style="background:#fff9c4;border-color:#9e9d24"></i>Ручная операция</span>
    </div>
  </section>

  <section>
    <h2>Узкие места</h2>
    <p class="hint">Отсортированы по критичности.</p>
    <div class="issues">__ISSUES__</div>
  </section>

  __OPEN_QUESTIONS__

  <footer>Отчёт сформирован __DATE__.</footer>
</div>

<script src="__MARKMAP__"></script>
<script src="__BPMN_JS__/bpmn-navigated-viewer.production.min.js"></script>
<script>
const PROCESSES = __PROCESSES_JSON__;
const viewer = new BpmnJS({ container: '#canvas' });
const meta = document.getElementById('proc-meta');
async function show(i) {
  document.querySelectorAll('.tab').forEach((t, j) => t.setAttribute('aria-selected', String(i === j)));
  const p = PROCESSES[i];
  meta.textContent = [p.trigger && ('Старт: ' + p.trigger), p.result && ('Результат: ' + p.result), p.frequency && ('Частота: ' + p.frequency)].filter(Boolean).join(' · ');
  try {
    await viewer.importXML(p.xml);
    viewer.get('canvas').zoom('fit-viewport', 'auto');
  } catch (e) {
    document.getElementById('canvas').textContent = 'Не удалось отрисовать схему: ' + e.message;
  }
}
document.querySelectorAll('.tab').forEach((t, i) => t.addEventListener('click', () => show(i)));
if (PROCESSES.length) show(0);
</script>
</body>
</html>
"""


def _stat(value, label):
    return f'<div class="stat"><b>{value}</b><span>{html.escape(label)}</span></div>'


def build_report(model: dict, mindmap_md: str, bpmn_by_process: dict[str, str]) -> str:
    c = model["company"]
    issues = sorted(model.get("issues", []), key=lambda i: SEVERITY_ORDER[i["severity"]])
    processes = {p["id"]: p for p in model.get("processes", [])}
    serious = sum(1 for i in issues if i["severity"] in ("critical", "high"))
    completeness = model["meta"].get("completeness")

    stats = [
        _stat(len(processes), "процессов разобрано"),
        _stat(len(model.get("roles", [])), "ролей в команде"),
        _stat(len(issues), "узких мест найдено"),
        _stat(serious, "из них серьёзных"),
    ]
    if completeness is not None:
        stats.append(_stat(f"{round(completeness * 100)}%", "полнота данных"))

    tabs = "".join(
        f'<button class="tab" role="tab" aria-selected="false">{html.escape(p["name"])}</button>'
        for p in processes.values()
    )

    issue_html = []
    for i in issues:
        label, cls = SEVERITY[i["severity"]]
        proc = processes.get(i.get("process_id"))
        where = f" · {html.escape(proc['name'])}" if proc else ""
        impact = f"<p>Чем грозит: {html.escape(i['impact'])}</p>" if i.get("impact") else ""
        inferred = ' <span class="inferred">(предположение, нужно подтвердить)</span>' if i.get("source") == "inferred" else ""
        issue_html.append(
            f'<div class="issue {cls}"><div><span class="tag sev">{label}</span>'
            f'<span class="tag type">{ISSUE_TYPE.get(i["type"], i["type"])}</span>'
            f'<span class="inferred">{where}</span></div>'
            f'<h3>{html.escape(i["description"])}{inferred}</h3>{impact}</div>'
        )

    open_q = model["meta"].get("open_questions") or []
    open_html = ""
    if open_q:
        items = "".join(f"<li>{html.escape(q)}</li>" for q in open_q)
        open_html = ('<section><h2>Что нужно уточнить</h2><p class="hint">Без этих ответов часть выводов — предположения.</p>'
                     f'<ul class="questions">{items}</ul></section>')

    proc_data = [
        {"name": p["name"], "trigger": p.get("trigger"), "result": p.get("result"),
         "frequency": p.get("frequency"), "xml": bpmn_by_process[p["id"]]}
        for p in processes.values()
    ]

    replacements = {
        "__BPMN_JS__": BPMN_JS,
        "__MARKMAP__": MARKMAP,
        "__COMPANY__": html.escape(c["name"]),
        "__DESCRIPTION__": html.escape(c["description"]),
        "__STATS__": "".join(stats),
        "__MINDMAP__": mindmap_md.replace("</", "<\\/"),
        "__TABS__": tabs,
        "__ISSUES__": "".join(issue_html) or '<p class="hint">Узких мест не найдено.</p>',
        "__OPEN_QUESTIONS__": open_html,
        "__DATE__": datetime.now().strftime("%d.%m.%Y"),
        "__PROCESSES_JSON__": json.dumps(proc_data, ensure_ascii=False).replace("</", "<\\/"),
    }
    page = TEMPLATE
    for key, value in replacements.items():
        page = page.replace(key, str(value))
    return page
