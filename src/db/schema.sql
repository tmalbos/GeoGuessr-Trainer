-- =============================================================
-- GeoGuessr Trainer DB — Schema
-- =============================================================

-- ENUMS: countries
CREATE TYPE continents AS ENUM (
    'Africa', 'Antarctica', 'Asia', 'Europe', 'North America', 'Oceania', 'South America'
);

-- ENUMS: biomes
CREATE TYPE realm_type AS ENUM (
    'Afrotropic', 'Antarctica', 'Australasia', 'Indomalayan', 'Nearctic', 'Neotropic', 'Oceania', 'Palearctic'
);

-- ENUMS: road lines
CREATE TYPE line_color AS ENUM (
    'white', 'faded white', 'yellow', 'orange-tinted yellow', 'green', 'red', 'orange'
);
CREATE TYPE line_pattern AS ENUM (
    'solid', 'dashed', 'short-dashed', 'squares'
);
CREATE TYPE road_rule AS ENUM (
    'whole-country', 'region-dependant', 'urban', 'rural'
);

-- ENUMS: plates
CREATE TYPE car_type AS ENUM (
    'normal', 'commercial', 'taxi', 'motorcycle', 'military', 'government', 'electric'
);
CREATE TYPE plate_color AS ENUM (
    'white', 'yellow', 'pastel yellow', 'blue', 'green', 'orange', 'brown', 'red', 'black'
);
CREATE TYPE strip_side  AS ENUM ('left', 'right', 'top', 'bottom');
CREATE TYPE plate_shape AS ENUM ('wide', 'short', 'tall', 'standard');

-- 1. STRONG ENTITIES (No dependencies)
CREATE TABLE country (
    code         CHAR(2)     PRIMARY KEY,
    continent    continents  NOT NULL,
    name         VARCHAR(60) UNIQUE NOT NULL,
    flag_url     CHAR(31)    UNIQUE NOT NULL,
    web_domain   CHAR(3)     UNIQUE NOT NULL,
    capital      VARCHAR(30),
    driving_side CHAR(1)     NOT NULL CHECK (driving_side IN ('L', 'R'))
);

CREATE TABLE biome (
    biome_id SERIAL      PRIMARY KEY,
    realm    realm_type  NOT NULL,
    name     VARCHAR(60) NOT NULL
);

CREATE TABLE game (
    challenge_token CHAR(16),
    game_id         CHAR(16),
    map_name        VARCHAR(60) NOT NULL,
    is_daily        BOOLEAN     NOT NULL DEFAULT FALSE,
    played_at       TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (challenge_token, game_id)
);

CREATE TABLE road_line (
    road_line_id  SERIAL       PRIMARY KEY,
    rule          road_rule    NOT NULL,

    inner_color   line_color,
    inner_count   SMALLINT     NOT NULL,
    inner_pattern line_pattern,

    outer_color   line_color,
    outer_count   SMALLINT     NOT NULL,
    outer_pattern line_pattern,

    extra_color   line_color,
    extra_pattern line_pattern,

    CONSTRAINT chk_road_line_inner_count_range CHECK (inner_count BETWEEN 0 and 2),
    CONSTRAINT chk_road_line_outer_count_range CHECK (outer_count BETWEEN 0 and 2),
    CONSTRAINT chk_road_line_inner_fields_consistency CHECK (
        inner_count > 0 OR (inner_color IS NULL AND inner_pattern IS NULL)
    ),
    CONSTRAINT chk_road_line_outer_fields_consistency CHECK (
        outer_count > 0 OR (outer_color IS NULL AND outer_pattern IS NULL)
    ),
    CONSTRAINT chk_road_line_extra_fields_consistency CHECK (extra_color IS NULL OR extra_pattern IS NOT NULL),
    CONSTRAINT uq_road_line_configuration UNIQUE NULLS NOT DISTINCT (
        rule,
        inner_color,
        inner_count,
        inner_pattern,
        outer_color,
        outer_count,
        outer_pattern,
        extra_color,
        extra_pattern
    )
);

