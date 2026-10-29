"""
소규모 유역 합리식 수리계산 웹앱
  ① 강우 조건(관측소·재현기간·방재성능목표) → ② 유역·유량(도달시간·합리식)
  → ③ 배수로 구간별 경사(등가경사) → ④ 단면 통수 검토(홍수량 vs 계획홍수량) + 유속 확인
"""
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import calc as C
from core import report

st.set_page_config(page_title="합리식 수리계산", page_icon="💧", layout="wide")


@st.cache_data
def stations():
    return C.load_stations()


ST = stations()
BASIN_ORDER = ["낙동강", "형산강", "낙동강동해권", "태화강", "회야강", "서낙동강", "낙동강남해권", "금강"]
ALL_CODES = sorted(ST, key=lambda k: (BASIN_ORDER.index(ST[k].basin) if ST[k].basin in BASIN_ORDER else 99,
                                      ST[k].name, ST[k].agency))
RETURN_PERIODS = sorted(next(iter(ST.values())).coef)

N_PRESETS = {
    "콘크리트 측구·U형 측구 (0.015)": 0.015,
    "콘크리트 흄관 (0.013)": 0.013,
    "이중벽 PE관·PVC관 (0.011)": 0.011,
    "콘크리트 박스 (0.015)": 0.015,
    "돌붙임·석축 수로 (0.025)": 0.025,
    "토사 수로 (0.030)": 0.030,
    "직접 입력": None,
}


def card(html: str, color: str, bg: str):
    st.markdown(f'<div style="border:2px solid {color};background:{bg};border-radius:12px;'
                f'padding:14px 18px;margin:6px 0 10px 0">{html}</div>', unsafe_allow_html=True)


st.title("💧 소규모 유역 합리식 수리계산")
st.caption("WAMIS 확률강우강도식(지역빈도해석, General형) · 합리식 · 등가경사 · Manning 등류 단면 검토 — "
           "대구·경북·경남·울산·부산 강우관측소 210곳")

left, right = st.columns([1, 1.15], gap="large")

