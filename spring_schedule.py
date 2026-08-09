"""
버스 배차 스케줄링 - 반복 이완(스프링) 방법 알고리즘 (3차: 범용 override 규칙 시스템)

설계 개요
---------
문제를 "이벤트(시간점) 사이의 차이 제약 그래프(Simple Temporal Network)"로 본다.
각 트립(회차)은 최대 3개의 이벤트를 가진다: S(시작 출발) / M(중간 도착) / E(끝 도착).

이벤트 사이 관계는 x_to - x_from ∈ [lo, hi] 형태의 "엣지"로 표현되고, 엣지 종류는:
    - leg1: S -> M (구간1 이동시간),  leg2: M -> E (구간2 이동시간)
    - start_hw / mid_hw / end_hw: 정류장별 배차간격 체인 (세션 내에서만 이어짐)
    - rest: 같은 버스의 휴게시간(점심시간 포함) 체인
    - cap: day_end/mid_end/morning_start/mid_start 등 상한 또는 하한 전용 고정 제약

핵심 변경점 (이번 버전)
---------------------
1. 배차간격을 시작/중간/끝 정류장별로 따로 설정 가능 (headway_start_*, headway_mid_*, headway_end_*).
2. 기존의 개별 override 딕셔너리(headway_overrides, meal_break_overrides, occurrence_rest_overrides,
   travel_time_overrides, lunch_break_overrides)를 하나의 범용 "override_rules" 리스트로 통합.
   규칙 하나는 "어떤 엣지 종류(kinds)에, 어떤 조건(버스쌍/버스+회차/시간대)에서 매치되면,
   [lo, hi] 중 지정된 쪽만 교체한다"는 형태 - 상한/하한 둘 다, 그리고 정류장 배차간격/이동시간
   (leg1,leg2 구분)/휴게시간 전부를 이 하나의 시스템으로 다룬다.
   이 구조는 나중에 AI 에이전트가 대화로 규칙을 추가/삭제하게 만들기 위한 것이라, 규칙은
   plain dict로 직렬화 가능하게 설계했다 (override_rules: List[dict]).
3. suggest_overrides()는 이제 이 범용 규칙 형태로 제안을 만든다 - 사용자가 그걸 보고
   표에서 직접 조정(값 변경/삭제/조건 추가)할 수 있다.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Set
import math

INF = math.inf

STATION_OF_KIND = {"start_hw": "start", "mid_hw": "mid", "end_hw": "end"}


# ============================================================
# 1. 데이터 모델
# ============================================================

@dataclass
class TimeBand:
    basis: str        # "start" 또는 "mid" (leg1/leg2 이동시간 슬랙/피크 전용, 4.10절)
    win_lo: float
    win_hi: float
    slack: float = 0.0
    peak: float = 0.0


@dataclass
class OverrideRule:
    """범용 제약 override 규칙 한 줄.
    kinds에 해당하는 엣지들 중, 아래 선택자(selector) 조건에 다 맞는 엣지에 대해
    lo/hi 중 None이 아닌 쪽만 교체한다. 여러 규칙이 매치되면 '조건이 더 구체적인'
    규칙이 우선하고, 구체성이 같으면 나중에 추가된 규칙이 우선한다."""
    id: int
    kinds: Set[str]                      # {"start_hw","mid_hw","end_hw"} / {"rest"} / {"leg1"} / {"leg2"} 등
    bus_from: Optional[int] = None        # 배차간격/휴게 체인: 발신 트립의 버스
    bus_to: Optional[int] = None          # 배차간격 체인: 수신 트립의 버스
    bus: Optional[int] = None             # leg1/leg2/rest: 특정 버스
    occ: Optional[int] = None             # leg1/leg2/rest: 특정 "하루 전체 회차 번호"(global_occ)
    time_lo: Optional[float] = None        # basis 시각이 이 구간에 들어갈 때만 매치 (start_hw/mid_hw/end_hw)
    time_hi: Optional[float] = None
    lo: Optional[float] = None            # 새 하한 (None이면 기존 유지)
    hi: Optional[float] = None            # 새 상한 (None이면 기존 유지)
    label: str = ""

    def specificity(self) -> int:
        score = 0
        for f in (self.bus_from, self.bus_to, self.bus, self.occ):
            if f is not None:
                score += 1
        if self.time_lo is not None:
            score += 1
        return score

    def matches(self, edge: "Edge", basis_time: Optional[float]) -> bool:
        if edge.kind not in self.kinds:
            return False
        if edge.kind in ("start_hw", "mid_hw", "end_hw"):
            if self.bus_from is not None and self.bus_from != edge.bus_from:
                return False
            if self.bus_to is not None and self.bus_to != edge.bus_to:
                return False
        else:  # rest / leg1 / leg2
            if self.bus is not None and self.bus != edge.bus_from:
                return False
            if self.occ is not None and self.occ != edge.occ:
                return False
        if self.time_lo is not None:
            if basis_time is None or not (self.time_lo <= basis_time < self.time_hi):
                return False
        return True


@dataclass
class Trip:
    bus: int
    session: str
    occ: int
    global_occ: int
    round_robin_idx: int
    is_mid_origin: bool = False
    is_auto_mid_origin: bool = False
    is_last_of_day: bool = False
    is_last_of_session: bool = False

    def key(self, ev: str) -> str:
        return f"{self.session}:{self.bus}:{self.occ}:{ev}"


@dataclass
class Edge:
    frm: str
    to: str
    kind: str                       # leg1/leg2/start_hw/mid_hw/end_hw/rest/cap
    bus_from: Optional[int] = None   # 체인 엣지: 발신 트립 버스 / leg,rest: 그 트립(또는 앞 트립)의 버스
    bus_to: Optional[int] = None     # 체인 엣지: 수신 트립 버스
    occ: Optional[int] = None        # leg1/leg2/rest 전용: 관련 트립의 global_occ
    is_lunch: bool = False           # rest 전용: 점심시간 경계인지
    cap_lo: Optional[float] = None   # cap 전용
    cap_hi: Optional[float] = None
    assigned: Optional[float] = None  # 스트레치 단계에서 배정된 길이


class Problem:
    def __init__(self, params: dict):
        self.p = params
        self.trips: List[Trip] = []
        self.edges: List[Edge] = []
        self.values: Dict[str, float] = {}
        self.incoming: Dict[str, List[Edge]] = {}
        self.history: List[dict] = []
        self.rules: List[OverrideRule] = []

    def add_edge(self, e: Edge):
        self.edges.append(e)
        self.incoming.setdefault(e.to, []).append(e)

    def snapshot(self, label: str):
        self.history.append({"label": label, "values": dict(self.values)})


def parse_rules(raw_rules: List[dict]) -> List[OverrideRule]:
    rules = []
    for i, r in enumerate(raw_rules):
        kinds = r["kinds"] if isinstance(r["kinds"], set) else set(r["kinds"])
        rules.append(OverrideRule(
            id=r.get("id", i), kinds=kinds,
            bus_from=r.get("bus_from"), bus_to=r.get("bus_to"),
            bus=r.get("bus"), occ=r.get("occ"),
            time_lo=r.get("time_lo"), time_hi=r.get("time_hi"),
            lo=r.get("lo"), hi=r.get("hi"), label=r.get("label", ""),
        ))
    return rules


# ============================================================
# 2. 시간대(슬랙/피크) 조회 - 4.10절 (leg1/leg2 기본값 계산용)
# ============================================================

def travel_range(base_tau: float, basis_time: float, basis: str,
                  time_bands: List[TimeBand]) -> Tuple[float, float]:
    max_slack = 0.0
    max_peak = 0.0
    for b in time_bands:
        if b.basis != basis:
            continue
        if b.win_lo <= basis_time < b.win_hi:
            max_slack = max(max_slack, b.slack)
            max_peak = max(max_peak, b.peak)
    return base_tau - max_slack, base_tau + max_peak


# ============================================================
# 3. 제약 해석 (기본값 + override 규칙 적용) - 모든 엣지 종류를 여기서 통일 처리
# ============================================================

def resolve_bounds(edge: Edge, prob: "Problem", p: dict,
                    time_bands: List[TimeBand], rules: List[OverrideRule]) -> Tuple[float, float]:
    if edge.kind == "cap":
        return edge.cap_lo, edge.cap_hi

    frm_val = prob.values.get(edge.frm, 0.0 if edge.frm == "ORIGIN" else None)

    if edge.kind in ("start_hw", "mid_hw", "end_hw"):
        station = STATION_OF_KIND[edge.kind]
        default_lo = p[f"headway_{station}_lo"]
        default_hi = p[f"headway_{station}_hi"]
    elif edge.kind == "rest":
        default_lo = p["rest_lo"]
        default_hi = INF if edge.is_lunch else p["rest_hi"]
    else:  # leg1 / leg2
        basis = "start" if edge.kind == "leg1" else "mid"
        base_tau = p["tau_sm_default"] if edge.kind == "leg1" else p["tau_me_default"]
        basis_time = frm_val if frm_val is not None else 0.0
        default_lo, default_hi = travel_range(base_tau, basis_time, basis, time_bands)

    matches = [r for r in rules if r.matches(edge, frm_val)]
    if not matches:
        return default_lo, default_hi
    best = max(matches, key=lambda r: (r.specificity(), rules.index(r)))
    lo = best.lo if best.lo is not None else default_lo
    hi = best.hi if best.hi is not None else default_hi
    return lo, hi


# ============================================================
# 4. mid_start 자동 배정 (전처리, 4.6절)
# ============================================================

def auto_assign_mid_origin(p: dict, manual_mid_origin: set) -> set:
    """공백(= (morning_start+tau_sm) - mid_start)을 중간정류장 배차간격 목표로 나눠
    필요한 버스 수를 계산, 버스 1번부터 순서대로 mid_origin으로 지정."""
    mid_start = p.get("mid_start")
    if mid_start is None:
        return set()

    headway_target = (p["headway_mid_lo"] + p["headway_mid_hi"]) / 2.0
    natural_arrival = p["morning_start"] + p["tau_sm_default"]
    gap = natural_arrival - mid_start
    if gap <= 0:
        return set()

    n_needed = min(math.ceil(gap / headway_target), p["n_buses"])
    assigned = set()
    bus = 1
    while len(assigned) < n_needed and bus <= p["n_buses"]:
        if bus not in manual_mid_origin:
            assigned.add(bus)
        bus += 1
    return assigned


# ============================================================
# 5. 그래프 빌드
# ============================================================

def build_problem(p: dict) -> Problem:
    prob = Problem(p)
    prob.rules = parse_rules(p.get("override_rules", []))

    manual_mid_origin = {(b, o) for (b, o) in p.get("mid_origin_overrides", [])}
    manual_mid_bus_only = {b for (b, o) in manual_mid_origin if o == 1}
    auto_mid = auto_assign_mid_origin(p, manual_mid_bus_only)

    sessions = [("morning", p["n_morning"]), ("afternoon", p["m_afternoon"])]

    trips_by_bus_session: Dict[Tuple[int, str], List[Trip]] = {}
    for session, n_occ in sessions:
        idx = 0
        for occ in range(1, n_occ + 1):
            for bus in range(1, p["n_buses"] + 1):
                is_mid = (bus, occ) in manual_mid_origin
                is_auto = (session == "morning" and occ == 1 and bus in auto_mid)
                if is_auto:
                    is_mid = True
                global_occ = occ if session == "morning" else p["n_morning"] + occ
                t = Trip(bus=bus, session=session, occ=occ, global_occ=global_occ,
                          round_robin_idx=idx, is_mid_origin=is_mid, is_auto_mid_origin=is_auto)
                t.is_last_of_session = (occ == n_occ)
                t.is_last_of_day = (session == "afternoon" and occ == n_occ)
                prob.trips.append(t)
                trips_by_bus_session.setdefault((bus, session), []).append(t)
                idx += 1

    trips_in_order = prob.trips

    # ---- 정류장 배차간격 체인 (4.1) - 세션 경계를 넘지 않음 ----
    for session, _ in sessions:
        session_trips = [t for t in trips_in_order if t.session == session]

        start_seq = [t for t in session_trips if not t.is_mid_origin]
        for a, b in zip(start_seq, start_seq[1:]):
            prob.add_edge(Edge(a.key("S"), b.key("S"), "start_hw", bus_from=a.bus, bus_to=b.bus))

        for a, b in zip(session_trips, session_trips[1:]):
            prob.add_edge(Edge(a.key("M"), b.key("M"), "mid_hw", bus_from=a.bus, bus_to=b.bus))
            prob.add_edge(Edge(a.key("E"), b.key("E"), "end_hw", bus_from=a.bus, bus_to=b.bus))

    # ---- 같은 버스 휴게시간 체인 (4.2, 4.3 점심시간) ----
    for bus in range(1, p["n_buses"] + 1):
        chain = trips_by_bus_session[(bus, "morning")] + trips_by_bus_session[(bus, "afternoon")]
        for a, b in zip(chain, chain[1:]):
            is_lunch = (a.session == "morning" and b.session == "afternoon")
            frm = a.key("E")
            to = b.key("S") if not b.is_mid_origin else b.key("M")
            prob.add_edge(Edge(frm, to, "rest", bus_from=bus, occ=a.global_occ, is_lunch=is_lunch))
            if is_lunch:
                prob.add_edge(Edge("ORIGIN", to, "cap",
                                     cap_lo=p["afternoon_target_start"], cap_hi=INF))

    # ---- 트립 내부 이동시간 (leg1: S->M, leg2: M->E) ----
    for t in trips_in_order:
        if not t.is_mid_origin:
            prob.add_edge(Edge(t.key("S"), t.key("M"), "leg1", bus_from=t.bus, occ=t.global_occ))
        prob.add_edge(Edge(t.key("M"), t.key("E"), "leg2", bus_from=t.bus, occ=t.global_occ))

    # ---- 상한 전용 제약 (day_end, mid_end) ----
    if p.get("day_end") is not None:
        for bus in range(1, p["n_buses"] + 1):
            for session in ("morning", "afternoon"):
                last = trips_by_bus_session[(bus, session)][-1]
                prob.add_edge(Edge("ORIGIN", last.key("E"), "cap", cap_lo=-INF, cap_hi=p["day_end"]))
    if p.get("mid_end") is not None:
        for bus in range(1, p["n_buses"] + 1):
            for session in ("morning", "afternoon"):
                last = trips_by_bus_session[(bus, session)][-1]
                prob.add_edge(Edge("ORIGIN", last.key("M"), "cap", cap_lo=-INF, cap_hi=p["mid_end"]))

    # ---- 오전 시작 하한 ----
    for bus in range(1, p["n_buses"] + 1):
        first = trips_by_bus_session[(bus, "morning")][0]
        target_key = first.key("S") if not first.is_mid_origin else first.key("M")
        floor = p["mid_start"] if first.is_auto_mid_origin else p["morning_start"]
        prob.add_edge(Edge("ORIGIN", target_key, "cap", cap_lo=floor, cap_hi=INF))

    prob.values["ORIGIN"] = 0.0
    return prob


# ============================================================
# 6. 엔진 B: 압축
# ============================================================

def compact(prob: Problem, order: List[str], time_bands: List[TimeBand],
            start_idx: int = 0) -> List[dict]:
    p = prob.p
    violations = []
    for key in order[start_idx:]:
        edges_in = prob.incoming.get(key, [])
        best_lo, best_hi = -INF, INF
        lo_edge = hi_edge = None
        for e in edges_in:
            frm_val = prob.values.get(e.frm, 0.0 if e.frm == "ORIGIN" else None)
            if frm_val is None:
                continue
            lo, hi = resolve_bounds(e, prob, p, time_bands, prob.rules)
            cand_lo, cand_hi = frm_val + lo, frm_val + hi
            if cand_lo > best_lo:
                best_lo, lo_edge = cand_lo, (e, frm_val, lo)
            if cand_hi < best_hi:
                best_hi, hi_edge = cand_hi, (e, frm_val, hi)
        value = best_lo if best_lo > -INF else 0.0
        prob.values[key] = value
        if value > best_hi + 1e-9:
            e_lo, frm_lo_val, lo_req = lo_edge
            e_hi, frm_hi_val, hi_req = hi_edge
            violations.append({
                "event": key,
                "requires_at_least": {"from": e_lo.frm, "kind": e_lo.kind,
                                        "from_value": frm_lo_val, "edge_lo": lo_req, "result": best_lo},
                "but_at_most": {"from": e_hi.frm, "kind": e_hi.kind,
                                 "from_value": frm_hi_val, "edge_hi": hi_req, "result": best_hi,
                                 "bus_from": e_hi.bus_from, "bus_to": e_hi.bus_to},
                "shortfall": value - best_hi,
            })
    prob.snapshot(f"compact@{start_idx}")
    return violations


# ============================================================
# 7. 엔진 C: 스트레치 (mid 배차간격을 중간값에 가깝게)
# ============================================================

def stretch(prob: Problem, order: List[str], time_bands: List[TimeBand]) -> None:
    p = prob.p
    mid_edges = [e for e in prob.edges if e.kind == "mid_hw"]
    if not mid_edges:
        return

    desired_extra = {}
    for e in mid_edges:
        lo, hi = resolve_bounds(e, prob, p, time_bands, prob.rules)
        mid_target = (lo + hi) / 2.0
        desired_extra[id(e)] = max(0.0, mid_target - lo)

    total_room = sum(desired_extra.values())
    if total_room <= 0:
        return

    budget = INF
    for e in prob.edges:
        if e.kind == "cap" and e.cap_hi < INF:
            budget = min(budget, e.cap_hi - prob.values[e.to])
    total_to_apply = min(total_room, max(0.0, budget))

    remaining_edges = {id(e): e for e in mid_edges}
    allocation = {id(e): 0.0 for e in mid_edges}
    remaining_budget = total_to_apply

    for _ in range(10):
        if not remaining_edges or remaining_budget <= 1e-9:
            break
        room_sum = sum(desired_extra[i] for i in remaining_edges)
        if room_sum <= 1e-9:
            break
        newly_full = []
        for i in remaining_edges:
            share = remaining_budget * (desired_extra[i] / room_sum)
            take = min(share, desired_extra[i] - allocation[i])
            allocation[i] += take
        used = sum(allocation[i] for i in remaining_edges)
        remaining_budget = total_to_apply - used
        for i in list(remaining_edges):
            if allocation[i] >= desired_extra[i] - 1e-9:
                newly_full.append(i)
        for i in newly_full:
            remaining_edges.pop(i, None)

    for e in mid_edges:
        e.assigned = resolve_bounds(e, prob, p, time_bands, prob.rules)[0] + allocation[id(e)]

    prob.snapshot("stretch-plan")


def apply_stretch_and_fix(prob: Problem, order: List[str], time_bands: List[TimeBand]) -> None:
    p = prob.p
    key_to_edge_mid = {e.to: e for e in prob.edges if e.kind == "mid_hw"}

    for key in order:
        edges_in = prob.incoming.get(key, [])
        best_lo, best_hi = -INF, INF
        for e in edges_in:
            frm_val = prob.values.get(e.frm, 0.0 if e.frm == "ORIGIN" else None)
            if frm_val is None:
                continue
            lo, hi = resolve_bounds(e, prob, p, time_bands, prob.rules)
            if e is key_to_edge_mid.get(key) and e.assigned is not None:
                candidate = frm_val + min(e.assigned, hi)
            else:
                candidate = frm_val + lo
            best_lo = max(best_lo, candidate)
            best_hi = min(best_hi, frm_val + hi)
        value = best_lo if best_lo > -INF else prob.values.get(key, 0.0)
        if value > best_hi:
            value = best_hi
        prob.values[key] = value

    prob.snapshot("stretch-applied(left-to-right fix)")


# ============================================================
# 8. 검증 (section 9)
# ============================================================

def verify(prob: Problem, time_bands: List[TimeBand]) -> List[dict]:
    p = prob.p
    problems = []
    for e in prob.edges:
        frm_val = prob.values.get(e.frm, 0.0 if e.frm == "ORIGIN" else None)
        to_val = prob.values.get(e.to)
        if frm_val is None or to_val is None:
            continue
        lo, hi = resolve_bounds(e, prob, p, time_bands, prob.rules)
        gap = to_val - frm_val
        if gap < lo - 1e-6 or gap > hi + 1e-6:
            problems.append({"edge": (e.frm, e.to), "kind": e.kind, "required": (lo, hi), "actual": gap})
    return problems


# ============================================================
# 9. 위반 -> override 규칙 자동 제안
# ============================================================

def suggest_overrides(hard_violations: List[dict]) -> List[dict]:
    """압축 단계 모순을 (버스쌍, 배차간격 종류) 기준으로 묶어서, 각각을 해소하는 데
    필요한 최소 hi를 계산해 override_rules 형식의 dict 리스트로 반환한다.
    day_end/mid_end 같은 cap 위반은 배차간격 규칙으로 못 고치므로 제외한다."""

    def parse_bus(key):
        if key == "ORIGIN":
            return None
        _, bus, _, _ = key.split(":")
        return int(bus)

    needed: Dict[Tuple[str, int, int], float] = {}
    for h in hard_violations:
        hi_edge = h["but_at_most"]
        if hi_edge["kind"] not in ("start_hw", "mid_hw", "end_hw"):
            continue
        bus_to = parse_bus(h["event"])
        bus_from = hi_edge.get("bus_from")
        if bus_from is None or bus_to is None:
            continue
        key = (hi_edge["kind"], bus_from, bus_to)
        required = hi_edge["edge_hi"] + h["shortfall"]
        needed[key] = max(needed.get(key, 0.0), required)

    return [
        {"kinds": [kind], "bus_from": bf, "bus_to": bt, "lo": None, "hi": math.ceil(hi),
          "label": f"자동제안: {kind} {bf}→{bt} 상한 {math.ceil(hi)}분"}
        for (kind, bf, bt), hi in needed.items()
    ]


# ============================================================
# 10. 전체 파이프라인
# ============================================================

def solve(p: dict, max_rounds: int = 5):
    time_bands = [TimeBand(**b) for b in p.get("time_bands", [])]
    prob = build_problem(p)
    order = [t.key(ev) for t in prob.trips for ev in ("S", "M", "E") if not (ev == "S" and t.is_mid_origin)]

    violations = compact(prob, order, time_bands)
    if violations:
        return prob, verify(prob, time_bands), violations

    for _round in range(max_rounds):
        stretch(prob, order, time_bands)
        apply_stretch_and_fix(prob, order, time_bands)
        problems = verify(prob, time_bands)
        if not problems:
            break

    return prob, verify(prob, time_bands), []


if __name__ == "__main__":
    params = dict(
        n_buses=4, n_morning=3, m_afternoon=3,
        morning_start=5 * 60, afternoon_target_start=12 * 60,
        day_end=23 * 60 + 30, mid_end=None, mid_start=5 * 60 + 30,
        headway_start_lo=8, headway_start_hi=30,
        headway_mid_lo=8, headway_mid_hi=30,
        headway_end_lo=8, headway_end_hi=30,
        rest_lo=8, rest_hi=30,
        tau_sm_default=20, tau_me_default=15,
        mid_origin_overrides=[], override_rules=[],
        time_bands=[dict(basis="start", win_lo=7 * 60, win_hi=9 * 60, slack=0, peak=15)],
    )
    prob, problems, hard_violations = solve(params)

    def fmt(m):
        h, mm = divmod(int(round(m)), 60)
        return f"{h:02d}:{mm:02d}"

    session_order = {"morning": 0, "afternoon": 1}
    for t in sorted(prob.trips, key=lambda t: (session_order[t.session], t.round_robin_idx)):
        s = fmt(prob.values[t.key("S")]) if not t.is_mid_origin else "-"
        m = fmt(prob.values[t.key("M")])
        e = fmt(prob.values[t.key("E")])
        tag = " (mid-origin)" if t.is_mid_origin else ""
        print(f"{t.session} bus{t.bus} occ{t.occ}: S={s} M={m} E={e}{tag}")

    print("\n하드 위반:", hard_violations)
    print("검증 위반:", problems)
    print(f"\n총 {len(prob.history)}개의 스냅샷 기록됨")
