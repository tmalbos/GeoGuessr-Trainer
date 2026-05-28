# GeoGuessr Trainer — Glossary

## Area Code

The local geographic routing prefix a subscriber dials after the country calling code, before the local number. Stored as bare digits only — no hyphens, spaces, or country prefix. Variable length across countries (e.g. `11` for Buenos Aires, `351` for Córdoba, `2284` for Olavarría). One area code maps to one geographic zone; zones are non-overlapping within a country for all target countries except the US (NANP overlays), where the primary/older code is used as a simplification.

**Avoid:** "phone number", "dialling code", "calling code" (those refer to the country-level prefix, e.g. `+54`).

## City

A named place at Nominatim zoom=10 resolution — the level returned when `geo_enrich` reverse-geocodes a coordinate. Corresponds to what Nominatim returns under the `city`, `town`, `village`, or `municipality` address key. The underlying OSM admin_level varies by country; this inconsistency is a known limitation, deferred. A city is a first-class DB entity with a polygon geometry sourced from Nominatim and a nullable `area_code` field.

**Avoid:** "admin_level 10" (that is an OSM concept distinct from Nominatim zoom=10).

## Zone Type

A future discriminator (`geographic`, `mobile`, `landline`, `special`) reserved for flagging area codes that are not geographically bounded (e.g. national mobile prefixes). Not modelled in the current schema. Future use: surface a warning to the player that a given number does not narrow down a location.

## Area Code Zone (deprecated concept)

The original framing of this feature: a polygon table storing one row per area code. Superseded by the **City** model — area code zones are derived implicitly by grouping city polygons sharing the same area code.
