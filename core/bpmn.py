"""Генератор BPMN 2.0 XML из процесса модели бизнеса.

Раскладка своя, без внешних библиотек:
- дорожки (lanes) = роли исполнителей, в порядке первого появления;
- колонки = самый длинный путь от старта (обратные связи, т.е. циклы, игнорируются при расчёте);
- шаги с узкими местами подсвечиваются красным, ручные операции — жёлтым.
"""
from collections import defaultdict, deque
from xml.sax.saxutils import quoteattr

TYPE_MAP = {
    "start": "startEvent",
    "task": "task",
    "decision": "exclusiveGateway",
    "wait": "intermediateCatchEvent",
    "message": "intermediateThrowEvent",
    "end": "endEvent",
}
SIZE = {"task": (120, 70), "decision": (50, 50)}
EVENT_SIZE = (36, 36)

POOL_X, POOL_Y = 40, 40
LANE_LABEL_W = 30   # полоса с названием пула
COL_W = 190
LANE_H = 150
ROW_H = 110
FIRST_COL_X = POOL_X + LANE_LABEL_W + 80

ISSUE_COLORS = {  # (заливка, обводка)
    "critical": ("#ffcdd2", "#c62828"),
    "high": ("#ffcdd2", "#c62828"),
    "medium": ("#ffe0b2", "#ef6c00"),
    "low": ("#fff9c4", "#f9a825"),
}
MANUAL_COLORS = ("#fff9c4", "#9e9d24")
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
UNASSIGNED_LANE = "__unassigned__"


def _size(step_type: str) -> tuple[int, int]:
    return SIZE.get(step_type, EVENT_SIZE)


def _ranks(steps: list[dict]) -> tuple[dict, set]:
    """Колонка каждого шага (длиннейший путь) и множество обратных рёбер (циклов)."""
    by_id = {s["id"]: s for s in steps}
    starts = [s["id"] for s in steps if s["type"] == "start"] or [steps[0]["id"]]

    # DFS: ищем обратные рёбра, чтобы цикл «подумаю → напомнить → снова ответ» не ломал раскладку
    back_edges, state = set(), {}
    for root in starts + [s["id"] for s in steps]:
        if root in state:
            continue
        stack = [(root, iter(by_id[root].get("next", [])))]
        state[root] = "open"
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = "done"
                stack.pop()
                continue
            target = nxt["step_id"]
            if state.get(target) == "open":
                back_edges.add((node, target))
            elif target not in state:
                state[target] = "open"
                stack.append((target, iter(by_id[target].get("next", []))))

    indeg = defaultdict(int)
    for s in steps:
        for n in s.get("next", []):
            if (s["id"], n["step_id"]) not in back_edges:
                indeg[n["step_id"]] += 1
    rank = {s["id"]: 0 for s in steps}
    queue = deque(s["id"] for s in steps if indeg[s["id"]] == 0)
    while queue:
        node = queue.popleft()
        for n in by_id[node].get("next", []):
            t = n["step_id"]
            if (node, t) in back_edges:
                continue
            rank[t] = max(rank[t], rank[node] + 1)
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    return rank, back_edges


def _assign_rows(steps, rank, lane_of, back_edges):
    """Ряд внутри дорожки. Ветки развилки расходятся по разным рядам,
    а шаг наследует ряд предшественника — так каждая ветка идёт своей линией."""
    by_id = {s["id"]: s for s in steps}
    preds = defaultdict(list)
    for s in steps:
        for n in s.get("next", []):
            if (s["id"], n["step_id"]) not in back_edges:
                preds[n["step_id"]].append(s["id"])

    preferred = {}
    for s in steps:
        if s["type"] != "decision":
            continue
        same_lane = [n["step_id"] for n in s.get("next", [])
                     if lane_of[n["step_id"]] == lane_of[s["id"]] and (s["id"], n["step_id"]) not in back_edges]
        for offset, target in enumerate(same_lane):
            preferred[(s["id"], target)] = offset

    row, occupied = {}, set()
    for s in sorted(steps, key=lambda s: (rank[s["id"]], steps.index(s))):
        want = 0
        same_lane_preds = [p for p in preds[s["id"]] if lane_of[p] == lane_of[s["id"]] and p in row]
        if same_lane_preds:
            p = same_lane_preds[0]
            want = row[p] + preferred.get((p, s["id"]), 0)
        r = want
        while (rank[s["id"]], lane_of[s["id"]], r) in occupied:
            r += 1
        row[s["id"]] = r
        occupied.add((rank[s["id"]], lane_of[s["id"]], r))
    return row


