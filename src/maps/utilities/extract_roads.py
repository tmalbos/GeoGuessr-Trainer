import argparse
import math

import geopandas as gpd
import pyogrio

FIELDS = ["name", "type", "ref", "route"]


def is_null(value):
    """Return True for None, NaN, or other missing values."""
    if value is None:
        return True

    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def parse_other_tags(other_tags):
    """Parse GDAL's OSM other_tags format:

        "ref"=>"ABC","route"=>"road"

    into:

        {"ref": "ABC", "route": "road"}
    """
    if is_null(other_tags):
        return {}

    result = {}

    for item in str(other_tags).split(","):
        item = item.strip()

        if "=>" not in item:
            continue

        key, value = item.split("=>", 1)

        key = key.strip().strip('"')
        value = value.strip().strip('"')

        result[key] = value

    return result


def extract_multilinestrings(input_pbf: str, output_geojson: str) -> None:
    layer = "multilinestrings"

    print(f"Reading: {input_pbf}")
    print(f"Layer:  {layer}")

    gdf = pyogrio.read_dataframe(
        input_pbf,
        layer=layer,
    )

    print(f"Found {len(gdf):,} MultiLineString features")

    source_columns = set(gdf.columns)

    kept_features = []

    for _, row in gdf.iterrows():
        other_tags = parse_other_tags(row.get("other_tags"))

        properties = {}

        # Resolve the four attributes.
        #
        # Root-level attributes take precedence over other_tags.
        for field in FIELDS:
            if field in source_columns:
                value = row[field]

                if not is_null(value):
                    properties[field] = value

                elif field not in other_tags:
                    # The attribute exists at root level and is explicitly
                    # null. This matters for name/type.
                    properties[field] = None

            elif field in other_tags:
                properties[field] = other_tags[field]

        # ------------------------------------------------------------
        # Required fields:
        #
        # name  -> must exist, null allowed
        # type  -> must exist, null allowed
        # ref   -> must exist and cannot be null
        # route -> must exist, cannot be null, must equal "road"
        # ------------------------------------------------------------

        if "name" not in properties:
            continue

        if "type" not in properties:
            continue

        if "ref" not in properties or is_null(properties["ref"]):
            continue

        if "route" not in properties:
            continue

        if is_null(properties["route"]):
            continue

        # Only keep route="road".
        if properties["route"] != "road":
            continue

        kept_features.append(
            {
                "geometry": row.geometry,
                **properties,
            }
        )

    print(f"Keeping {len(kept_features):,} features")
    print(f"Discarding {len(gdf) - len(kept_features):,} features")

    if not kept_features:
        msg = "No features matched the filtering criteria."
        raise RuntimeError(msg)

    result = gpd.GeoDataFrame(
        kept_features,
        geometry="geometry",
        crs=gdf.crs,
    )

    # Final GeoJSON contains exactly these attributes.
    result = result[["geometry", "name", "type", "ref", "route"]]

    pyogrio.write_dataframe(
        result,
        output_geojson,
        driver="GeoJSON",
    )

    print(f"Wrote: {output_geojson}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract road MultiLineStrings from an OSM PBF.")

    parser.add_argument(
        "input_pbf",
        help="Input .osm.pbf file",
    )

    parser.add_argument(
        "output_geojson",
        help="Output .geojson file",
    )

    args = parser.parse_args()

    extract_multilinestrings(
        args.input_pbf,
        args.output_geojson,
    )
