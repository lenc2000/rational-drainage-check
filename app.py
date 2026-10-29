"""
소규모 유역 합리식 수리계산 웹앱
  ① 강우 조건(관측소·재현기간·방재성능목표) → ② 유역·유량(도달시간·합리식) → ③ 배수로 단면 통수 검토
"""
import io
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import calc as C

st.set_page_config(page_title="합리식 수리계산", page_icon="💧", layout="wide")


@st.cache_data
def stations():
    return C.load_stations()


ST = stations()
BASIN_ORDER = ["낙동강", "형산강", "낙동강동해권", "태화강", "회야강", "서낙동강", "낙동강남해권", "금강"]
codes = sorted(ST, key=lambda k: (BASIN_ORDER.index(ST[k].basin) if ST[k].basin in BASIN_ORDER else 99,
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

st.title("💧 소규모 유역 합리식 수리계산")
st.caption("WAMIS 확률강우강도식(지역빈도해석, General형) · 합리식 · Manning 등류 단면 검토 — "
           "대구·경북·경남·울산·부산 강우관측소 210곳")

left, right = st.columns([1, 1.15], gap="large")

# ===========================================================================
# 입력
# ===========================================================================
with left:
    # ---------------- ① 강우 조건 ----------------
    st.subheader("① 강우 조건")
    default_idx = codes.index("20051273") if "20051273" in codes else 0
    code = st.selectbox("강우관측소 (지역)", codes, index=default_idx,
                        format_func=lambda k: ST[k].label,
                        help="관측소 이름을 입력하면 검색됩니다. 같은 지명이라도 관할기관별 관측소가 따로 있습니다.")
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
        st.caption("가까운 관측소를 위 목록에서 골라 주세요. 표준지침은 유역을 지배하는 관측소(티센망)를 쓰도록 하고 있습니다.")

    T = st.selectbox("재현기간 (년)", RETURN_PERIODS,
                     index=RETURN_PERIODS.index(50) if 50 in RETURN_PERIODS else 0)

    target = st.number_input("방재성능목표 1시간 강우량 (mm)", min_value=0.0, value=0.0, step=1.0,
                             help="해당 시·군이 공고한 지역별 방재성능목표 중 1시간 값. 모르면 0으로 두면 비교를 생략합니다.")
    apply_target = st.checkbox("방재성능목표가 더 크면 설계 강우강도에 반영 (비율 보정)", value=False,
                               help="강우강도식의 1시간 강우량보다 목표값이 크면, 그 비율만큼 도달시간 강우강도를 키워서 씁니다.")

    # ---------------- ② 유역·유량 ----------------
    st.subheader("② 유역 및 첨두유량")
    st.caption("기본값은 예시(문경 모곡리 D지점)입니다. 현장 값으로 바꿔 주세요.")
    A_ha = st.number_input("유역면적 A (ha)", min_value=0.0001, value=3.9401, step=0.1, format="%.4f")
    st.caption(f"= {A_ha * 10000:,.0f} ㎡ = {A_ha / 100:.4f} ㎢")

    c_mode = st.radio("유출계수 C", ["직접 입력", "토지이용별 가중평균"], horizontal=True)
    if c_mode == "직접 입력":
        C_val = st.number_input("유출계수 C", min_value=0.01, max_value=1.0, value=0.744, step=0.01, format="%.3f")
    else:
        lu_default = pd.DataFrame({"토지이용": ["밭", "임야", "도로", "초지", "나지"],
                                   "면적 (㎡)": [11211.0, 9841.0, 12862.0, 5038.0, 449.0],
                                   "유출계수": [0.60, 0.80, 0.95, 0.45, 0.50]})
        lu = st.data_editor(lu_default, num_rows="dynamic", hide_index=True, use_container_width=True)
        lu = lu.dropna()
        tot = float(lu["면적 (㎡)"].sum())
        C_val = float((lu["면적 (㎡)"] * lu["유출계수"]).sum() / tot) if tot > 0 else 0.0
        st.caption(f"가중평균 C = {C_val:.3f}  (면적 합계 {tot:,.0f} ㎡ — 위 유역면적과 맞는지 확인하세요)")

    cl1, cl2 = st.columns(2)
    L_m = cl1.number_input("유로연장 L (m)", min_value=1.0, value=502.53, step=10.0)
    H_m = cl2.number_input("표고차 H (m)", min_value=0.01, value=103.0, step=1.0)
    st.caption(f"유역 평균경사 H/L = {H_m / L_m:.4f} ({H_m / L_m * 100:.2f}%, 1/{L_m / H_m:.0f})")

    method = st.selectbox("도달시간 산정방법", list(C.TC_METHODS), format_func=C.TC_METHODS.get)
    t_inlet, vel_in, tc_in = 0.0, None, None
    if method in ("kraven2", "rziha", "velocity"):
        in_mode = st.radio("유입시간", ["직접 입력", "Kerby 공식"], horizontal=True)
        if in_mode == "직접 입력":
            t_inlet = st.number_input("유입시간 (분)", min_value=0.0, value=7.0, step=1.0)
        else:
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

    # ---------------- ③ 배수로 단면 ----------------
    st.subheader("③ 계획 배수로 단면")
    shape = st.radio("단면 형식", ["사각 (측구·암거)", "원형 (관)"], horizontal=True)
    if shape.startswith("사각"):
        s1, s2 = st.columns(2)
        B = s1.number_input("폭 B (m)", min_value=0.05, value=0.80, step=0.05, format="%.2f")
        Hs = s2.number_input("높이 H (m)", min_value=0.05, value=0.80, step=0.05, format="%.2f")
        dims, shape_key = {"B": B, "H": Hs}, "rect"
    else:
        D_mm = st.number_input("관경 D (mm)", min_value=100, value=800, step=50)
        dims, shape_key = {"D": D_mm / 1000}, "circ"

    sc1, sc2 = st.columns(2)
    S_pct = sc1.number_input("배수로 종단경사 (%)", min_value=0.01, value=1.0, step=0.1, format="%.2f",
                             help="계획 배수로의 종단경사. 유역 평균경사와는 다른 값입니다.")
    preset = sc2.selectbox("조도계수 n", list(N_PRESETS))
    n_val = N_PRESETS[preset] if N_PRESETS[preset] is not None else \
        st.number_input("조도계수 n 직접 입력", min_value=0.005, value=0.015, step=0.001, format="%.3f")

    with st.expander("⚙️ 판정 기준"):
        j1, j2 = st.columns(2)
        allow = j1.number_input("허용 수심비 (높이·관경 대비)", min_value=0.3, max_value=1.0, value=0.8, step=0.05,
                                help="실무 기준 80% — 계획수심이 단면 높이(관경)의 80% 이하이면 통수 가능")
        safety = j2.number_input("설계유량 여유율 (배)", min_value=1.0, value=1.0, step=0.05,
                                 help="설계유량에 곱하는 값. 1.0이면 여유율 없이 수심비로만 판정")
        v1, v2 = st.columns(2)
        vmin = v1.number_input("최소유속 (m/s)", min_value=0.0, value=0.8, step=0.1)
        vmax = v2.number_input("최대유속 (m/s)", min_value=0.5, value=3.0, step=0.1)

# ===========================================================================
# 계산
# ===========================================================================
tc = C.time_of_concentration(method, L_m, H_m, t_inlet, vel_in, tc_in)
rain = C.design_rain(stn.coef[T], tc.tc, target if target > 0 else None, apply_target)
Q = C.rational_q(C_val, rain.I_design, A_ha)
S = S_pct / 100
sec = C.check_section(shape_key, dims, Q, n_val, S, allow, safety, vmin, vmax)

# ===========================================================================
# 결과
# ===========================================================================
with right:
    st.subheader("결과")
    m1, m2, m3 = st.columns(3)
    m1.metric("도달시간 tc", f"{tc.tc:.1f} 분")
    m2.metric("설계 강우강도 I", f"{rain.I_design:.1f} mm/hr", help=f"근거: {rain.basis}")
    m3.metric("첨두유량 Q", f"{Q:.3f} ㎥/s")

    verdict = "통수 가능 (OK)" if sec.ok else "통수 불가 (NG)"
    msg = f"**{sec.size_text}** — {verdict}"
    if sec.ok:
        st.success(msg)
    else:
        st.error(msg)

    # ---------- 단면 결과 ----------
    st.markdown("##### 단면 검토")
    rows = [("설계유량 Q × 여유율", f"{sec.Q_design:.3f} ㎥/s")]
    if sec.depth is None:
        rows += [("계획수심", "단면 초과 (만수로도 부족)"),
                 ("수심 판정", "NG")]
    else:
        rows += [("계획수심 (등류)", f"{sec.depth:.3f} m"),
                 ("수심비 (수심 / 높이)", f"{sec.depth_ratio * 100:.1f} %  (허용 {allow * 100:.0f}% 이하)"),
                 ("수심 판정", "OK" if sec.depth_ok else "NG"),
                 ("유속", f"{sec.velocity:.2f} m/s"),
                 ("유속 판정", ("OK — " if sec.vel_ok else "NG — ") + sec.vel_msg)]
    rows += [(f"통수능 (수심 {allow * 100:.0f}%)", f"{sec.Q_allow:.3f} ㎥/s"),
             ("통수능 (만수)" if shape_key == "rect" else "최대 통수능 (수심 약 94%)", f"{sec.Q_full:.3f} ㎥/s"),
             ("통수능 여유", f"{(sec.Q_allow / sec.Q_design - 1) * 100:+.1f} %  (수심 {allow * 100:.0f}% 기준)")]
    st.table(pd.DataFrame(rows, columns=["항목", "값"]).set_index("항목"))

    # 단면 그림
    fig = go.Figure()
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
            yy = min(sec.depth, Dd)
            ts = [i / 200 for i in range(201)]
            pts = []
            r = Dd / 2
            a0 = math.acos(max(-1.0, min(1.0, (r - yy) / r)))
            for t in ts:
                ang = -math.pi / 2 - a0 + 2 * a0 * t
                pts.append((r + r * math.cos(ang), r + r * math.sin(ang)))
            path = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in pts) + " Z"
            fig.add_shape(type="path", path=path, fillcolor="rgba(30,136,229,0.45)", line_width=0)
        fig.add_shape(type="circle", x0=0, y0=0, x1=Dd, y1=Dd, line=dict(color="#444", width=3))
        fig.add_shape(type="line", x0=0, x1=Dd, y0=allow * Dd, y1=allow * Dd,
                      line=dict(color="#e53935", dash="dash", width=2))
        xr, yr = [-0.15 * Dd, 1.15 * Dd], [-0.1 * Dd, 1.1 * Dd]
    fig.add_annotation(x=xr[1], y=allow * (dims.get("H") or dims.get("D")), text=f"허용 {allow * 100:.0f}%",
                       showarrow=False, xanchor="right", yanchor="bottom", font=dict(color="#e53935"))
    fig.update_xaxes(range=xr, visible=False, scaleanchor="y", scaleratio=1)
    fig.update_yaxes(range=yr, visible=False)
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    # 참고 규격
    ref_pipe = C.min_pipe(Q, n_val, S, allow, safety)
    if shape_key == "rect":
        ref_h = C.min_rect_height(Q, dims["B"], n_val, S, allow, safety)
        if ref_h is not None:
            st.caption(f"참고: 폭 B {dims['B']:.2f} m 이면 수심 기준을 만족하는 최소 높이는 약 {ref_h:.2f} m 입니다.")
    if ref_pipe:
        st.caption(f"참고: 같은 경사·조도계수에서 수심 기준을 만족하는 최소 원형관은 D{ref_pipe} 입니다 (유속은 별도 확인).")

    # ---------- 강우 · 방재성능목표 비교 ----------
    st.markdown("##### 강우강도 · 방재성능목표 비교")
    a, b, n = stn.coef[T]
    comp = [("관측소 / 재현기간", f"{stn.name} ({stn.agency}) / {T}년"),
            ("강우강도식", f"I = {a:g} / (t^{n:g} + {b:g})"),
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
    st.caption("※ 방재성능목표는 1시간 '평균' 값이라, 도달시간이 1시간보다 짧은 소규모 유역에 그대로 I로 쓰면 "
               "강우강도가 작게 나옵니다. 그래서 같은 비율로 보정하는 방식을 씁니다.")

    # IDF 곡선
    ts = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180]
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=ts, y=[C.intensity(stn.coef[T], t) for t in ts], mode="lines+markers",
                              name=f"{T}년 강우강도식"))
    fig2.add_trace(go.Scatter(x=[tc.tc], y=[rain.I_idf], mode="markers", name="도달시간",
                              marker=dict(size=12, color="#e53935")))
    if rain.target_1h:
        fig2.add_trace(go.Scatter(x=[60], y=[rain.target_1h], mode="markers", name="방재성능목표 1시간",
                                  marker=dict(size=12, symbol="diamond", color="#fb8c00")))
    fig2.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="지속기간 (분)",
                       yaxis_title="강우강도 (mm/hr)", legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig2, use_container_width=True)

    # ---------- 계산 과정 ----------
    with st.expander("🧮 계산 과정 (계산서)"):
        lines = [f"1) 유역 평균경사  S = H / L = {H_m:g} / {L_m:g} = {tc.slope:.4f}"]
        if method == "kirpich":
            lines.append(f"2) Kirpich  tc = 3.976 × L(km)^0.77 × S^-0.385 = 3.976 × {L_m / 1000:.4f}^0.77 × "
                         f"{tc.slope:.4f}^-0.385 = {tc.tc:.2f} 분")
        elif method == "direct":
            lines.append(f"2) 도달시간 직접 입력  tc = {tc.tc:.2f} 분")
        else:
            vtxt = {"kraven2": "Kraven(II) 경사별 유속표", "rziha": "Rziha  V = 72 × S^0.6 (km/hr)",
                    "velocity": "직접 입력"}[method]
            lines.append(f"2) 유하속도 ({vtxt})  V = {tc.velocity:.2f} m/s")
            lines.append(f"   유하시간  t2 = L / V = {L_m:g} / {tc.velocity:.2f} / 60 = {tc.t_travel:.2f} 분")
            lines.append(f"   도달시간  tc = 유입시간 {tc.t_inlet:.2f} + 유하시간 {tc.t_travel:.2f} = {tc.tc:.2f} 분")
        lines.append(f"3) 강우강도  I = {a:g} / ({tc.tc:.2f}^{n:g} + {b:g}) = {rain.I_idf:.2f} mm/hr")
        if rain.basis == "방재성능목표 보정":
            lines.append(f"   방재성능목표 보정  I' = {rain.I_idf:.2f} × {rain.ratio:.3f} = {rain.I_design:.2f} mm/hr")
        lines.append(f"4) 첨두유량  Q = C·I·A / 360 = {C_val:.3f} × {rain.I_design:.2f} × {A_ha:g} / 360 = {Q:.4f} ㎥/s")
        lines.append(f"5) 단면 ({sec.size_text}, n = {n_val:.3f}, S = {S:.4f})  "
                     f"Manning  Q = (1/n)·A·R^(2/3)·S^(1/2)")
        if sec.depth is not None:
            lines.append(f"   등류수심 y = {sec.depth:.3f} m  → 수심비 {sec.depth_ratio * 100:.1f}%, 유속 V = {sec.velocity:.2f} m/s")
        else:
            lines.append("   만수 통수능이 설계유량보다 작아 등류수심을 구할 수 없음 (단면 부족)")
        st.code("\n".join(lines), language=None)

    # ---------- 내려받기 ----------
    out = pd.DataFrame([
        ("관측소", stn.label), ("재현기간 (년)", T), ("유역면적 (ha)", A_ha), ("유출계수 C", round(C_val, 4)),
        ("유로연장 L (m)", L_m), ("표고차 H (m)", H_m), ("평균경사 S", round(tc.slope, 5)),
        ("도달시간 방법", C.TC_METHODS[method]), ("유입시간 (분)", round(tc.t_inlet, 2)),
        ("유하시간 (분)", round(tc.t_travel, 2)), ("도달시간 (분)", round(tc.tc, 2)),
        ("강우강도식 강우강도 (mm/hr)", round(rain.I_idf, 2)),
        ("강우강도식 1시간 강우량 (mm)", round(rain.depth_1h_idf, 2)),
        ("방재성능목표 1시간 (mm)", rain.target_1h or "-"),
        ("설계 강우강도 (mm/hr)", round(rain.I_design, 2)), ("강우강도 근거", rain.basis),
        ("첨두유량 Q (㎥/s)", round(Q, 4)), ("단면", sec.size_text), ("종단경사 (%)", S_pct),
        ("조도계수 n", n_val), ("허용 수심비", allow), ("여유율", safety),
        ("계획수심 (m)", round(sec.depth, 3) if sec.depth is not None else "단면 초과"),
        ("수심비 (%)", round(sec.depth_ratio * 100, 1) if sec.depth_ratio is not None else "-"),
        ("유속 (m/s)", round(sec.velocity, 2) if sec.velocity is not None else "-"),
        ("통수능-허용수심 (㎥/s)", round(sec.Q_allow, 4)), ("판정", verdict),
    ], columns=["항목", "값"])
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    st.download_button("📥 계산 결과 내려받기 (엑셀에서 열리는 CSV)", buf.getvalue().encode("utf-8-sig"),
                       file_name=f"수리계산_{stn.name}_{T}년.csv", mime="text/csv")

st.divider()
st.caption("자료: WAMIS 「전국 하천유역 홍수량 및 확률강우량 정보」 강우강도식(IDF, General형, GEV 분포, 지역빈도해석). "
           "WAMIS 확률강우량은 1시간 이상 자료로 산정된 것이어서, 1시간 미만 지속기간은 식을 연장해 쓰는 셈입니다. "
           "구조물 설계 시에는 「도로 배수시설 설계 및 관리지침」, 「소하천 설계기준」 등 해당 기준과 발주청 협의에 따르세요.")