CREATE TABLE license_plate (
    license_plate_id    SERIAL      PRIMARY KEY,
    car_type            car_type    NOT NULL,

    front_is_required   BOOLEAN     NOT NULL DEFAULT TRUE,
    front_color         plate_color NOT NULL,
    front_strip_color_1 plate_color,
    front_strip_side_1  strip_side,
    front_strip_color_2 plate_color,
    front_strip_side_2  strip_side,
    front_letter_color  plate_color NOT NULL,
    front_shape         plate_shape NOT NULL,

    back_color          plate_color,
    back_strip_color_1  plate_color,
    back_strip_side_1   strip_side,
    back_strip_color_2  plate_color,
    back_strip_side_2   strip_side,
    back_letter_color   plate_color,
    back_shape          plate_shape,

    CONSTRAINT chk_back_null_when_front_not_required CHECK (
        front_is_required = TRUE
        OR (
            back_color IS NULL AND
            back_strip_color_1 IS NULL AND
            back_strip_side_1 IS NULL AND
            back_strip_color_2 IS NULL AND
            back_strip_side_2 IS NULL AND
            back_letter_color IS NULL AND
            back_shape IS NULL
        )
    ),
    CONSTRAINT chk_front_colors_distinct CHECK (
        front_color <> front_letter_color
        AND (front_strip_color_1 IS NULL OR front_color <> front_strip_color_1)
        AND (front_strip_color_2 IS NULL OR front_color <> front_strip_color_2)
    ),
    CONSTRAINT chk_back_colors_distinct CHECK (
        back_color IS NULL OR (
            back_color <> back_letter_color
            AND (back_strip_color_1 IS NULL OR back_color <> back_strip_color_1)
            AND (back_strip_color_2 IS NULL OR back_color <> back_strip_color_2)
        )
    ),
    CONSTRAINT chk_front_strip_order CHECK (front_strip_color_2 IS NULL OR front_strip_color_1 IS NOT NULL),
    CONSTRAINT chk_back_strip_order CHECK (back_strip_color_2 IS NULL OR back_strip_color_1 IS NOT NULL),
    CONSTRAINT chk_front_strip1_both_or_none CHECK ((front_strip_color_1 IS NULL) = (front_strip_side_1 IS NULL)),
    CONSTRAINT chk_front_strip2_both_or_none CHECK ((front_strip_color_2 IS NULL) = (front_strip_side_2 IS NULL)),
    CONSTRAINT chk_back_strip1_both_or_none CHECK ((back_strip_color_1 IS NULL) = (back_strip_side_1 IS NULL)),
    CONSTRAINT chk_back_strip2_both_or_none CHECK ((back_strip_color_2 IS NULL) = (back_strip_side_2 IS NULL)),
    CONSTRAINT chk_front_strip_sides_distinct CHECK (
        front_strip_side_1 IS NULL
        OR front_strip_side_2 IS NULL
        OR front_strip_side_1 <> front_strip_side_2
    ),
    CONSTRAINT chk_back_strip_sides_distinct CHECK (
        back_strip_side_1 IS NULL
        OR back_strip_side_2 IS NULL
        OR back_strip_side_1 <> back_strip_side_2
    ),
    CONSTRAINT uq_license_plate_configuration UNIQUE NULLS NOT DISTINCT (
        car_type,
        front_is_required,
        front_color,
        front_strip_color_1,
        front_strip_side_1,
        front_strip_color_2,
        front_strip_side_2,
        front_letter_color,
        front_shape,
        back_color,
        back_strip_color_1,
        back_strip_side_1,
        back_strip_color_2,
        back_strip_side_2,
        back_letter_color,
        back_shape
    )
);

-- 2. WEAK ENTITIES (Dependent on Strong Entities)
CREATE TABLE state (
    country_code CHAR(2),
    state_id     SERIAL,
    name         VARCHAR(80) NOT NULL,

    PRIMARY KEY (country_code, state_id),
    FOREIGN KEY (country_code) REFERENCES country(code) ON DELETE CASCADE
);

CREATE TABLE city (
    country_code CHAR(2),
    city_id      BIGSERIAL,
    state_id     INTEGER,
    county       VARCHAR(120),
    name         VARCHAR(120) NOT NULL,
    area_code    VARCHAR(20),
    geometry     TEXT         NOT NULL,

    PRIMARY KEY (country_code, city_id),
    CONSTRAINT uq_city_identity UNIQUE NULLS NOT DISTINCT (country_code, state_id, county, name),
    FOREIGN KEY (country_code) REFERENCES country(code) ON DELETE CASCADE,
    FOREIGN KEY (country_code, state_id) REFERENCES state(country_code, state_id)
);

CREATE TABLE ecoregion (
    biome_id     INTEGER,
    ecoregion_id SERIAL,
    name         VARCHAR(100) UNIQUE NOT NULL,

    PRIMARY KEY (biome_id, ecoregion_id),
    FOREIGN KEY (biome_id) REFERENCES biome(biome_id) ON DELETE CASCADE
);

