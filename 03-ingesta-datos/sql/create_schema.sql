CREATE DATABASE IF NOT EXISTS airquality
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE IF NOT EXISTS airquality.daily_aqi_by_county (
    state_name          VARCHAR(100)      NOT NULL,
    county_name         VARCHAR(100)      NOT NULL,
    state_code          CHAR(2)           NOT NULL,
    county_code         CHAR(3)           NOT NULL,
    measurement_date    DATE              NOT NULL,
    aqi                 SMALLINT UNSIGNED NOT NULL,
    category            VARCHAR(50)       NOT NULL,
    defining_parameter  VARCHAR(50)       NOT NULL,
    defining_site       VARCHAR(20)       NOT NULL,
    sites_reporting     SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (state_code, county_code, measurement_date),
    INDEX idx_daily_aqi_measurement_date (measurement_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
