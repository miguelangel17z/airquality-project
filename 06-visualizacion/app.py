import os
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="SI3007 — Calidad del Aire y Clima (EE. UU. 2024)",
    layout="wide",
)

API_BASE = os.environ.get("API_URL", "").rstrip("/")

st.title("Análisis de Calidad del Aire (EPA), Clima (NOAA) y Población (Census 2024)")
st.caption("Arquitectura Data Lake en AWS: RDS + EC2 + URL → S3 Raw → AWS Glue → S3 Trusted → EMR (PySpark) → S3 Refined → Lambda + API Gateway → Streamlit")

with st.sidebar:
    st.header("Conexión a API Gateway")
    api_url = st.text_input("URL base de API Gateway", value=API_BASE, placeholder="https://xxxx.execute-api.us-east-1.amazonaws.com").rstrip("/")
    if st.button("Recargar datos desde API"):
        st.cache_data.clear()

if not api_url:
    st.warning("Configura la variable de entorno API_URL o ingresa la URL de tu API Gateway en la barra lateral.")
    st.stop()


@st.cache_data(ttl=300)
def fetch_dataset(base_url: str, name: str):
    url = f"{base_url}/datasets/{name}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


try:
    kpis = fetch_dataset(api_url, "kpis")[0]
except Exception as exc:
    st.error(f"No se pudo cargar /datasets/kpis desde {api_url}. Verifica que ya ejecutaste la Celda 8 en EMR. Detalle: {exc}")
    st.stop()

# Tarjetas KPI ejecutivas
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Observaciones 2024", f"{int(kpis['total_observations']):,}")
c2.metric("Condados monitoreados", f"{int(kpis['monitored_counties']):,}")
c3.metric("AQI promedio", f"{kpis['mean_aqi']} (med: {kpis['median_aqi']})")
c4.metric("Días con AQI > 100", f"{int(kpis['days_aqi_gt_100']):,} ({kpis['pct_days_aqi_gt_100']}%)")
c5.metric("Ozono definitorio", f"{kpis['pct_ozone_defining']}%")

tab_resumen, tab_p1, tab_p2, tab_p3, tab_p4, tab_p5, tab_p6 = st.tabs([
    "Resumen y Calidad",
    "P1: Ranking Geográfico",
    "P2: Estacionalidad",
    "P3: Laboral vs Fin de Semana",
    "P4: Perfil Climático",
    "P5: Episodios AQI > 100",
    "P6: Correlaciones",
])

with tab_resumen:
    col_a, col_b = st.columns(2)
    cat_df = pd.DataFrame(fetch_dataset(api_url, "category_distribution"))
    param_df = pd.DataFrame(fetch_dataset(api_url, "parameter_distribution"))

    with col_a:
        st.subheader("Distribución por categoría EPA")
        fig = px.bar(cat_df, x="category", y="observations", text="share_pct", color="category")
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Observaciones")
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("Contaminante definitorio del AQI")
        fig = px.bar(param_df, x="defining_parameter", y="total_days", text="share_all_pct", color="defining_parameter")
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Días")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Estadística descriptiva univariada (PySpark)")
    st.dataframe(pd.DataFrame(fetch_dataset(api_url, "univariate_summary")), use_container_width=True)

with tab_p1:
    p1_c = pd.DataFrame(fetch_dataset(api_url, "p1_counties"))
    p1_s = pd.DataFrame(fetch_dataset(api_url, "p1_states"))
    states = ["Todos"] + sorted(p1_c["state_name"].unique().tolist())
    sel_state = st.selectbox("Filtrar condados por estado", states)
    top_n = st.slider("Cantidad a mostrar", 5, 30, 15)

    filtered_c = p1_c if sel_state == "Todos" else p1_c[p1_c["state_name"] == sel_state]
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Top condados por AQI promedio (>=180 días)")
        plot_c = filtered_c.head(top_n).copy()
        plot_c["label"] = plot_c["county_name"] + ", " + plot_c["state_name"]
        fig = px.bar(plot_c.iloc[::-1], x="avg_aqi", y="label", orientation="h", hover_data=["valid_days", "coverage_pct_2024", "pct_ozone", "population_2024"])
        fig.update_layout(yaxis_title="", xaxis_title="AQI promedio diario")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Top estados (igual peso por condado elegible)")
        plot_s = p1_s.head(top_n)
        fig = px.bar(plot_s.iloc[::-1], x="state_avg_aqi", y="state_name", orientation="h", hover_data=["eligible_counties", "coverage_note", "min_county_aqi", "max_county_aqi"])
        fig.update_layout(yaxis_title="", xaxis_title="AQI promedio estatal")
        st.plotly_chart(fig, use_container_width=True)

