"""
버스 배차 스케줄링 - tkinter 실시간 조절 뷰어 (3차: 범용 override 규칙 편집기)

- 배차간격을 시작/중간/끝 정류장별로 따로 설정
- override 규칙 편집기: 종류(start_hw/mid_hw/end_hw/leg1/leg2/rest) + 조건(버스쌍 또는
  버스+회차, 시간대) + 새 하한/상한을 자유롭게 조합해서 추가/삭제 가능한 범용 표.
  비워둔 조건/값은 "제한 없음"(모든 값 매치 / 기존 값 유지)으로 취급된다.
  "⚡ 위반에서 자동 제안" 버튼이 채우는 것도 결국 이 표의 행 하나일 뿐이라, 사람이 수동으로
  추가한 규칙과 자동 제안된 규칙이 완전히 동일한 방식으로 조정/삭제된다.
  (이 표는 나중에 AI 에이전트가 대화로 채워넣을 인터페이스로도 그대로 쓸 수 있게 설계함)
"""

import tkinter as tk
from tkinter import ttk
import math

import spring_schedule as sch


def fmt(minutes):
    if minutes is None:
        return "-"
    h, m = divmod(int(round(minutes)), 60)
    return f"{h:02d}:{m:02d}"


def parse_hhmm(s: str):
    s = s.strip()
    if not s:
        return None
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def parse_int_or_none(s: str):
    s = s.strip()
    return int(s) if s else None


def parse_float_or_none(s: str):
    s = s.strip()
    return float(s) if s else None


BUS_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b",
              "#bcbd22", "#e377c2", "#7f7f7f"]

LEFT_MARGIN = 70
TOP_MARGIN = 40
ROW_H = 30
PX_PER_MIN = 3.2

KIND_LABELS = {
    "start_hw": "배차간격(시작)", "mid_hw": "배차간격(중간)", "end_hw": "배차간격(끝)",
    "leg1": "이동시간(시작→중간)", "leg2": "이동시간(중간→끝)", "rest": "휴게시간",
}
HEADWAY_KINDS = ("start_hw", "mid_hw", "end_hw")
TRIP_KINDS = ("leg1", "leg2", "rest")