# ===========================================================================
# 입력
# ===========================================================================
with left:
    # ---------------- ① 강우 조건 ----------------
    st.subheader("① 강우 조건")
    query = st.text_input("🔍 강우관측소 검색", value="",
                          placeholder="예: 문경, 영천, 기상청, 형산강 — 여러 단어는 띄어쓰기")
    toks = [t for t in query.split() if t]
    codes = [k for k in ALL_CODES
             if all(t in f"{ST[k].name} {ST[k].agency} {ST[k].basin} {ST[k].code}" for t in toks)]
    if toks and not codes:
        st.warning(f"'{query}' 이(가) 들어간 관측소가 없습니다. 전체 목록을 보여 드립니다. "
                   "관측소 이름은 읍·면·동 이름인 경우가 많으니 아래 '좌표로 찾기'도 써 보세요.")
        codes = ALL_CODES
    elif toks:
        st.caption(f"검색 결과 {len(codes)}곳")
    default_idx = codes.index("20051273") if "20051273" in codes else 0
    code = st.selectbox("강우관측소 (지역)", codes, index=default_idx, format_func=lambda k: ST[k].label)
    stn = ST[code]
    st.caption(f"위치 : 북위 {stn.lat:.4f}°, 동경 {stn.lon:.4f}°  ·  지역빈도해석 지역구분 {stn.region}")

    with st.expander("📍 현장 좌표로 가까운 관측소 찾기"):
        cc1, cc2 = st.columns(2)
        q_lat = cc1.number_input("위도 (°)", value=36.6000, format="%.4f", step=0.01)
        q_lon = cc2.number_input("경도 (°)", value=128.1500, format="%.4f", step=0.01)
        near = C.nearest_stations(ST, q_lat, q_lon, 5)
        st.dataframe(pd.DataFrame({"관측소": [s.label for _, s in near],
                                   "거리 (km)": [round(d, 1) for d, _ in near]}),
                     hide_index=True, use_container_width=True)
        st.caption("관측소 이름을 위 검색창에 넣어 고르세요. 표준지침은 유역을 지배하는 관측소(티센망)를 쓰도록 하고 있습니다.")

    T = st.selectbox("재현기간 (년)", RETURN_PERIODS,
                     index=RETURN_PERIODS.index(50) if 50 in RETURN_PERIODS else 0)

    target = st.number_input("방재성능목표 1시간 강우량 (mm)", min_value=0.0, value=0.0, step=1.0,
                             help="해당 시·군이 공고한 지역별 방재성능목표 중 1시간 값. 모르면 0으로 두면 비교를 생략합니다.")
    apply_target = st.checkbox("방재성능목표가 더 크면 설계 강우강도에 반영 (비율 보정)", value=False,
                               help="강우강도식의 1시간 강우량보다 목표값이 크면, 그 비율만큼 도달시간 강우강도를 키워서 씁니다.")

    # ---------------- ② 유역·유량 ----------------
    st.subheader("② 유역 및 홍수량")
    st.caption("기본값은 예시(문경 모곡리 D지점)입니다. 현장 값으로 바꿔 주세요.")
    A_ha = st.number_input("유역면적 A (ha)", min_value=0.0001, value=3.9401, step=0.1, format="%.4f")
    st.caption(f"= {A_ha * 10000:,.0f} ㎡ = {A_ha / 100:.4f} ㎢")

    c_mode = st.radio("유출계수 C", ["직접 입력", "토지이용별 가중평균"], horizontal=True)
    if c_mode == "직접 입력":
        C_val = st.number_input("유출계수 C", min_value=0.01, max_value=1.0, value=0.744, step=0.01, format="%.3f")
        landuse_rows = [("직접 입력 C", round(A_ha * 10000, 3), C_val)]
    else:
        lu_default = pd.DataFrame({"토지이용": ["밭", "임야", "도로", "초지", "나지"],
                                   "면적 (㎡)": [11211.0, 9841.0, 12862.0, 5038.0, 449.0],
                                   "유출계수": [0.60, 0.80, 0.95, 0.45, 0.50]})
        lu = st.data_editor(lu_default, num_rows="dynamic", hide_index=True, use_container_width=True)
        lu = lu.dropna()
        tot = float(lu["면적 (㎡)"].sum())
        C_val = float((lu["면적 (㎡)"] * lu["유출계수"]).sum() / tot) if tot > 0 else 0.0
        st.caption(f"가중평균 C = {C_val:.3f}  (면적 합계 {tot:,.0f} ㎡ — 위 유역면적과 맞는지 확인하세요)")
        landuse_rows = [(str(r["토지이용"]), float(r["면적 (㎡)"]), float(r["유출계수"])) for _, r in lu.iterrows()]

    cl1, cl2 = st.columns(2)
    L_m = cl1.number_input("유로연장 L (m)", min_value=1.0, value=502.53, step=10.0)
    H_m = cl2.number_input("표고차 H (m)", min_value=0.01, value=103.0, step=1.0)
    st.caption(f"유역 평균경사 H/L = {H_m / L_m:.4f} ({H_m / L_m * 100:.2f}%, 1/{L_m / H_m:.0f})")

    method = st.selectbox("도달시간 산정방법", list(C.TC_METHODS), format_func=C.TC_METHODS.get)
    t_inlet, vel_in, tc_in = 0.0, 3.0, 10.0
    inlet_mode, t1_direct, kl, kn, ks = "direct", 7.0, 30.0, 0.6, 0.25
    if method in ("kraven2", "kraven_cont", "rziha", "velocity"):
        in_mode = st.radio("유입시간", ["직접 입력", "Kerby 공식"], horizontal=True)
        if in_mode == "직접 입력":
            t1_direct = st.number_input("유입시간 (분)", min_value=0.0, value=7.0, step=1.0)
            t_inlet = t1_direct
        else:
            inlet_mode = "kerby"
            k1, k2, k3 = st.columns(3)
            kl = k1.number_input("표면류 길이 (m)", min_value=1.0, value=30.0)
            kn = k2.number_input("지체계수 N", min_value=0.01, value=0.6, step=0.05,
                                 help="포장면 0.02, 나지 0.1, 초지 0.3~0.4, 산림 0.6~0.8")
            ks = k3.number_input("사면경사 (m/m)", min_value=0.001, value=0.25, step=0.01, format="%.3f")
            t_inlet = C.kerby_minutes(kl, kn, ks)
            st.caption(f"Kerby 유입시간 = 1.44 × ({kl:g}×{kn:g}/√{ks:g})^0.467 = {t_inlet:.2f} 분")
        if method == "velocity":
            vel_in = st.number_input("유하 평균유속 V (m/s)", min_value=0.1, value=3.0, step=0.1)
    elif method == "direct":
        tc_in = st.number_input("도달시간 tc (분)", min_value=1.0, value=10.0, step=1.0)

    # ---------------- ③ 배수로 구간별 경사 ----------------
    st.subheader("③ 배수로 구간별 경사 → 등가경사")
    st.caption("구간별 연장과 시점·종점 표고를 넣으면 경사를 계산해 등가경사로 묶습니다. 행 추가는 표 아래 ➕.")
    seg_default = pd.DataFrame({
        "구간": [f"구간{i}" for i in range(1, 7)],
        "연장 (m)": [30.0, 25.0, 40.0, 35.0, 30.0, 40.0],
        "시점 표고 (m)": [150.000, 149.400, 149.025, 147.825, 147.475, 146.725],
        "종점 표고 (m)": [149.400, 149.025, 147.825, 147.475, 146.725, 146.245],
    })
    seg = st.data_editor(seg_default, num_rows="dynamic", hide_index=True, use_container_width=True,
                         column_config={"시점 표고 (m)": st.column_config.NumberColumn(format="%.3f"),
                                        "종점 표고 (m)": st.column_config.NumberColumn(format="%.3f")})
    seg = seg.dropna(subset=["연장 (m)", "시점 표고 (m)", "종점 표고 (m)"])
    segments = [(str(r["구간"]) if pd.notna(r["구간"]) else f"구간{i + 1}", float(r["연장 (m)"]),
                 float(r["시점 표고 (m)"]), float(r["종점 표고 (m)"])) for i, (_, r) in enumerate(seg.iterrows())]
    slope = C.equivalent_slope(segments)
    if slope.rows:
        st.dataframe(pd.DataFrame({"구간": [r[0] for r in slope.rows],
                                   "연장 (m)": [r[1] for r in slope.rows],
                                   "낙차 (m)": [round(r[4], 3) for r in slope.rows],
                                   "경사 (%)": [round(r[5] * 100, 3) for r in slope.rows]}),
                     hide_index=True, use_container_width=True)
    if slope.errors:
        for e in slope.errors:
            st.error(e)
    else:
        e1, e2, e3 = st.columns(3)
        e1.metric("등가경사 (설계 적용)", f"{slope.S_eq * 100:.3f} %")
        e2.metric("단순 평균경사", f"{slope.S_avg * 100:.3f} %")
        e3.metric("최소 ~ 최대", f"{slope.S_min * 100:.2f} ~ {slope.S_max * 100:.2f} %")
        st.caption(f"총연장 {slope.total_length:.2f} m, 총낙차 {slope.total_drop:.3f} m.  "
                   "등가경사 = (ΣL / Σ(L/√S))² — 구간별로 흘러가는 시간을 합한 것과 같아지는 하나의 경사")

    # ---------------- ④ 배수로 단면 ----------------
    st.subheader("④ 계획 배수로 단면")
    shape = st.radio("단면 형식", ["사각 (측구·암거)", "원형 (관)"], horizontal=True)
    B, Hs, D_mm = 0.80, 0.80, 800
    if shape.startswith("사각"):
        s1, s2 = st.columns(2)
        B = s1.number_input("폭 B (m)", min_value=0.05, value=0.80, step=0.05, format="%.2f")
        Hs = s2.number_input("높이 H (m)", min_value=0.05, value=0.80, step=0.05, format="%.2f")
        dims, shape_key = {"B": B, "H": Hs}, "rect"
    else:
        D_mm = st.number_input("관경 D (mm)", min_value=100, value=800, step=50)
        dims, shape_key = {"D": D_mm / 1000}, "circ"

    preset = st.selectbox("조도계수 n", list(N_PRESETS))
    n_val = N_PRESETS[preset] if N_PRESETS[preset] is not None else \
        st.number_input("조도계수 n 직접 입력", min_value=0.005, value=0.015, step=0.001, format="%.3f")

    with st.expander("⚙️ 판정 기준"):
        j1, j2 = st.columns(2)
        allow = j1.number_input("허용 수심비 (높이·관경 대비)", min_value=0.3, max_value=1.0, value=0.8, step=0.05,
                                help="실무 기준 80% — 이 수심에서 단면이 흘릴 수 있는 양이 계획홍수량")
        safety = j2.number_input("홍수량 여유율 (배)", min_value=1.0, value=1.0, step=0.05,
                                 help="홍수량에 곱하는 값. 1.0이면 여유율 없이 비교")
        v1, v2 = st.columns(2)
        vmin = v1.number_input("최소유속 (m/s)", min_value=0.0, value=0.8, step=0.1)
        vmax = v2.number_input("최대유속 (m/s)", min_value=0.5, value=3.0, step=0.1)

