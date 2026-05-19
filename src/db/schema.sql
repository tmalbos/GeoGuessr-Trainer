-- =============================================================
-- GeoGuessr Study DB — Schema
-- =============================================================

-- ENUMS: license plates
CREATE TYPE car_type AS ENUM (
    'normal', 'commercial', 'taxi', 'motorcycle',
    'military', 'government', 'electric'
);

CREATE TYPE plate_color AS ENUM (
    'white', 'yellow', 'pastel_yellow', 'blue', 'green',
    'orange', 'brown', 'red', 'black'
);

CREATE TYPE plate_shape AS ENUM ('wide', 'short', 'tall', 'standard');
CREATE TYPE strip_side  AS ENUM ('left', 'right', 'top', 'bottom');

-- ENUMS: road lines
CREATE TYPE line_color AS ENUM (
    'white', 'faded_white', 'yellow', 'orange_tinted_yellow',
    'green', 'red', 'orange'
);

CREATE TYPE line_pattern AS ENUM (
    'solid', 'dashed', 'short_dashed', 'squares'
);

CREATE TYPE road_rule AS ENUM (
    'whole_country', 'region_dependant', 'urban', 'rural'
);


-- =============================================================
-- GEO SIGNALS
-- =============================================================

CREATE TABLE countries (
    code  CHAR(2)      PRIMARY KEY,  -- ISO 3166-1 alpha-2
    name  VARCHAR(100) NOT NULL
);

CREATE TABLE states (
    id          SERIAL       PRIMARY KEY,
    country_id  CHAR(2)      NOT NULL REFERENCES countries(code),
    name        VARCHAR(100) NOT NULL
);

CREATE TABLE biomes (
    id    SERIAL       PRIMARY KEY,
    name  VARCHAR(150) NOT NULL,
    realm VARCHAR(100) NOT NULL
);

CREATE TABLE ecoregions (
    id        SERIAL       PRIMARY KEY,
    name      VARCHAR(150) NOT NULL,
    biome_id  INTEGER      NOT NULL REFERENCES biomes(id)
);

-- Tabla relacional state <-> ecoregion
CREATE TABLE state_ecoregions (
    state_id      INTEGER      NOT NULL REFERENCES states(id),
    ecoregion_id  INTEGER      NOT NULL REFERENCES ecoregions(id),
    coverage_pct  NUMERIC(5,2),
    PRIMARY KEY (state_id, ecoregion_id)
);

-- Cara de patente: reutilizable para front y back
CREATE TABLE license_plate_faces (
    id           SERIAL      PRIMARY KEY,
    is_required  BOOLEAN     NOT NULL DEFAULT TRUE,
    color        plate_color NOT NULL,
    letter_color plate_color NOT NULL,
    shape        plate_shape NOT NULL
);

CREATE TABLE license_plate_face_strips (
    id       SERIAL      PRIMARY KEY,
    face_id  INTEGER     NOT NULL REFERENCES license_plate_faces(id),
    color    plate_color NOT NULL,
    side     strip_side  NOT NULL
);

CREATE TABLE license_plates (
    id          SERIAL   PRIMARY KEY,
    country_id  CHAR(2)  NOT NULL REFERENCES countries(code),
    car_type    car_type NOT NULL,
    front_id    INTEGER  NOT NULL REFERENCES license_plate_faces(id),
    back_id     INTEGER  REFERENCES license_plate_faces(id)  -- NULL = igual que front
);

CREATE TABLE road_lines (
    id          SERIAL    PRIMARY KEY,
    country_id  CHAR(2)   NOT NULL REFERENCES countries(code),
    rule        road_rule NOT NULL,

    inner_color         line_color   NOT NULL,
    inner_count         SMALLINT     NOT NULL,
    inner_pattern       line_pattern NOT NULL,
    inner_extra_color   line_color,       -- NULL si no tiene extra
    inner_extra_pattern line_pattern,     -- NULL si no tiene extra

    outer_color   line_color   NOT NULL,
    outer_count   SMALLINT     NOT NULL,
    outer_pattern line_pattern NOT NULL
);


-- =============================================================
-- GAMES
-- =============================================================

CREATE TABLE games (
    game_id         VARCHAR(20)  PRIMARY KEY,
    map_name        VARCHAR(100),
    played_at       TIMESTAMPTZ,
    avg_distance    NUMERIC(8,2),
    total_score     INTEGER,
    challenge_token VARCHAR(20),
    is_daily        BOOLEAN      DEFAULT FALSE
);


-- =============================================================
-- ROUNDS
-- =============================================================

-- Punto geográfico reutilizable para guess y real
CREATE TABLE geo_points (
    id            SERIAL   PRIMARY KEY,
    lat           NUMERIC(10,7),
    lng           NUMERIC(10,7),
    city          VARCHAR(100),
    ecoregion_id  INTEGER  NOT NULL REFERENCES ecoregions(id)
);

CREATE TABLE rounds (
    id           SERIAL      PRIMARY KEY,
    game_id      VARCHAR(20) NOT NULL REFERENCES games(game_id),
    round_number SMALLINT    NOT NULL,
    score        INTEGER,
    distance_km  NUMERIC(8,2),
    steps        SMALLINT,
    time_sec     SMALLINT,
    guess_geo_id INTEGER     NOT NULL REFERENCES geo_points(id),
    real_geo_id  INTEGER     NOT NULL REFERENCES geo_points(id)
);