class App:
    def __init__(self, root):
        self.root = root
        root.title("버스 배차 - 스프링(반복 이완) 실시간 뷰어")

        self.left = ttk.Frame(root, padding=10)
        self.left.grid(row=0, column=0, sticky="ns")
        self.right = ttk.Frame(root, padding=10)
        self.right.grid(row=0, column=1, sticky="nsew")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.vars = {}
        self.time_bands = [{"basis": "start", "win_lo": 7 * 60, "win_hi": 9 * 60, "slack": 0, "peak": 15}]
        self.rules: list[dict] = []
        self._next_rule_id = 1

        self._ready = False
        self._build_controls()
        self._build_timeband_editor()
        self._build_rule_editor()
        self._ready = True

        self._build_canvas()

        self.prob = None
        self.recompute()

    # ---------------- 좌측: 구조/시각/배차 파라미터 ----------------

    def _stepper(self, parent, row, key, label, frm, to, init, width=6):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        v = tk.IntVar(value=init)
        self.vars[key] = v

        def on_change():
            if getattr(self, "_ready", False):
                self.recompute()

        sb = tk.Spinbox(parent, from_=frm, to=to, textvariable=v, width=width,
                          command=on_change, justify="right")
        sb.grid(row=row, column=1, sticky="w", padx=6, pady=2)
        sb.bind("<Return>", lambda _e: on_change())
        sb.bind("<FocusOut>", lambda _e: on_change())
        return sb

    def _time_entry(self, parent, row, key, label, init_minutes):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        v = tk.StringVar(value=fmt(init_minutes))
        self.vars[key] = v
        err = ttk.Label(parent, text="", foreground="#b00020")
        err.grid(row=row, column=2, sticky="w")

        def on_change(_e=None):
            if parse_hhmm(v.get()) is None:
                err.config(text="HH:MM 형식으로 입력")
                return
            err.config(text="")
            if getattr(self, "_ready", False):
                self.recompute()

        e = ttk.Entry(parent, textvariable=v, width=8)
        e.grid(row=row, column=1, sticky="w", padx=6, pady=2)
        e.bind("<Return>", on_change)
        e.bind("<FocusOut>", on_change)
        return e

    def _build_controls(self):
        p = self.left
        r = 0
        ttk.Label(p, text="구조 파라미터", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self._stepper(p, r, "n_buses", "버스 대수", 1, 15, 4); r += 1
        self._stepper(p, r, "n_morning", "오전 회차/버스", 1, 10, 3); r += 1
        self._stepper(p, r, "m_afternoon", "오후 회차/버스", 1, 10, 3); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1
        ttk.Label(p, text="시각 파라미터 (HH:MM)", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self._time_entry(p, r, "morning_start", "오전 시작", 5 * 60); r += 1
        self._time_entry(p, r, "afternoon_target_start", "오후 목표 시작", 12 * 60); r += 1
        self._time_entry(p, r, "day_end", "하루 마감(day_end)", 23 * 60 + 30); r += 1
        self._time_entry(p, r, "mid_start", "중간정류장 목표 서비스 시작", 5 * 60 + 30); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1
        ttk.Label(p, text="배차간격 (분, 정류장별 개별 설정)", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self._stepper(p, r, "headway_start_lo", "시작 정류장 하한", 1, 120, 8); r += 1
        self._stepper(p, r, "headway_start_hi", "시작 정류장 상한", 1, 180, 30); r += 1
        self._stepper(p, r, "headway_mid_lo", "중간 정류장 하한", 1, 120, 8); r += 1
        self._stepper(p, r, "headway_mid_hi", "중간 정류장 상한", 1, 180, 30); r += 1
        self._stepper(p, r, "headway_end_lo", "끝 정류장 하한", 1, 120, 8); r += 1
        self._stepper(p, r, "headway_end_hi", "끝 정류장 상한", 1, 180, 30); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1
        ttk.Label(p, text="휴게/이동 시간 (분)", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self._stepper(p, r, "rest_lo", "휴게시간 하한", 1, 120, 8); r += 1
        self._stepper(p, r, "rest_hi", "휴게시간 상한", 1, 180, 30); r += 1
        self._stepper(p, r, "tau_sm", "시작→중간 기준시간", 1, 180, 20); r += 1
        self._stepper(p, r, "tau_me", "중간→끝 기준시간", 1, 180, 15); r += 1

        self._param_next_row = r

    # ---------------- 슬랙/피크 시간대 편집기 ----------------

    def _build_timeband_editor(self):
        p = self.left
        r = self._param_next_row
        ttk.Separator(p).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1
        ttk.Label(p, text="슬랙타임(여유)/피크타임(혼잡) - 이동시간 기본값에 적용",
                   font=("", 9, "bold")).grid(row=r, column=0, columnspan=4, sticky="w"); r += 1

        form = ttk.Frame(p)
        form.grid(row=r, column=0, columnspan=4, sticky="w", pady=(4, 2)); r += 1

        ttk.Label(form, text="기준").grid(row=0, column=0, sticky="w")
        self.tb_basis = tk.StringVar(value="start")
        ttk.Combobox(form, textvariable=self.tb_basis, values=["start", "mid"],
                      width=6, state="readonly").grid(row=0, column=1, padx=(2, 10))

        ttk.Label(form, text="시간대").grid(row=0, column=2, sticky="w")
        self.tb_from = tk.StringVar(value="07:00")
        ttk.Entry(form, textvariable=self.tb_from, width=6).grid(row=0, column=3, padx=2)
        ttk.Label(form, text="~").grid(row=0, column=4)
        self.tb_to = tk.StringVar(value="09:00")
        ttk.Entry(form, textvariable=self.tb_to, width=6).grid(row=0, column=5, padx=(2, 10))

        ttk.Label(form, text="슬랙(-n분)").grid(row=0, column=6, sticky="w")
        self.tb_slack = tk.IntVar(value=0)
        tk.Spinbox(form, from_=0, to=120, textvariable=self.tb_slack, width=5).grid(row=0, column=7, padx=(2, 10))

        ttk.Label(form, text="피크(+m분)").grid(row=0, column=8, sticky="w")
        self.tb_peak = tk.IntVar(value=10)
        tk.Spinbox(form, from_=0, to=120, textvariable=self.tb_peak, width=5).grid(row=0, column=9, padx=2)

        ttk.Button(form, text="추가", command=self._add_timeband).grid(row=0, column=10, padx=(10, 4))
        ttk.Button(form, text="선택 삭제", command=self._delete_timeband).grid(row=0, column=11)

        cols = ("basis", "window", "slack", "peak")
        self.tb_tree = ttk.Treeview(p, columns=cols, show="headings", height=3, selectmode="extended")
        for c, w, t in zip(cols, (60, 140, 100, 100), ("기준", "시간대", "슬랙(-n분)", "피크(+m분)")):
            self.tb_tree.heading(c, text=t)
            self.tb_tree.column(c, width=w, anchor="center")
        self.tb_tree.grid(row=r, column=0, columnspan=4, sticky="w", pady=(2, 4)); r += 1

        self._timeband_next_row = r
        self._refresh_timeband_tree()

    def _refresh_timeband_tree(self):
        self.tb_tree.delete(*self.tb_tree.get_children())
        for i, b in enumerate(self.time_bands):
            window = f"{fmt(b['win_lo'])} ~ {fmt(b['win_hi'])}"
            self.tb_tree.insert("", "end", iid=str(i), values=(b["basis"], window, b["slack"], b["peak"]))

    def _add_timeband(self):
        lo, hi = parse_hhmm(self.tb_from.get()), parse_hhmm(self.tb_to.get())
        if lo is None or hi is None or hi <= lo:
            return
        self.time_bands.append({"basis": self.tb_basis.get(), "win_lo": lo, "win_hi": hi,
                                  "slack": self.tb_slack.get(), "peak": self.tb_peak.get()})
        self._refresh_timeband_tree()
        self.recompute()

    def _delete_timeband(self):
        selected = [int(i) for i in self.tb_tree.selection()]
        if not selected:
            return
        self.time_bands = [b for i, b in enumerate(self.time_bands) if i not in selected]
        self._refresh_timeband_tree()
        self.recompute()

    # ---------------- 범용 override 규칙 편집기 ----------------

    def _build_rule_editor(self):
        p = self.left
        r = self._timeband_next_row
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6); r += 1
        ttk.Label(p, text="제약 override 규칙 - 특정 조건(버스쌍 / 버스+회차 / 시간대)의 배차간격·이동시간·휴게시간만"
                          " 별도로 조정 (빈 칸은 '제한 없음')",
                   font=("", 9, "bold")).grid(row=r, column=0, columnspan=6, sticky="w"); r += 1

        form1 = ttk.Frame(p)
        form1.grid(row=r, column=0, columnspan=6, sticky="w", pady=(4, 2)); r += 1
        ttk.Label(form1, text="종류").grid(row=0, column=0, sticky="w")
        self.rule_kind = tk.StringVar(value="mid_hw")
        ttk.Combobox(form1, textvariable=self.rule_kind, state="readonly", width=16,
                      values=list(KIND_LABELS.keys())).grid(row=0, column=1, padx=(2, 10))

        ttk.Label(form1, text="버스A(→)").grid(row=0, column=2, sticky="w")
        self.rule_bus_from = tk.StringVar()
        ttk.Entry(form1, textvariable=self.rule_bus_from, width=4).grid(row=0, column=3, padx=(2, 6))
        ttk.Label(form1, text="버스B").grid(row=0, column=4, sticky="w")
        self.rule_bus_to = tk.StringVar()
        ttk.Entry(form1, textvariable=self.rule_bus_to, width=4).grid(row=0, column=5, padx=(2, 10))

        ttk.Label(form1, text="버스").grid(row=0, column=6, sticky="w")
        self.rule_bus = tk.StringVar()
        ttk.Entry(form1, textvariable=self.rule_bus, width=4).grid(row=0, column=7, padx=(2, 6))
        ttk.Label(form1, text="회차").grid(row=0, column=8, sticky="w")
        self.rule_occ = tk.StringVar()
        ttk.Entry(form1, textvariable=self.rule_occ, width=4).grid(row=0, column=9, padx=(2, 2))

        form2 = ttk.Frame(p)
        form2.grid(row=r, column=0, columnspan=6, sticky="w", pady=(2, 4)); r += 1
        ttk.Label(form2, text="시간대").grid(row=0, column=0, sticky="w")
        self.rule_time_from = tk.StringVar()
        ttk.Entry(form2, textvariable=self.rule_time_from, width=6).grid(row=0, column=1, padx=2)
        ttk.Label(form2, text="~").grid(row=0, column=2)
        self.rule_time_to = tk.StringVar()
        ttk.Entry(form2, textvariable=self.rule_time_to, width=6).grid(row=0, column=3, padx=(2, 10))

        ttk.Label(form2, text="새 하한").grid(row=0, column=4, sticky="w")
        self.rule_lo = tk.StringVar()
        ttk.Entry(form2, textvariable=self.rule_lo, width=6).grid(row=0, column=5, padx=(2, 6))
        ttk.Label(form2, text="새 상한").grid(row=0, column=6, sticky="w")
        self.rule_hi = tk.StringVar()
        ttk.Entry(form2, textvariable=self.rule_hi, width=6).grid(row=0, column=7, padx=(2, 10))

        ttk.Label(form2, text="설명").grid(row=0, column=8, sticky="w")
        self.rule_label = tk.StringVar()
        ttk.Entry(form2, textvariable=self.rule_label, width=16).grid(row=0, column=9, padx=(2, 10))

        btns = ttk.Frame(p)
        btns.grid(row=r, column=0, columnspan=6, sticky="w"); r += 1
        ttk.Button(btns, text="추가", command=self._add_rule).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(btns, text="선택 삭제", command=self._delete_rule).grid(row=0, column=1, padx=(0, 12))
        ttk.Button(btns, text="⚡ 위반에서 자동 제안", command=self._auto_suggest).grid(row=0, column=2)

        cols = ("kind", "pair", "bus_occ", "window", "lo", "hi", "label")
        self.rule_tree = ttk.Treeview(p, columns=cols, show="headings", height=6, selectmode="extended")
        widths = (110, 90, 70, 110, 60, 60, 160)
        heads = ("종류", "버스A→B", "버스/회차", "시간대", "하한", "상한", "설명")
        for c, w, h in zip(cols, widths, heads):
            self.rule_tree.heading(c, text=h)
            self.rule_tree.column(c, width=w, anchor="center")
        self.rule_tree.grid(row=r, column=0, columnspan=6, sticky="w", pady=(2, 4)); r += 1

        self._refresh_rule_tree()

    def _refresh_rule_tree(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for rule in self.rules:
            kind = "/".join(rule["kinds"])
            pair = f"{rule['bus_from']}→{rule['bus_to']}" if rule.get("bus_from") or rule.get("bus_to") else "-"
            bus_occ = f"{rule.get('bus') or ''}/{rule.get('occ') or ''}".strip("/") or "-"
            window = f"{fmt(rule['time_lo'])}~{fmt(rule['time_hi'])}" if rule.get("time_lo") is not None else "-"
            lo = rule["lo"] if rule.get("lo") is not None else "-"
            hi = rule["hi"] if rule.get("hi") is not None else "-"
            self.rule_tree.insert("", "end", iid=str(rule["id"]),
                                    values=(kind, pair, bus_occ, window, lo, hi, rule.get("label", "")))

    def _add_rule(self):
        kind = self.rule_kind.get()
        rule = {
            "id": self._next_rule_id, "kinds": [kind],
            "bus_from": parse_int_or_none(self.rule_bus_from.get()),
            "bus_to": parse_int_or_none(self.rule_bus_to.get()),
            "bus": parse_int_or_none(self.rule_bus.get()),
            "occ": parse_int_or_none(self.rule_occ.get()),
            "time_lo": parse_hhmm(self.rule_time_from.get()),
            "time_hi": parse_hhmm(self.rule_time_to.get()),
            "lo": parse_float_or_none(self.rule_lo.get()),
            "hi": parse_float_or_none(self.rule_hi.get()),
            "label": self.rule_label.get().strip(),
        }
        self._next_rule_id += 1
        self.rules.append(rule)
        self._refresh_rule_tree()
        self.recompute()

    def _delete_rule(self):
        selected = {int(i) for i in self.rule_tree.selection()}
        if not selected:
            return
        self.rules = [r for r in self.rules if r["id"] not in selected]
        self._refresh_rule_tree()
        self.recompute()

    def _auto_suggest(self):
        if self.prob is None:
            return
        suggestions = sch.suggest_overrides(self.hard)
        if not suggestions:
            self.info_label.config(text="자동 완화로 풀 수 있는 배차간격 모순이 없습니다 "
                                          "(day_end 등 다른 원인일 수 있어요)", foreground="#b06a00")
            return
        for s in suggestions:
            s["id"] = self._next_rule_id
            self._next_rule_id += 1
            self.rules.append(s)
        self._refresh_rule_tree()
        self.recompute()

    # ---------------- 우측: 캔버스 ----------------

    def _build_canvas(self):
        self.canvas = tk.Canvas(self.right, bg="white", width=950, height=420)
        vsb = ttk.Scrollbar(self.right, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(self.right, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.right.rowconfigure(0, weight=1)
        self.right.columnconfigure(0, weight=1)

        self.info_label = ttk.Label(self.right, text="", foreground="#333")
        self.info_label.grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.frame_scale = ttk.Scale(self.right, from_=0, to=1, orient="horizontal",
                                       command=lambda _=None: self._draw())
        self.frame_scale.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.frame_label = ttk.Label(self.right, text="엔진 단계: -")
        self.frame_label.grid(row=4, column=0, sticky="w")

    # ---------------- 재계산 & 그리기 ----------------

    def _current_params(self):
        v = {k: var.get() for k, var in self.vars.items() if isinstance(var, tk.IntVar)}
        times = {}
        for key in ("morning_start", "afternoon_target_start", "day_end", "mid_start"):
            times[key] = parse_hhmm(self.vars[key].get())
        if any(t is None for t in times.values()):
            return None

        return dict(
            n_buses=v["n_buses"], n_morning=v["n_morning"], m_afternoon=v["m_afternoon"],
            morning_start=times["morning_start"], afternoon_target_start=times["afternoon_target_start"],
            day_end=times["day_end"], mid_end=None, mid_start=times["mid_start"],
            headway_start_lo=min(v["headway_start_lo"], v["headway_start_hi"]),
            headway_start_hi=max(v["headway_start_lo"], v["headway_start_hi"]),
            headway_mid_lo=min(v["headway_mid_lo"], v["headway_mid_hi"]),
            headway_mid_hi=max(v["headway_mid_lo"], v["headway_mid_hi"]),
            headway_end_lo=min(v["headway_end_lo"], v["headway_end_hi"]),
            headway_end_hi=max(v["headway_end_lo"], v["headway_end_hi"]),
            rest_lo=min(v["rest_lo"], v["rest_hi"]), rest_hi=max(v["rest_lo"], v["rest_hi"]),
            tau_sm_default=v["tau_sm"], tau_me_default=v["tau_me"],
            mid_origin_overrides=[],
            override_rules=list(self.rules),
            time_bands=list(self.time_bands),
        )

    def recompute(self):
        p = self._current_params()
        if p is None:
            return
        try:
            prob, problems, hard = sch.solve(p)
        except Exception as ex:
            self.info_label.config(text=f"오류: {ex}")
            return
        self.prob = prob
        self.problems = problems
        self.hard = hard
        n_frames = max(1, len(prob.history) - 1)
        self.frame_scale.configure(to=n_frames)
        self.frame_scale.set(n_frames)
        self._draw()

    def _draw(self):
        if self.prob is None:
            return
        canvas = self.canvas
        canvas.delete("all")
        prob = self.prob

        frame_idx = int(round(self.frame_scale.get()))
        frame_idx = max(0, min(frame_idx, len(prob.history) - 1))
        snap = prob.history[frame_idx]
        values = snap["values"]
        self.frame_label.config(text=f"엔진 단계: {snap['label']}  ({frame_idx+1}/{len(prob.history)})")

        n_buses = prob.p["n_buses"]
        max_val = max([v for v in values.values() if v is not None] + [60])
        min_val = min([v for v in values.values() if v is not None] + [0])
        width = LEFT_MARGIN + int(max_val * PX_PER_MIN) + 60
        height = TOP_MARGIN + (n_buses + 1) * ROW_H
        canvas.configure(scrollregion=(0, 0, width, height))

        start_tick = int(min_val // 30) * 30
        t = start_tick
        while t <= max_val:
            x = LEFT_MARGIN + t * PX_PER_MIN
            canvas.create_line(x, TOP_MARGIN - 8, x, height, fill="#e8e8e8")
            canvas.create_text(x, TOP_MARGIN - 18, text=fmt(t), fill="#888", font=("", 8))
            t += 30

        for bus in range(1, n_buses + 1):
            row = bus - 1
            y = TOP_MARGIN + row * ROW_H
            color = BUS_COLORS[(bus - 1) % len(BUS_COLORS)]
            canvas.create_text(5, y, text=f"버스 {bus}", anchor="w", font=("", 8, "bold"), fill="#333")

            bus_trips = sorted([t for t in prob.trips if t.bus == bus],
                                 key=lambda t: (0 if t.session == "morning" else 1, t.round_robin_idx))
            prev_e = None
            for t in bus_trips:
                pts = []
                if not t.is_mid_origin:
                    pts.append(("S", values.get(t.key("S"))))
                pts.append(("M", values.get(t.key("M"))))
                pts.append(("E", values.get(t.key("E"))))
                pts = [(name, val) for name, val in pts if val is not None]
                if not pts:
                    continue

                first_val = pts[0][1]
                if prev_e is not None:
                    x1 = LEFT_MARGIN + prev_e * PX_PER_MIN
                    x2 = LEFT_MARGIN + first_val * PX_PER_MIN
                    canvas.create_line(x1, y, x2, y, fill="#bbbbbb", width=1, dash=(3, 2))

                for name, val in pts:
                    x = LEFT_MARGIN + val * PX_PER_MIN
                    canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
                    tag = f"{name}{t.occ}" if t.session == "morning" else f"{name}{t.occ}오"
                    canvas.create_text(x, y - 10, text=tag, font=("", 6), fill=color)
                for (n1, v1), (n2, v2) in zip(pts, pts[1:]):
                    x1 = LEFT_MARGIN + v1 * PX_PER_MIN
                    x2 = LEFT_MARGIN + v2 * PX_PER_MIN
                    canvas.create_line(x1, y, x2, y, fill=color, width=2)
                prev_e = pts[-1][1]

        n_violations = len(self.problems) + len(self.hard)
        if n_violations:
            if self.hard:
                h = self.hard[0]
                sample = (f"{h['event']} 모순 - "
                          f"{h['requires_at_least']['kind']}로 최소 {h['requires_at_least']['result']:.1f}분 필요, "
                          f"{h['but_at_most']['kind']}로 최대 {h['but_at_most']['result']:.1f}분까지만 허용"
                          f"(초과 {h['shortfall']:.1f}분)")
            else:
                v = self.problems[0]
                sample = f"{v['kind']} {v['edge']}: 요구 {v['required']}, 실제 {v['actual']:.1f}"
            self.info_label.config(text=f"⚠ 위반 {n_violations}건 (예: {sample})", foreground="#b00020")
        else:
            self.info_label.config(text="모든 제약 만족", foreground="#0a7a0a")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
