import time
from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union

# ============================================================
# SETTINGS
# ============================================================

# Output of: ogr2ogr (filter/reproject/makevalid) -> mapshaper
# (topojson conversion + simplify). TopoJSON stores each shared
# boundary as a single arc referenced by every polygon that
# touches it, so simplifying that arc once keeps every dependent
# polygon's edge bit-identical - unlike simplifying a flat
# GeoJSON, where two polygons that used to share an edge can each
# get simplified independently and drift apart by a tiny amount.
# That drift was the root cause of the GEOS "unable to assign
# free hole to a shell" / "CoverageUnion cannot process
# overlapping inputs" errors seen with the geojson pipeline.
#
# GDAL's GeoJSON driver reads TopoJSON directly (decodes arcs
# back into polygon coordinates on load), so no extra conversion
# step is needed here - point read_file straight at the file.
INPUT_FILE = "landforms.topojson"

# The CRS used upstream in the ogr2ogr -t_srs step. Like GeoJSON,
# TopoJSON has no reliable CRS metadata convention, so we don't
# trust whatever (if anything) is embedded in the file - set it
# explicitly instead.
WORK_CRS = "EPSG:6933"

OUTPUT_DIR = Path("landform_scenarios")
OUTPUT_DIR.mkdir(exist_ok=True)

# Number of segments per quarter-circle when buffering. Default
# in shapely is 8; at multi-km buffer distances for a
# generalization product, a lower value is visually
# indistinguishable and meaningfully cuts vertex count in every
# downstream difference/intersection.
BUFFER_QUAD_SEGS = 4

SCENARIOS = {
    "Conservative": {
        "L3_to_L1": 20,
        "L2_to_L1": 5,
    },
    "Moderate": {
        "L3_to_L1": 40,
        "L2_to_L1": 10,
    },
    "Aggressive": {
        "L3_to_L1": 60,
        "L2_to_L1": 20,
    },
}

TOPOG_LABELS = {
    1: "Plains",
    2: "Hills",
    3: "Mountains",
    4: "High Tablelands",
    7: "Depressions or Basins",
}


# ============================================================
# COVERAGE-UNION-AWARE DISSOLVE
#
# Coverage union (GeoPandas >= 1.0 / Shapely >= 2.0 / GEOS >=
# 3.9) is much faster than the general unary union, but requires
# input polygons to share edges exactly with no overlaps. With
# the topojson-based pipeline, dissolve groups built from shared
# arcs should satisfy this - but we still fall back safely if a
# given dataset doesn't, rather than crashing the run.
# ============================================================

_coverage_union_unavailable = False


def dissolve_fast(gdf, by):
    global _coverage_union_unavailable

    if _coverage_union_unavailable:
        return gdf.dissolve(by=by, as_index=False)

    try:
        return gdf.dissolve(by=by, as_index=False, method="coverage")
    except TypeError:
        _coverage_union_unavailable = True
        return gdf.dissolve(by=by, as_index=False)
    except Exception as exc:
        print(
            f"  (coverage union unavailable for this data - "
            f"{type(exc).__name__}; falling back to standard "
            f"dissolve for all remaining dissolves)"
        )
        _coverage_union_unavailable = True
        return gdf.dissolve(by=by, as_index=False)


# ============================================================
# START
# ============================================================

total_start = time.time()

print("=" * 70)
print("READING PRE-FILTERED / PRE-SIMPLIFIED SOURCE (TopoJSON)")
print("=" * 70)

gdf = gpd.read_file(INPUT_FILE)

# Force the CRS rather than trusting the file.
gdf = gdf.set_crs(WORK_CRS, allow_override=True)

print(f"Features read: {len(gdf):,}")
print(f"CRS forced to: {gdf.crs}")

# Cheap safety net: repair anything that's invalid regardless of
# where it came from. This is not the precision-grid workaround -
# just a normal validity check.
invalid_mask = ~gdf.geometry.is_valid
n_invalid = int(invalid_mask.sum())
if n_invalid:
    print(f"Repairing {n_invalid:,} invalid geometries...")
    gdf.loc[invalid_mask, "geometry"] = gdf.loc[invalid_mask, "geometry"].make_valid()


# ============================================================
# INITIAL DISSOLVE
# ============================================================

print()
print("=" * 70)
print("INITIAL DISSOLVE")
print("=" * 70)

start = time.time()

gdf = dissolve_fast(
    gdf,
    by=["Division", "Province", "Topog", "TopoHierarchy"],
)

print(f"Features after initial dissolve: {len(gdf):,}")
print(f"Initial dissolve time: {time.time() - start:.1f} sec")


# ============================================================
# ONE UNION PER HIERARCHY LEVEL
# ============================================================