CREATE TABLE round (
    challenge_token    CHAR(16),
    game_id            CHAR(16),
    round_number       SMALLINT,

    guess_latitude     NUMERIC(10,7),
    guess_longitude    NUMERIC(10,7),
    guess_country_code CHAR(2),
    guess_state_id     INTEGER,
    guess_city         VARCHAR(200),
    guess_biome_id     INTEGER,
    guess_ecoregion_id INTEGER,

    real_latitude      NUMERIC(10,7) NOT NULL,
    real_longitude     NUMERIC(10,7) NOT NULL,
    real_country_code  CHAR(2)       NOT NULL,
    real_state_id      INTEGER,
    real_city          VARCHAR(200),
    real_biome_id      INTEGER       NOT NULL,
    real_ecoregion_id  INTEGER       NOT NULL,

    score              SMALLINT      NOT NULL,
    distance_km        NUMERIC(7,1)  NOT NULL,
    steps              SMALLINT      NOT NULL,
    time_sec           SMALLINT      NOT NULL,

    PRIMARY KEY (challenge_token, game_id, round_number),
    FOREIGN KEY (challenge_token, game_id) REFERENCES game(challenge_token, game_id) ON DELETE CASCADE,
    FOREIGN KEY (guess_country_code) REFERENCES country(code) ON DELETE RESTRICT,
    FOREIGN KEY (real_country_code) REFERENCES country(code) ON DELETE RESTRICT,
    FOREIGN KEY (guess_country_code, guess_state_id) REFERENCES state(country_code, state_id) ON DELETE RESTRICT,
    FOREIGN KEY (real_country_code, real_state_id) REFERENCES state(country_code, state_id) ON DELETE RESTRICT,
    FOREIGN KEY (guess_biome_id, guess_ecoregion_id) REFERENCES ecoregion(biome_id, ecoregion_id) ON DELETE RESTRICT,
    FOREIGN KEY (real_biome_id, real_ecoregion_id) REFERENCES ecoregion(biome_id, ecoregion_id) ON DELETE RESTRICT,

    CONSTRAINT chk_round_round_number_unsigned CHECK (round_number >= 0),
    CONSTRAINT chk_round_score_range CHECK (score BETWEEN 0 AND 5000),
    CONSTRAINT chk_round_steps_unsigned CHECK (steps >= 0),
    CONSTRAINT chk_round_time_sec_unsigned CHECK (time_sec >= 0),
    CONSTRAINT chk_round_distance_unsigned CHECK (distance_km >= 0),
    CONSTRAINT chk_round_guess_latitude_range CHECK (guess_latitude IS NULL OR guess_latitude BETWEEN -90 AND 90),
    CONSTRAINT chk_round_guess_longitude_range CHECK (guess_longitude IS NULL OR guess_longitude BETWEEN -180 AND 180),
    CONSTRAINT chk_round_real_latitude_range CHECK (real_latitude BETWEEN -90 AND 90),
    CONSTRAINT chk_round_real_longitude_range CHECK (real_longitude BETWEEN -180 AND 180),
    CONSTRAINT chk_round_guess_all_or_none CHECK (
        (
            guess_latitude IS NULL
            AND guess_longitude IS NULL
            AND guess_country_code IS NULL
            AND guess_biome_id IS NULL
            AND guess_ecoregion_id IS NULL
            AND guess_state_id IS NULL
            AND guess_city IS NULL
        )
        OR
        (
            guess_latitude IS NOT NULL
            AND guess_longitude IS NOT NULL
            AND guess_country_code IS NOT NULL
            AND guess_biome_id IS NOT NULL
            AND guess_ecoregion_id IS NOT NULL
        )
    )
);

-- 3. MANY-TO-MANY (NN) RELATIONSHIP TABLES
CREATE TABLE state_designs_license_plate (
    country_code     CHAR(2),
    state_id         INTEGER,
    license_plate_id INTEGER,

    PRIMARY KEY (country_code, state_id, license_plate_id),
    FOREIGN KEY (country_code, state_id) REFERENCES state(country_code, state_id) ON DELETE CASCADE,
    FOREIGN KEY (license_plate_id) REFERENCES license_plate(license_plate_id) ON DELETE CASCADE
);

CREATE TABLE country_issues_license_plate (
    country_code     CHAR(2),
    license_plate_id INTEGER,

    PRIMARY KEY (country_code, license_plate_id),
    FOREIGN KEY (country_code) REFERENCES country(code) ON DELETE CASCADE,
    FOREIGN KEY (license_plate_id) REFERENCES license_plate(license_plate_id) ON DELETE CASCADE
);

CREATE TABLE country_paints_road_line (
    country_code CHAR(2),
    road_line_id INTEGER,

    PRIMARY KEY (country_code, road_line_id),
    FOREIGN KEY (country_code) REFERENCES country(code) ON DELETE CASCADE,
    FOREIGN KEY (road_line_id) REFERENCES road_line(road_line_id) ON DELETE CASCADE
);

CREATE INDEX idx_city_country_code ON city (country_code);
CREATE INDEX idx_city_country_area_code ON city (country_code, area_code);
