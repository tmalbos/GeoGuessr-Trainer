import argparse
import re

import geopandas as gpd

VALID_REF = re.compile(r"^DJ\d{1,3}[A-Z]$")


def filter_roads(input_geojson: str, output_geojson: str) -> None:
    print(f"Reading: {input_geojson}")

    gdf = gpd.read_file(input_geojson)

    original_count = len(gdf)

    # Keep only refs containing 1–3 digits and nothing else.
    valid_ref = gdf["ref"].apply(
        lambda ref: bool(VALID_REF.fullmatch(str(ref).strip())) if ref is not None else False
    )

    # Keep only roads whose name is null.
    unnamed = gdf["name"].isna()

    # Both filters must pass.
    result = gdf[valid_ref & unnamed].copy()

    # name is no longer needed in the final output.
    result = result.drop(columns=["name"])

    print(f"Original roads:  {original_count:,}")
    print(f"Removed roads:   {original_count - len(result):,}")
    print(f"Remaining roads: {len(result):,}")

    result.to_file(
        output_geojson,
        driver="GeoJSON",
    )

    print(f"Wrote: {output_geojson}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Filter roads to unnamed roads with 1–3 digit refs."
    )

    parser.add_argument(
        "input_geojson",
        help="Input roads GeoJSON",
    )

    parser.add_argument(
        "output_geojson",
        help="Output filtered GeoJSON",
    )

    args = parser.parse_args()

    filter_roads(
        args.input_geojson,
        args.output_geojson,
    )
