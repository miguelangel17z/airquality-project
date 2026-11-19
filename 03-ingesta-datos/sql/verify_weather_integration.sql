SELECT COUNT(*) AS noaa_records,
       MIN(measurement_date) AS noaa_first_date,
       MAX(measurement_date) AS noaa_last_date
FROM airquality.daily_weather_by_county;

SELECT COUNT(*) AS matching_records
FROM airquality.v_aqi_weather_daily;

SELECT COUNT(*) AS epa_records,
       COUNT(*) - COUNT(w.state_code) AS unmatched_epa_records,
       ROUND(100.0 * COUNT(w.state_code) / NULLIF(COUNT(*), 0), 2) AS coverage_pct,
       MIN(a.measurement_date) AS epa_first_date,
       MAX(a.measurement_date) AS epa_last_date
FROM airquality.daily_aqi_by_county AS a
LEFT JOIN airquality.daily_weather_by_county AS w
    ON a.state_code = w.state_code
    AND a.county_code = w.county_code
    AND a.measurement_date = w.measurement_date;
