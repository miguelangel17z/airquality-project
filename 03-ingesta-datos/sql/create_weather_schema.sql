CREATE TABLE IF NOT EXISTS airquality.daily_weather_by_county (
    state_code        CHAR(2)      NOT NULL,
    county_code       CHAR(3)      NOT NULL,
    measurement_date  DATE         NOT NULL,
    tmax_c            DECIMAL(6,2) NOT NULL,
    tmin_c            DECIMAL(6,2) NOT NULL,
    tavg_c            DECIMAL(6,2) NOT NULL,
    precipitation_mm  DECIMAL(8,2) NOT NULL,
    PRIMARY KEY (state_code, county_code, measurement_date),
    INDEX idx_daily_weather_measurement_date (measurement_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE OR REPLACE SQL SECURITY INVOKER VIEW airquality.v_aqi_weather_daily AS
SELECT
    a.state_name,
    a.county_name,
    a.state_code,
    a.county_code,
    a.measurement_date,
    a.aqi,
    a.category,
    a.defining_parameter,
    a.defining_site,
    a.sites_reporting,
    w.tmax_c,
    w.tmin_c,
    w.tavg_c,
    w.precipitation_mm
FROM airquality.daily_aqi_by_county AS a
INNER JOIN airquality.daily_weather_by_county AS w
    ON a.state_code = w.state_code
    AND a.county_code = w.county_code
    AND a.measurement_date = w.measurement_date;