# ===========================================================================
# 계산
# ===========================================================================
tc = C.time_of_concentration(method, L_m, H_m, t_inlet, vel_in, tc_in)
rain = C.design_rain(stn.coef[T], tc.tc, target if target > 0 else None, apply_target)
Q = C.rational_q(C_val, rain.I_design, A_ha)
slope_ok = not slope.errors
if slope_ok:
    S = slope.S_eq
    sec = C.check_section(shape_key, dims, Q, n_val, S, allow, safety, vmin, vmax)
    sec_max = C.check_section(shape_key, dims, Q, n_val, slope.S_max, allow, safety, vmin, vmax)
    sec_min = C.check_section(shape_key, dims, Q, n_val, slope.S_min, allow, safety, vmin, vmax)
    pass_cap = sec.Q_allow >= sec.Q_design

# ===========================================================================
# 결과
# ===========================================================================
with right:
    st.subheader("결과")
    m1, m2, m3 = st.columns(3)
    m1.metric("도달시간 tc (분)", f"{tc.tc:.1f}")
    m2.metric("강우강도 I (mm/hr)", f"{rain.I_design:.1f}", help=f"근거: {rain.basis}")
    m3.metric("홍수량 Q (㎥/s)", f"{Q:.3f}")

    if not slope_ok:
        st.error("③ 배수로 구간 경사를 고쳐 주세요. 경사가 0 이하인 구간이 있으면 단면 검토를 할 수 없습니다.")
        st.stop()

    # ---------- 단면 통수 판정 (가장 크게) ----------
    diff = sec.Q_allow - sec.Q_design
    margin = sec.Q_allow / sec.Q_design - 1
    q_label = "홍수량 Q" + (f" × {safety:g}" if safety != 1 else "")
    if pass_cap:
        color, bg, head = "#2e7d32", "#e8f5e9", "✅ 통수 가능"
    else:
        color, bg, head = "#c62828", "#ffebee", "❌ 통수 불가"
    card(f'<div style="font-size:15px;color:#444">{sec.size_text} · n {n_val:.3f} · 등가경사 {S * 100:.3f}%'
         f' · 허용수심 {allow * 100:.0f}%</div>'
         f'<div style="font-size:34px;font-weight:800;color:{color};margin:4px 0 8px 0">{head}</div>'
         f'<table style="width:100%;font-size:17px;border-collapse:collapse">'
         f'<tr><td>{q_label}</td><td style="text-align:right;font-weight:700">{sec.Q_design:.3f} ㎥/s</td></tr>'
         f'<tr><td>계획홍수량 Qa (단면 통수량)</td><td style="text-align:right;font-weight:700">{sec.Q_allow:.3f} ㎥/s</td></tr>'
         f'<tr style="border-top:1px solid #bbb"><td>차이 (Qa − Q)</td>'
         f'<td style="text-align:right;font-weight:800;color:{color}">{diff:+.3f} ㎥/s ({margin * 100:+.1f}%)</td></tr>'
         f'</table>', color, bg)

    fig_q = go.Figure(go.Bar(
        x=[sec.Q_design, sec.Q_allow], y=[q_label, "계획홍수량 Qa"], orientation="h",
        marker_color=["#90a4ae", color], text=[f"{sec.Q_design:.3f}", f"{sec.Q_allow:.3f}"],
        textposition="auto"))
    fig_q.update_layout(height=150, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="㎥/s")
    st.plotly_chart(fig_q, use_container_width=True)

    if sec_min.Q_allow < sec.Q_design:
        st.warning(f"가장 완만한 구간(경사 {slope.S_min * 100:.2f}%)에서는 계획홍수량이 {sec_min.Q_allow:.3f} ㎥/s 로 "
                   f"홍수량보다 작습니다. 그 구간만 단면을 키우거나 경사를 확보하는 것을 검토하세요.")
    else:
        st.caption(f"가장 완만한 구간(경사 {slope.S_min * 100:.2f}%)에서도 계획홍수량 {sec_min.Q_allow:.3f} ㎥/s 로 통수 가능합니다.")

    # 단면 그림
    fig = go.Figure()
    hgt = dims.get("H") or dims.get("D")
    if shape_key == "rect":
        Bw, Hh = dims["B"], dims["H"]
        if sec.depth is not None:
            fig.add_shape(type="rect", x0=0, y0=0, x1=Bw, y1=min(sec.depth, Hh),
                          fillcolor="rgba(30,136,229,0.45)", line_width=0)
        fig.add_shape(type="rect", x0=0, y0=0, x1=Bw, y1=Hh, line=dict(color="#444", width=3))
        fig.add_shape(type="line", x0=0, x1=Bw, y0=allow * Hh, y1=allow * Hh,
                      line=dict(color="#e53935", dash="dash", width=2))
        xr, yr = [-0.15 * Bw, 1.15 * Bw], [-0.1 * Hh, 1.1 * Hh]
    else:
        Dd = dims["D"]
        if sec.depth is not None:
            yy, r = min(sec.depth, Dd), Dd / 2
            a0 = math.acos(max(-1.0, min(1.0, (r - yy) / r)))
            pts = [(r + r * math.cos(-math.pi / 2 - a0 + 2 * a0 * t / 200),
                    r + r * math.sin(-math.pi / 2 - a0 + 2 * a0 * t / 200)) for t in range(201)]
            path = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in pts) + " Z"
            fig.add_shape(type="path", path=path, fillcolor="rgba(30,136,229,0.45)", line_width=0)
        fig.add_shape(type="circle", x0=0, y0=0, x1=Dd, y1=Dd, line=dict(color="#444", width=3))
        fig.add_shape(type="line", x0=0, x1=Dd, y0=allow * Dd, y1=allow * Dd,
                      line=dict(color="#e53935", dash="dash", width=2))
        xr, yr = [-0.15 * Dd, 1.15 * Dd], [-0.1 * Dd, 1.1 * Dd]
    fig.add_annotation(x=xr[1], y=allow * hgt, text=f"허용 {allow * 100:.0f}%", showarrow=False,
                       xanchor="right", yanchor="bottom", font=dict(color="#e53935"))
    if sec.depth is not None:
        fig.add_annotation(x=xr[0], y=min(sec.depth, hgt), text=f"수심 {sec.depth:.3f} m", showarrow=False,
                           xanchor="left", yanchor="bottom", font=dict(color="#1565c0"))
    fig.update_xaxes(range=xr, visible=False, scaleanchor="y", scaleratio=1)
    fig.update_yaxes(range=yr, visible=False)
    fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    # ---------- 유속 확인 (별도) ----------
    st.markdown("##### 🌊 유속 확인")
    if sec.velocity is None:
        card('<div style="font-size:16px">단면이 부족해 등류수심을 구할 수 없어 유속 검토를 생략합니다.</div>',
             "#9e9e9e", "#f5f5f5")
    else:
        vcolor, vbg = ("#2e7d32", "#e8f5e9") if sec.vel_ok else ("#ef6c00", "#fff3e0")
        vmax_txt = ""
        if sec_max.velocity is not None:
            ok2 = vmin <= sec_max.velocity <= vmax
            vmax_txt = (f'<div style="font-size:14px;margin-top:6px">가장 급한 구간(경사 {slope.S_max * 100:.2f}%) 유속 '
                        f'<b>{sec_max.velocity:.2f} m/s</b> — {"허용범위 이내" if ok2 else "허용범위 밖"}</div>')
        card(f'<div style="font-size:15px;color:#444">등가경사 {S * 100:.3f}% 에서의 등류 유속 '
             f'(수심 {sec.depth:.3f} m, 수심비 {sec.depth_ratio * 100:.1f}%)</div>'
             f'<div style="font-size:26px;font-weight:800;color:{vcolor};margin:4px 0">V = {sec.velocity:.2f} m/s</div>'
             f'<div style="font-size:15px">{"✅ " if sec.vel_ok else "⚠️ "}{sec.vel_msg}</div>{vmax_txt}',
             vcolor, vbg)
        if not sec.vel_ok and sec.velocity > vmax:
            st.caption("유속이 빠르면: 집수정 낙차로 배수로 경사 줄이기, 돌붙임 등 조도 큰 수로, 단면을 넓고 얕게 등을 검토하세요.")

    # 참고 규격
    ref_pipe = C.min_pipe(Q, n_val, S, allow, safety)
    if shape_key == "rect":
        ref_h = C.min_rect_height(Q, dims["B"], n_val, S, allow, safety)
        if ref_h is not None:
            st.caption(f"참고: 폭 B {dims['B']:.2f} m 이면 통수 가능한 최소 높이는 약 {ref_h:.2f} m 입니다.")
    if ref_pipe:
        st.caption(f"참고: 같은 경사·조도계수에서 통수 가능한 최소 원형관은 D{ref_pipe} 입니다.")

    with st.expander("📋 단면 계산 상세"):
        rows = [("홍수량 Q × 여유율", f"{sec.Q_design:.4f} ㎥/s"),
                (f"계획홍수량 Qa (수심 {allow * 100:.0f}%)", f"{sec.Q_allow:.4f} ㎥/s"),
                ("통수능 (만수)" if shape_key == "rect" else "최대 통수능 (수심 약 94%)", f"{sec.Q_full:.4f} ㎥/s"),
                ("등류수심", f"{sec.depth:.3f} m" if sec.depth is not None else "단면 초과"),
                ("수심비", f"{sec.depth_ratio * 100:.1f} %" if sec.depth_ratio is not None else "-"),
                ("유속 (등가경사)", f"{sec.velocity:.2f} m/s" if sec.velocity is not None else "-"),
                ("가장 완만한 구간 계획홍수량", f"{sec_min.Q_allow:.4f} ㎥/s"),
                ("가장 급한 구간 유속", f"{sec_max.velocity:.2f} m/s" if sec_max.velocity is not None else "단면 초과")]
        st.table(pd.DataFrame(rows, columns=["항목", "값"]).set_index("항목"))

    # ---------- 강우 · 방재성능목표 비교 ----------
    st.markdown("##### 강우강도 · 방재성능목표 비교")
    a, b, n = stn.coef[T]
    comp = [("관측소 / 재현기간", f"{stn.name} ({stn.agency}) / {T}년"),
            ("강우강도식", f"I = {a} / (t^{n} + {b})"),
            (f"도달시간 {tc.tc:.1f}분 강우강도", f"{rain.I_idf:.1f} mm/hr"),
            ("강우강도식 1시간 강우량", f"{rain.depth_1h_idf:.1f} mm")]
    if rain.target_1h:
        comp += [("방재성능목표 1시간 강우량", f"{rain.target_1h:.1f} mm"),
                 ("목표 / 강우강도식 (1시간)", f"{rain.ratio:.3f}"),
                 ("목표 비율로 보정한 강우강도", f"{rain.I_target:.1f} mm/hr")]
    comp += [("설계에 적용한 강우강도", f"{rain.I_design:.1f} mm/hr  ({rain.basis})")]
    st.table(pd.DataFrame(comp, columns=["항목", "값"]).set_index("항목"))
    if rain.target_1h:
        if rain.ratio > 1:
            st.warning(f"방재성능목표 1시간 강우량({rain.target_1h:.0f} mm)이 강우강도식 {T}년 빈도 1시간 "
                       f"강우량({rain.depth_1h_idf:.1f} mm)보다 {(rain.ratio - 1) * 100:.0f}% 큽니다."
                       + ("" if apply_target else " 반영하려면 왼쪽 체크박스를 켜세요."))
        else:
            st.info("강우강도식 1시간 강우량이 방재성능목표 이상입니다.")

    ts = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180]
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=ts, y=[C.intensity(stn.coef[T], t) for t in ts], mode="lines+markers",
                              name=f"{T}년 강우강도식"))
    fig2.add_trace(go.Scatter(x=[tc.tc], y=[rain.I_idf], mode="markers", name="도달시간",
                              marker=dict(size=12, color="#e53935")))
    if rain.target_1h:
        fig2.add_trace(go.Scatter(x=[60], y=[rain.target_1h], mode="markers", name="방재성능목표 1시간",
                                  marker=dict(size=12, symbol="diamond", color="#fb8c00")))
    fig2.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="지속기간 (분)",
                       yaxis_title="강우강도 (mm/hr)", legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig2, use_container_width=True)

    # ---------- 계산 과정 ----------
    with st.expander("🧮 계산 과정 (계산서)"):
        lines = [f"1) 유역 평균경사  S = H / L = {H_m:g} / {L_m:g} = {tc.slope:.4f}"]
        if method == "kirpich":
            lines.append(f"2) Kirpich  tc = 3.976 × L(km)^0.77 × S^-0.385 = {tc.tc:.2f} 분")
        elif method == "direct":
            lines.append(f"2) 도달시간 직접 입력  tc = {tc.tc:.2f} 분")
        else:
            vtxt = {"kraven2": "Kraven(II) 경사별 유속표", "kraven_cont": "Kraven 연속식",
                    "rziha": "Rziha  V = 72 × S^0.6 (km/hr)", "velocity": "직접 입력"}[method]
            lines.append(f"2) 유하속도 ({vtxt})  V = {tc.velocity:.2f} m/s")
            lines.append(f"   유하시간  t2 = L / V / 60 = {L_m:g} / {tc.velocity:.2f} / 60 = {tc.t_travel:.2f} 분")
            lines.append(f"   도달시간  tc = 유입시간 {tc.t_inlet:.2f} + 유하시간 {tc.t_travel:.2f} = {tc.tc:.2f} 분")
        lines.append(f"3) 강우강도  I = {a} / ({tc.tc:.2f}^{n} + {b}) = {rain.I_idf:.2f} mm/hr")
        if rain.basis == "방재성능목표 보정":
            lines.append(f"   방재성능목표 보정  I' = {rain.I_idf:.2f} × {rain.ratio:.3f} = {rain.I_design:.2f} mm/hr")
        lines.append(f"4) 홍수량  Q = C·I·A / 360 = {C_val:.3f} × {rain.I_design:.2f} × {A_ha:g} / 360 = {Q:.4f} ㎥/s")
        lines.append(f"5) 등가경사  S = (ΣL / Σ(L/√S))² = ({slope.total_length:.2f} / "
                     f"{sum(r[1] / math.sqrt(r[5]) for r in slope.rows):.3f})² = {S:.5f}")
        lines.append(f"6) 계획홍수량 ({sec.size_text}, 수심 {allow * 100:.0f}%, n = {n_val:.3f})  "
                     f"Qa = A·(1/n)·R^(2/3)·S^(1/2) = {sec.Q_allow:.4f} ㎥/s")
        lines.append(f"   판정  Qa {sec.Q_allow:.4f} {'≥' if pass_cap else '<'} Q {sec.Q_design:.4f}  → "
                     f"{'통수 가능' if pass_cap else '통수 불가'}")
        if sec.depth is not None:
            lines.append(f"7) 유속  등류수심 y = {sec.depth:.3f} m, V = Q / A(y) = {sec.velocity:.2f} m/s")
        st.code("\n".join(lines), language=None)

    # ---------- 엑셀 계산서 ----------
    params = dict(
        site="검토 지점", station_label=stn.label, T=T, a=a, b=b, n=n,
        target=float(target), apply_target=bool(apply_target), A_ha=float(A_ha), landuse=landuse_rows,
        L=float(L_m), H=float(H_m), tc_method=method, inlet_mode=inlet_mode, t1_direct=float(t1_direct),
        kerby_L=float(kl), kerby_N=float(kn), kerby_S=float(ks), V_direct=float(vel_in), tc_direct=float(tc_in),
        segments=segments, shape=shape_key, B=float(B), Hs=float(Hs), D=float(D_mm) / 1000,
        rough=float(n_val), allow=float(allow), safety=float(safety), vmin=float(vmin), vmax=float(vmax))
    st.download_button("📥 엑셀 계산서 내려받기 (수식 연결)", report.build_workbook(params),
                       file_name=f"수리계산서_{stn.name}_{T}년.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", use_container_width=True)
    st.caption("엑셀에서 열면 모든 값이 수식으로 다시 계산됩니다. 파란 글씨 칸을 바꾸면 결과가 따라 바뀝니다.")

st.divider()
st.caption("자료: WAMIS 「전국 하천유역 홍수량 및 확률강우량 정보」 강우강도식(IDF, General형, GEV 분포, 지역빈도해석). "
           "WAMIS 확률강우량은 1시간 이상 자료로 산정된 것이어서, 1시간 미만 지속기간은 식을 연장해 쓰는 셈입니다. "
           "구조물 설계 시에는 「도로 배수시설 설계 및 관리지침」, 「소하천 설계기준」 등 해당 기준과 발주청 협의에 따르세요.")