with tab_p2:
    p2 = pd.DataFrame(fetch_dataset(api_url, "p2_monthly")).sort_values("month")
    st.subheader(f"Estacionalidad mensual 2024 — Panel balanceado ({int(p2['panel_counties'].iloc[0])} condados en los 12 meses)")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=p2["month"], y=p2["p75_aqi"], line=dict(width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=p2["month"], y=p2["p25_aqi"], fill="tonexty", fillcolor="rgba(197,48,48,0.2)", line=dict(width=0), name="Banda IQR (P25–P75)"))
    fig.add_trace(go.Scatter(x=p2["month"], y=p2["mean_aqi"], mode="lines+markers", line=dict(color="#c53030", width=3), name="Media AQI"))
    fig.add_trace(go.Scatter(x=p2["month"], y=p2["mean_tavg_c"], mode="lines+markers", line=dict(color="#2b6cb0", dash="dot"), name="Temp. promedio (°C)", yaxis="y2"))
    fig.update_layout(
        xaxis=dict(tickmode="linear", tick0=1, dtick=1, title="Mes (2024)"),
        yaxis=dict(title="AQI"),
        yaxis2=dict(title="Temperatura promedio (°C)", overlaying="y", side="right"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(p2, use_container_width=True)

with tab_p3:
    p3 = pd.DataFrame(fetch_dataset(api_url, "p3_weekday")).sort_values("month")
    st.subheader("Diferencia de AQI: Fin de semana menos Lunes–Viernes (pares condado-mes >=4 días/grupo)")
    p3["signo"] = p3["mean_diff_aqi"].apply(lambda v: "Mayor en fin de semana" if v > 0 else "Menor en fin de semana")
    fig = px.bar(p3, x="month", y="mean_diff_aqi", color="signo", hover_data=["valid_county_month_pairs", "median_diff_aqi", "pct_weekend_higher"])
    fig.add_hline(y=0, line_color="black")
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(p3, use_container_width=True)

with tab_p4:
    p4 = pd.DataFrame(fetch_dataset(api_url, "p4_climate"))
    st.subheader("P4 (Adaptada a granularidad diaria): AQI según temperatura máxima y precipitación")
    fig = px.bar(p4, x="temp_range", y="avg_aqi", color="rain_condition", barmode="group", text="avg_aqi", hover_data=["observations", "pct_days_aqi_gt_100", "pct_ozone"])
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(p4, use_container_width=True)

with tab_p5:
    p5 = pd.DataFrame(fetch_dataset(api_url, "p5_exceedances"))
    st.subheader("Top condados con mayor % de días con AQI > 100 (>=180 días válidos)")
    top_p5 = p5.head(20).copy()
    top_p5["label"] = top_p5["county_name"] + ", " + top_p5["state_name"]
    fig = px.bar(top_p5.iloc[::-1], x="pct_days_aqi_gt_100", y="label", orientation="h", hover_data=["days_aqi_gt_100", "valid_days", "exceedances_ozone", "exceedances_pm25", "population_2024"])
    fig.update_layout(yaxis_title="", xaxis_title="% de días válidos con AQI > 100")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(p5.head(30), use_container_width=True)

with tab_p6:
    p6 = pd.DataFrame(fetch_dataset(api_url, "p6_correlations"))
    st.subheader("Correlación Pearson vs. Spearman (AQI vs. Temperatura máxima por condado, >=30 pares)")
    fig = px.scatter(
        p6, x="pearson_aqi_tmax", y="spearman_aqi_tmax", size="valid_pairs", color="pct_ozone",
        hover_name="county_name", hover_data=["state_name", "pearson_aqi_prcp", "population_2024"],
        color_continuous_scale="YlOrRd",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(p6.head(30), use_container_width=True)