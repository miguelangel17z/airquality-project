CREATE DATABASE IF NOT EXISTS air_quality_source
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE IF NOT EXISTS air_quality_source.stations (
    state_code VARCHAR(2) NOT NULL,
    county_code VARCHAR(3) NOT NULL,
    site_number VARCHAR(4) NOT NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    datum VARCHAR(16) NULL,
    elevation_m DECIMAL(20,12) NULL,
    land_use VARCHAR(64) NULL,
    location_setting VARCHAR(64) NULL,
    site_established_date DATE NULL,
    site_closed_date DATE NULL,
    met_state_code VARCHAR(2) NULL,
    met_county_code VARCHAR(3) NULL,
    met_site_number VARCHAR(4) NULL,
    met_site_type VARCHAR(64) NULL,
    met_distance_m DECIMAL(16,6) NULL,
    met_site_direction VARCHAR(8) NULL,
    gmt_offset_hours DECIMAL(6,3) NULL,
    owning_agency VARCHAR(128) NULL,
    local_site_name VARCHAR(128) NULL,
    address VARCHAR(255) NULL,
    postal_code VARCHAR(16) NULL,
    state_name VARCHAR(64) NULL,
    county_name VARCHAR(128) NULL,
    city_name VARCHAR(128) NULL,
    cbsa_name VARCHAR(128) NULL,
    tribe_name VARCHAR(128) NULL,
    extraction_date DATE NULL,
    PRIMARY KEY (state_code, county_code, site_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