def process_to_bpmn(process: dict, model: dict) -> str:
    steps = process["steps"]
    roles = {r["id"]: r["name"] for r in model.get("roles", [])}
    pid = process["id"]

    # Подсветка проблемных шагов
    step_issue = {}
    for issue in model.get("issues", []):
        if issue.get("process_id") == pid and issue.get("step_id"):
            prev = step_issue.get(issue["step_id"])
            if prev is None or SEVERITY_ORDER.index(issue["severity"]) > SEVERITY_ORDER.index(prev):
                step_issue[issue["step_id"]] = issue["severity"]

    # Дорожки
    lane_order = []
    lane_of = {}
    for s in steps:
        lane = s.get("role_id") or UNASSIGNED_LANE
        lane_of[s["id"]] = lane
        if lane not in lane_order:
            lane_order.append(lane)
    # старт/финиш без роли — в дорожку первого/последнего соседа, чтобы не плодить пустую дорожку
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        if lane_of[s["id"]] == UNASSIGNED_LANE and s["type"] in ("start", "end"):
            neighbour = None
            if s["type"] == "start" and s.get("next"):
                neighbour = by_id[s["next"][0]["step_id"]]
            if s["type"] == "end":
                neighbour = next((p for p in steps if any(n["step_id"] == s["id"] for n in p.get("next", []))), None)
            if neighbour and neighbour.get("role_id"):
                lane_of[s["id"]] = neighbour["role_id"]
    lane_order = [l for l in lane_order if l in lane_of.values()]
    for l in lane_of.values():
        if l not in lane_order:
            lane_order.append(l)
    lane_index = {l: i for i, l in enumerate(lane_order)}

    rank, back_edges = _ranks(steps)
    row = _assign_rows(steps, rank, lane_of, back_edges)

    # Высота дорожки зависит от числа рядов в ней
    lane_rows = defaultdict(int)
    for s in steps:
        lane_rows[lane_of[s["id"]]] = max(lane_rows[lane_of[s["id"]]], row[s["id"]] + 1)
    lane_top, lane_height, y = {}, {}, POOL_Y
    for lane in lane_order:
        lane_top[lane] = y
        lane_height[lane] = max(LANE_H, lane_rows[lane] * ROW_H + 30)
        y += lane_height[lane]

    # Координаты
    bounds = {}
    for s in steps:
        w, h = _size(s["type"])
        cx = FIRST_COL_X + rank[s["id"]] * COL_W + 60
        cy = lane_top[lane_of[s["id"]]] + 15 + row[s["id"]] * ROW_H + ROW_H // 2
        bounds[s["id"]] = (cx - w // 2, cy - h // 2, w, h)
    pool_w = FIRST_COL_X - POOL_X + (max(rank.values()) + 1) * COL_W + 40
    pool_h = y - POOL_Y

    proc_id = f"Process_{pid}"
    collab_id = f"Collaboration_{pid}"
    flows = []
    for s in steps:
        for i, n in enumerate(s.get("next", [])):
            flows.append((f"Flow_{s['id']}_{i}", s["id"], n["step_id"], n.get("condition")))
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for fid, src, tgt, _ in flows:
        outgoing[src].append(fid)
        incoming[tgt].append(fid)

    def sid(x):
        return f"Step_{x}"

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
        'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
        'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
        'xmlns:color="http://www.omg.org/spec/BPMN/non-normative/color/1.0" '
        f'id="Definitions_{pid}" targetNamespace="https://biz-analyzer/bpmn">'
    )
    out.append(f'  <bpmn:collaboration id="{collab_id}">')
    out.append(f'    <bpmn:participant id="Participant_{pid}" name={quoteattr(process["name"])} processRef="{proc_id}" />')
    out.append("  </bpmn:collaboration>")
    out.append(f'  <bpmn:process id="{proc_id}" name={quoteattr(process["name"])} isExecutable="false">')

    out.append(f'    <bpmn:laneSet id="LaneSet_{pid}">')
    for lane in lane_order:
        name = roles.get(lane, "Исполнитель не указан")
        out.append(f'      <bpmn:lane id="Lane_{pid}_{lane}" name={quoteattr(name)}>')
        for s in steps:
            if lane_of[s["id"]] == lane:
                out.append(f"        <bpmn:flowNodeRef>{sid(s['id'])}</bpmn:flowNodeRef>")
        out.append("      </bpmn:lane>")
    out.append("    </bpmn:laneSet>")

    for s in steps:
        tag = TYPE_MAP[s["type"]]
        name = s["name"] + (" (?)" if s.get("source") == "inferred" else "")
        out.append(f'    <bpmn:{tag} id="{sid(s["id"])}" name={quoteattr(name)}>')
        for f in incoming[s["id"]]:
            out.append(f"      <bpmn:incoming>{f}</bpmn:incoming>")
        for f in outgoing[s["id"]]:
            out.append(f"      <bpmn:outgoing>{f}</bpmn:outgoing>")
        if s["type"] == "wait":
            out.append(f'      <bpmn:timerEventDefinition id="Timer_{s["id"]}" />')
        if s["type"] == "message":
            out.append(f'      <bpmn:messageEventDefinition id="Msg_{s["id"]}" />')
        out.append(f"    </bpmn:{tag}>")

    for fid, src, tgt, cond in flows:
        name_attr = f" name={quoteattr(cond)}" if cond else ""
        out.append(f'    <bpmn:sequenceFlow id="{fid}" sourceRef="{sid(src)}" targetRef="{sid(tgt)}"{name_attr} />')
    out.append("  </bpmn:process>")

    # --- Диаграмма ---
    out.append(f'  <bpmndi:BPMNDiagram id="Diagram_{pid}">')
    out.append(f'    <bpmndi:BPMNPlane id="Plane_{pid}" bpmnElement="{collab_id}">')
    out.append(f'      <bpmndi:BPMNShape id="Participant_{pid}_di" bpmnElement="Participant_{pid}" isHorizontal="true">')
    out.append(f'        <dc:Bounds x="{POOL_X}" y="{POOL_Y}" width="{pool_w}" height="{pool_h}" />')
    out.append("      </bpmndi:BPMNShape>")
    for lane in lane_order:
        out.append(f'      <bpmndi:BPMNShape id="Lane_{pid}_{lane}_di" bpmnElement="Lane_{pid}_{lane}" isHorizontal="true">')
        out.append(
            f'        <dc:Bounds x="{POOL_X + LANE_LABEL_W}" y="{lane_top[lane]}" '
            f'width="{pool_w - LANE_LABEL_W}" height="{lane_height[lane]}" />'
        )
        out.append("      </bpmndi:BPMNShape>")

    for s in steps:
        x, y, w, h = bounds[s["id"]]
        color_attr = ""
        if s["id"] in step_issue:
            fill, stroke = ISSUE_COLORS[step_issue[s["id"]]]
            color_attr = f' color:background-color="{fill}" color:border-color="{stroke}"'
        elif s.get("is_manual"):
            fill, stroke = MANUAL_COLORS
            color_attr = f' color:background-color="{fill}" color:border-color="{stroke}"'
        out.append(f'      <bpmndi:BPMNShape id="{sid(s["id"])}_di" bpmnElement="{sid(s["id"])}"{color_attr}>')
        out.append(f'        <dc:Bounds x="{x}" y="{y}" width="{w}" height="{h}" />')
        if s["type"] != "task":  # подпись под событиями и развилками
            out.append(f'        <bpmndi:BPMNLabel><dc:Bounds x="{x - 30}" y="{y + h + 5}" width="{w + 60}" height="27" /></bpmndi:BPMNLabel>')
        out.append("      </bpmndi:BPMNShape>")

    for fid, src, tgt, cond in flows:
        sx, sy, sw, sh = bounds[src]
        tx, ty, tw, th = bounds[tgt]
        if (src, tgt) in back_edges or tx <= sx:
            # обратная связь: уходим вниз, под дорожку источника, и возвращаемся к цели снизу
            lane_bottom = lane_top[lane_of[src]] + lane_height[lane_of[src]] - 12
            pts = [(sx + sw // 2, sy + sh), (sx + sw // 2, lane_bottom),
                   (tx + tw // 2, lane_bottom), (tx + tw // 2, ty + th)]
        elif abs((sy + sh // 2) - (ty + th // 2)) < 2:
            pts = [(sx + sw, sy + sh // 2), (tx, ty + th // 2)]
        else:
            start = (sx + sw, sy + sh // 2)
            if src in by_id and by_id[src]["type"] == "decision":
                # из развилки выходим сверху/снизу — так ветки читаются лучше
                start = (sx + sw // 2, sy if ty < sy else sy + sh)
                pts = [start, (start[0], ty + th // 2), (tx, ty + th // 2)]
            else:
                mid = (sx + sw + tx) // 2
                pts = [start, (mid, sy + sh // 2), (mid, ty + th // 2), (tx, ty + th // 2)]
        out.append(f'      <bpmndi:BPMNEdge id="{fid}_di" bpmnElement="{fid}">')
        for px, py in pts:
            out.append(f'        <di:waypoint x="{px}" y="{py}" />')
        if cond:
            # подпись условия — у поворота стрелки, а для прямой — над началом линии
            if len(pts) >= 3:
                lx, ly = pts[1][0] + 8, pts[1][1] - 20
            else:
                lx, ly = pts[0][0] + 6, pts[0][1] - 20
            out.append(f'        <bpmndi:BPMNLabel><dc:Bounds x="{lx}" y="{ly}" width="90" height="18" /></bpmndi:BPMNLabel>')
        out.append("      </bpmndi:BPMNEdge>")

    out.append("    </bpmndi:BPMNPlane>")
    out.append("  </bpmndi:BPMNDiagram>")
    out.append("</bpmn:definitions>")
    return "\n".join(out) + "\n"
