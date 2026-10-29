"""
소규모 유역 합리식 수리계산 엔진

흐름: 도달시간 → 강우강도(WAMIS 확률강우강도식) → 방재성능목표 비교 → 첨두유량(합리식)
      → 배수로 단면 통수능 검토(Manning 등류, 사각·원형)

단위 약속
  유역면적 A : ha        유로연장 L : m        표고차 H : m
  강우강도 I : mm/hr     시간 t : 분            유량 Q : ㎥/s
  배수로 경사 S : m/m (화면에서는 %로 입력)
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "idf_general_yeongnam.csv"


# ---------------------------------------------------------------------------
# 1. 확률강우강도식 (WAMIS 지역빈도해석, General형  I = a / (t^n + b))
# ---------------------------------------------------------------------------
@dataclass
class Station:
    code: str
    name: str
    agency: str
    basin: str
    region: int
    lat: float
    lon: float
    coef: dict = field(default_factory=dict)   # {재현기간: (a, b, n)}

    @property
    def label(self) -> str:
        return f"{self.name} ({self.agency} · {self.basin} · {self.code})"


def load_stations(path: Path = DATA) -> dict[str, Station]:
    st: dict[str, Station] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            s = st.get(r["code"])
            if s is None:
                s = Station(r["code"], r["name"], r["agency"], r["basin"], int(r["region"]),
                            float(r["lat"]), float(r["lon"]))
                st[r["code"]] = s
            s.coef[int(r["T"])] = (float(r["a"]), float(r["b"]), float(r["n"]))
    return st


def intensity(coef: tuple, t_min: float) -> float:
    """강우강도 I (mm/hr) = a / (t^n + b)"""
    a, b, n = coef
    return a / (t_min ** n + b)


def nearest_stations(stations: dict[str, Station], lat: float, lon: float, k: int = 5):
    """위·경도로 가까운 관측소 k개 (거리 km)"""
    def dist(s: Station) -> float:
        p1, p2 = math.radians(lat), math.radians(s.lat)
        dp, dl = p2 - p1, math.radians(s.lon - lon)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 6371.0 * 2 * math.asin(math.sqrt(h))
    return sorted(((dist(s), s) for s in stations.values()), key=lambda x: x[0])[:k]


# ---------------------------------------------------------------------------
# 2. 도달시간
# ---------------------------------------------------------------------------
TC_METHODS = {
    "kraven2": "유입시간 + 유하시간 (Kraven II, 경사별 유속표)",
    "rziha": "유입시간 + 유하시간 (Rziha)",
    "kirpich": "Kirpich (전체 도달시간)",
    "velocity": "유입시간 + 유하시간 (유속 직접 입력)",
    "direct": "도달시간 직접 입력",
}


def kerby_minutes(L_m: float, N: float, S: float) -> float:
    """Kerby 유입시간 (분) = 1.44 (L·N / √S)^0.467,  L: 표면류 길이(m)"""
    return 1.44 * (L_m * N / math.sqrt(S)) ** 0.467


def kraven2_velocity(S: float) -> float:
    """Kraven(II) 경사별 평균유속 (m/s): S>1/100 → 3.5, 1/200~1/100 → 3.0, S<1/200 → 2.1"""
    if S > 1 / 100:
        return 3.5
    if S >= 1 / 200:
        return 3.0
    return 2.1


def rziha_velocity(S: float) -> float:
    """Rziha 평균유속: W = 72 (H/L)^0.6 km/hr → m/s 로 환산 (= 20 (H/L)^0.6)"""
    return 72.0 * S ** 0.6 / 3.6


def kirpich_minutes(L_m: float, S: float) -> float:
    """Kirpich 도달시간 (분) = 3.976 L^0.77 S^-0.385,  L: km"""
    return 3.976 * (L_m / 1000.0) ** 0.77 * S ** -0.385


@dataclass
class TcResult:
    method: str
    slope: float          # 유역 평균경사 H/L
    t_inlet: float        # 유입시간 (분)
    velocity: float | None
    t_travel: float       # 유하시간 (분)
    tc: float             # 도달시간 (분)


def time_of_concentration(method: str, L_m: float, H_m: float, t_inlet: float = 0.0,
                          velocity: float | None = None, tc_direct: float | None = None) -> TcResult:
    S = H_m / L_m if L_m > 0 else 0.0
    if method == "direct":
        return TcResult(method, S, 0.0, None, 0.0, float(tc_direct))
    if method == "kirpich":
        tc = kirpich_minutes(L_m, S)
        return TcResult(method, S, 0.0, None, tc, tc)
    if method == "kraven2":
        V = kraven2_velocity(S)
    elif method == "rziha":
        V = rziha_velocity(S)
    elif method == "velocity":
        V = float(velocity)
    else:
        raise ValueError(method)
    t2 = L_m / V / 60.0
    return TcResult(method, S, t_inlet, V, t2, t_inlet + t2)


# ---------------------------------------------------------------------------
# 3. 첨두유량 (합리식) + 방재성능목표 비교
# ---------------------------------------------------------------------------
def rational_q(C: float, I: float, A_ha: float) -> float:
    """Q (㎥/s) = C·I·A / 360   (I: mm/hr, A: ha)"""
    return C * I * A_ha / 360.0


@dataclass
class RainResult:
    I_idf: float             # 강우강도식 강우강도 (도달시간 기준)
    depth_1h_idf: float      # 강우강도식 1시간 강우량 (mm)
    target_1h: float | None  # 방재성능목표 1시간 강우량 (mm)
    ratio: float | None      # 목표 / 강우강도식 1시간
    I_target: float | None   # 방재성능목표 비율로 보정한 강우강도
    I_design: float          # 설계에 쓴 강우강도
    basis: str               # 설계 강우강도 근거


def design_rain(coef: tuple, tc: float, target_1h: float | None, apply_target: bool) -> RainResult:
    I = intensity(coef, tc)
    d1 = intensity(coef, 60.0)               # 1시간 강도(mm/hr) = 1시간 강우량(mm)
    if target_1h and target_1h > 0:
        k = target_1h / d1
        It = I * k
    else:
        k = It = None
    if apply_target and k is not None and k > 1.0:
        return RainResult(I, d1, target_1h, k, It, It, "방재성능목표 보정")
    return RainResult(I, d1, target_1h, k, It, I, "확률강우강도식")


# ---------------------------------------------------------------------------
# 4. 배수로 단면 통수능 (Manning 등류)
# ---------------------------------------------------------------------------
def rect_geom(B: float, y: float):
    A = B * y
    P = B + 2 * y
    return A, P


def circ_geom(D: float, y: float):
    y = min(max(y, 0.0), D)
    th = 2 * math.acos(1 - 2 * y / D)       # 중심각(rad)
    A = D * D / 8 * (th - math.sin(th))
    P = D * th / 2
    return A, P


def manning_q(A: float, P: float, n: float, S: float) -> float:
    if A <= 0 or P <= 0:
        return 0.0
    R = A / P
    return A * R ** (2 / 3) * math.sqrt(S) / n


@dataclass
class SectionResult:
    shape: str
    size_text: str
    height: float            # 사각 H 또는 원형 D (m)
    Q_design: float          # 설계유량 (여유율 반영)
    depth: float | None      # 등류수심 (m), 만수 초과면 None
    depth_ratio: float | None
    velocity: float | None   # 등류수심에서의 유속 (m/s)
    Q_allow: float           # 허용수심(높이×비율)일 때 통수능
    Q_full: float            # 사각: 만수(H) / 원형: 최대통수능(약 0.94D)
    allow_ratio: float
    depth_ok: bool
    vel_ok: bool | None
    vel_msg: str
    ok: bool


def _solve_depth(geom, size: float, ymax: float, Q: float, n: float, S: float) -> float | None:
    f = lambda y: manning_q(*geom(size, y), n, S)
    if f(ymax) < Q:
        return None
    lo, hi = 0.0, ymax
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) < Q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def check_section(shape: str, dims: dict, Q: float, n: float, S: float,
                  allow_ratio: float = 0.8, safety: float = 1.0,
                  vmin: float = 0.8, vmax: float = 3.0) -> SectionResult:
    Qd = Q * safety
    if shape == "rect":
        B, H = dims["B"], dims["H"]
        geom, size, height = rect_geom, B, H
        ymax = H
        Q_full = manning_q(*rect_geom(B, H), n, S)
        text = f"사각 B {B:.2f} × H {H:.2f} m"
    else:
        D = dims["D"]
        geom, size, height = circ_geom, D, D
        ymax = 0.938 * D                                  # 원형관 최대통수 수심
        Q_full = manning_q(*circ_geom(D, ymax), n, S)
        text = f"원형 D {D * 1000:.0f} mm"
    Q_allow = manning_q(*geom(size, allow_ratio * height), n, S)
    y = _solve_depth(geom, size, ymax, Qd, n, S)
    if y is None:
        return SectionResult(shape, text, height, Qd, None, None, None, Q_allow, Q_full,
                             allow_ratio, False, None, "단면 초과 — 유속 검토 불가", False)
    A, _ = geom(size, y)
    V = Qd / A
    depth_ok = y <= allow_ratio * height + 1e-9
    if V < vmin:
        vel_ok, msg = False, f"최소유속 {vmin:.1f} m/s 미달 (토사 퇴적 우려)"
    elif V > vmax:
        vel_ok, msg = False, f"최대유속 {vmax:.1f} m/s 초과 (세굴·마모 우려)"
    else:
        vel_ok, msg = True, f"허용범위 {vmin:.1f}~{vmax:.1f} m/s 이내"
    return SectionResult(shape, text, height, Qd, y, y / height, V, Q_allow, Q_full,
                         allow_ratio, depth_ok, vel_ok, msg, depth_ok and vel_ok)


PIPE_DIAMETERS_MM = [300, 350, 400, 450, 500, 600, 700, 800, 900, 1000, 1100, 1200,
                     1350, 1500, 1650, 1800, 2000]


def min_pipe(Q: float, n: float, S: float, allow_ratio: float = 0.8, safety: float = 1.0):
    """수심 기준(허용수심 이하)을 만족하는 최소 규격 원형관 (mm)"""
    for d in PIPE_DIAMETERS_MM:
        D = d / 1000
        if manning_q(*circ_geom(D, allow_ratio * D), n, S) >= Q * safety:
            return d
    return None


def min_rect_height(Q: float, B: float, n: float, S: float, allow_ratio: float = 0.8,
                    safety: float = 1.0, step: float = 0.05):
    """폭 B 고정 시 수심 기준을 만족하는 최소 높이 H (step 단위 올림, m)"""
    y = _solve_depth(rect_geom, B, 50.0, Q * safety, n, S)
    if y is None:
        return None
    H = y / allow_ratio
    return math.ceil(H / step - 1e-9) * step
