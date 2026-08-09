# 버스 배차 스케줄링 - 반복 이완(스프링) 방법

여러 대의 버스가 시작 정류장 → 중간 정류장 → 끝 정류장을 왕복 운행할 때,
배차간격/휴게시간/이동시간/운행 마감 등 제약을 만족하는 각 회차(트립)의
출발·도착 시각을 자동으로 산출하는 도구입니다.

문제 자체의 전체 요구사항(배차간격, 휴게시간, 슬랙/피크타임, mid_start
자동배정 등)은 [`bus_dispatch_problem_definition.md`](bus_dispatch_problem_definition.md)에
정리되어 있습니다.

## 핵심 아이디어

**반복 이완(스프링) 방법**: 왼쪽(이른 시간)부터 트립을 순서대로 배치하면서,
매번 (1) 이미 배치된 노드들을 하한 기준으로 완전히 압축하고 (2) 목표(배차간격을
중간값에 가깝게)에 맞춰 한 번에 늘리는 과정을 반복합니다.

문제를 이벤트(시간점) 사이의 차이 제약 그래프(Simple Temporal Network)로 모델링합니다.
트립 하나는 최대 3개의 이벤트(S: 시작 출발, M: 중간 도착, E: 끝 도착)를 가지며,
이벤트 사이 관계는 전부 `x_to - x_from ∈ [lo, hi]` 형태의 엣지로 표현됩니다.

## 파일 구성

| 파일 | 설명 |
|---|---|
| `spring_schedule.py` | 알고리즘 본체 (UI 없음, 순수 로직) |
| `spring_viewer.py` | tkinter 실시간 뷰어 — 파라미터를 슬라이더/스테퍼/표로 조절하면 즉시 재계산해서 간트 차트로 표시 |
| `bus_dispatch_problem_definition.md` | 원본 문제 정의서 |
| `CLAUDE.md` | 아키텍처/엔진 구조 설명 및 진행 상황 메모 |

## 실행 방법

```bash
# 알고리즘만 콘솔에서 확인
python3 spring_schedule.py

# 실시간 뷰어 (tkinter 필요: apt install python3-tk)
python3 spring_viewer.py
```

## 아키텍처 요약

- **엔진 A (다음 노드)**: `build_problem()`에서 처리 순서를 고정 — mid_start
  자동배정은 전처리에서 먼저 끝내고, 이후는 라운드로빈 순서 + 트립 내부
  S→M→E 순서로 순회.
- **엔진 B (압축)**: `compact()` — 각 이벤트를 들어오는 모든 엣지의 하한 중
  최댓값으로 배치. 위반 시 어떤 두 엣지가 충돌했는지 구조화된 정보로 반환.
- **엔진 C (스트레치)**: `stretch()` + `apply_stretch_and_fix()` — mid_hw
  엣지만 대상으로 중간값 쪽으로 늘림. day_end/mid_end 여유를 예산으로 삼아
  waterfilling 배분.
- **검증**: `verify()` — 모든 엣지를 재확인해 위반 리스트 반환. `solve()`가
  최대 5라운드 압축↔스트레치를 반복.
- **범용 override 규칙 시스템** (`OverrideRule`): 특정 조건(버스쌍/회차/시간대)의
  제약만 사용자가 조정할 수 있는 범용 규칙. `suggest_overrides()`가 압축
  단계 모순을 분석해 자동으로 규칙을 제안.

더 자세한 설계 배경과 알려진 이슈는 [`CLAUDE.md`](CLAUDE.md)를 참고하세요.

## 라이선스

[LICENSE](LICENSE) 참고.
