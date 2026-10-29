"""계산엔진 검산 (손계산 값과 비교).  실행: python -m pytest tests  또는  python tests/test_calc.py"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import calc as C  # noqa: E402


def close(a, b, tol=1e-3):
    assert abs(a - b) <= tol * max(1.0, abs(b)), (a, b)


def test_idf_mungyeong():
    st = C.load_stations()
    assert len(st) == 210
    m = st["20051273"]                       # 문경(기상청)
    assert m.coef[30] == (1434.855, 8.923926, 0.6652471)
    close(C.intensity(m.coef[30], 60), 59.3865)


def test_tc_formulas():
    close(C.kerby_minutes(30, 0.6, 0.25), 7.676)
    close(C.kirpich_minutes(1000, 0.1), 3.976 * 0.1 ** -0.385)
    close(C.rziha_velocity(0.1), 20 * 0.1 ** 0.6)
    assert C.kraven2_velocity(0.02) == 3.5
    assert C.kraven2_velocity(0.007) == 3.0
    assert C.kraven2_velocity(0.004) == 2.1
    r = C.time_of_concentration("kraven2", 502.53, 103.0, t_inlet=7.0)
    close(r.tc, 7.0 + 502.53 / 3.5 / 60)


def test_rational():
    close(C.rational_q(0.744, 111.86, 3.9401), 0.744 * 111.86 * 3.9401 / 360)


def test_manning_rect_and_circle():
    A, P = C.rect_geom(0.5, 0.4)
    close(C.manning_q(A, P, 0.015, 0.01), 0.3828)        # 손계산 0.383
    D = 0.6
    A, P = C.circ_geom(D, D)
    close(A, math.pi * D * D / 4)
    qf = C.manning_q(A, P, 0.013, 0.01)
    close(qf, 0.6140)                                      # 손계산 0.614
    A2, P2 = C.circ_geom(D, 0.938 * D)
    close(C.manning_q(A2, P2, 0.013, 0.01) / qf, 1.0757)  # 최대통수능 ≈ 만관의 1.076배


def test_check_section_logic():
    ok = C.check_section("rect", {"B": 0.5, "H": 0.5}, 0.30, 0.015, 0.01)
    assert ok.depth_ok and ok.depth_ratio < 0.8
    ng = C.check_section("rect", {"B": 0.5, "H": 0.5}, 0.40, 0.015, 0.01)
    assert not ng.depth_ok                                 # 수심 80% 초과
    over = C.check_section("circ", {"D": 0.3}, 1.0, 0.013, 0.01)
    assert over.depth is None and not over.ok              # 단면 초과


def test_target_ratio():
    coef = (1434.855, 8.923926, 0.6652471)
    r = C.design_rain(coef, 10.0, 90.0, apply_target=True)
    close(r.ratio, 90.0 / C.intensity(coef, 60))
    close(r.I_design, C.intensity(coef, 10.0) * r.ratio)
    r2 = C.design_rain(coef, 10.0, 50.0, apply_target=True)   # 목표가 더 작으면 보정 안 함
    assert r2.basis == "확률강우강도식"


def test_equivalent_slope():
    segs = [("1", 30, 150.0, 149.4), ("2", 25, 149.4, 149.025), ("3", 40, 149.025, 147.825),
            ("4", 35, 147.825, 147.475), ("5", 30, 147.475, 146.725), ("6", 40, 146.725, 146.245)]
    r = C.equivalent_slope(segs)
    Ls = [30, 25, 40, 35, 30, 40]
    Ss = [0.02, 0.015, 0.03, 0.01, 0.025, 0.012]
    close(r.S_eq, (sum(Ls) / sum(L / math.sqrt(S) for L, S in zip(Ls, Ss))) ** 2)   # 손계산 0.016605
    close(r.S_avg, 3.755 / 200)
    assert r.S_min == min(r.rows, key=lambda x: x[5])[5] and not r.errors
    bad = C.equivalent_slope([("a", 50, 10, 9.5), ("b", 50, 9.5, 9.6)])
    assert bad.errors                                         # 역경사 구간 검출


def test_kraven_cont_matches_old_excel():
    # 기존 엑셀 양식(연속형 Kraven + Kerby) 모곡리 D지점 tc ≈ 9.54분
    r = C.time_of_concentration("kraven_cont", 502.53, 103.0, t_inlet=C.kerby_minutes(30, 0.6, 0.25))
    close(r.tc, 7.6764 + 502.53 / 4.5 / 60)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("통과:", name)