print()
print("Building hierarchy geometries...")

hierarchy_geometries = {}

for level in [1, 2, 3]:
    subset = gdf[gdf["TopoHierarchy"] == level]

    if len(subset) == 0:
        hierarchy_geometries[level] = None
        continue

    start = time.time()
    hierarchy_geometries[level] = unary_union(subset.geometry)

    print(f"Level {level}: {len(subset):,} dissolved features, union {time.time() - start:.1f} sec")

# All-geometry union, used for Level 1's remainder. Computed once
# here rather than rebuilt inside every scenario.
all_geometry = unary_union(gdf.geometry)


# ============================================================
# PER-TOPOG DISSOLVED GEOMETRIES
#
# Independent of scenario distances - computed once, reused by
# every scenario, instead of being rebuilt per scenario.
# ============================================================

print("Pre-computing per-Topog dissolved geometries...")

topo_geometries = {}  # (level, topog) -> geometry

for level in [1, 2, 3]:
    subset = gdf[gdf["TopoHierarchy"] == level]
    if len(subset) == 0:
        continue
    for topog in subset["Topog"].unique():
        topo_subset = subset[subset["Topog"] == topog]
        topo_geometries[level, int(topog)] = unary_union(topo_subset.geometry)


# ============================================================
# SCENARIO FUNCTION
# ============================================================


def process_scenario(name, distances) -> None:

    print()
    print("=" * 70)
    print(f"SCENARIO: {name}")
    print("=" * 70)

    start_total = time.time()

    level3 = hierarchy_geometries[3]
    level2 = hierarchy_geometries[2]

    # --------------------------------------------------------
    # INFLUENCE ZONES
    #
    # Priority is Level 3 > Level 2 > Level 1, so only the L3 ->
    # L1 buffer is needed for Level 3's reach (Level 2's own
    # claim is already handled by its own L2 -> L1 buffer, and
    # Level 3 wins any overlap regardless of distance).
    # --------------------------------------------------------

    print("Creating Level 3 influence zone...")
    level3_to_l1 = (
        level3.buffer(distances["L3_to_L1"] * 1000, quad_segs=BUFFER_QUAD_SEGS)
        if level3 is not None
        else None
    )

    print("Creating Level 2 influence zone...")
    level2_to_l1 = (
        level2.buffer(distances["L2_to_L1"] * 1000, quad_segs=BUFFER_QUAD_SEGS)
        if level2 is not None
        else None
    )

    print("Resolving hierarchy priority...")

    generalized3 = level3_to_l1

    generalized2 = level2_to_l1
    if generalized2 is not None and generalized3 is not None:
        generalized2 = generalized2.difference(generalized3)

    generalized1 = all_geometry
    if generalized3 is not None:
        generalized1 = generalized1.difference(generalized3)
    if generalized2 is not None:
        generalized1 = generalized1.difference(generalized2)

    # --------------------------------------------------------
    # RESTORE ORIGINAL TOPOG CATEGORIES
    # --------------------------------------------------------

    print("Restoring original Topog classifications...")

    output_parts = []

    for level, generalized in [(3, generalized3), (2, generalized2), (1, generalized1)]:
        if generalized is None:
            continue

        for (lvl, topog), topo_geometry in topo_geometries.items():
            if lvl != level:
                continue

            new_geometry = topo_geometry.intersection(generalized)

            if new_geometry.is_empty:
                continue

            output_parts.append(
                {
                    "Topog": topog,
                    "TopoHierarchy": int(level),
                    "geometry": new_geometry,
                }
            )

    result = gpd.GeoDataFrame(output_parts, geometry="geometry", crs=WORK_CRS)
    result = result[~result.geometry.is_empty].copy()

    print("Final spatial dissolve...")
    result = dissolve_fast(result, by=["Topog", "TopoHierarchy"])

    result["TopogName"] = result["Topog"].map(TOPOG_LABELS)
    result = result.to_crs("EPSG:4326")

    output_file = OUTPUT_DIR / f"NLW_Terrain_{name}.gpkg"
    result.to_file(output_file, layer="Landforms", driver="GPKG")

    elapsed = time.time() - start_total

    print()
    print(f"Finished: {name}")
    print(f"Output features: {len(result):,}")
    print(f"Output: {output_file}")
    print(f"Time: {elapsed:.1f} sec")


# ============================================================
# RUN SCENARIOS
# ============================================================

for name, distances in SCENARIOS.items():
    process_scenario(name, distances)


# ============================================================
# DONE
# ============================================================

print()
print("=" * 70)
print("ALL SCENARIOS COMPLETE")
print("=" * 70)
print(f"Total runtime: {time.time() - total_start:.1f} sec")
