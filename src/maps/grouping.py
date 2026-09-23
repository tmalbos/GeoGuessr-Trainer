import re
import sys

from shapely.ops import unary_union
from topo_io import hierarchical_name_id
from unidecode import unidecode


def _norm(value):
    """Accent/case/punctuation-insensitive form: 'Abu_Dhabi' == 'Abu Dhabi'."""
    return re.sub(r"[^a-z0-9]", "", unidecode(str(value)).lower())


def build_land_group_geometries(rows, name_chains, fine_geoms, coarse_level):
    """Rebuild the Land layer with the CSV groups applied.

    CSV Level1 is ADM1, so when the loaded levels start at ADM0 the first
    slot of every name chain (the country itself) is dropped before
    matching. Unresolved (None) names are also dropped, so the chain
    matches the Land layer's SVG id (which skips empty names). Names are
    compared in normalized form (case, accents, spaces and underscores
    ignored). A row with k levels matches every Land polygon whose first
    k names equal it, so shallower rows cover all the polygons beneath them.

    Every Land polygon is keyed by its group if the CSV lists it, or by its
    own normal Land id if not. Polygons are then merged per key, so listed
    polygons are unioned and unlisted ones pass through unchanged.
    Returns {output id: geometry} in lon/lat.
    """
    offset = 1 if coarse_level == 0 else 0
    chains = [[n for n in chain[offset:] if n] for chain in name_chains]
    norm_chains = [tuple(_norm(n) for n in chain) for chain in chains]

    index_by_depth = {}

    def index_for(k):
        if k not in index_by_depth:
            d = {}
            for idx, chain in enumerate(norm_chains):
                if len(chain) < k:
                    continue
                d.setdefault(chain[:k], []).append(idx)
            index_by_depth[k] = d
        return index_by_depth[k]

    owner = {}
    for row in rows:
        key = tuple(_norm(n) for n in row["levels"])
        idxs = index_for(len(key)).get(key)
        if not idxs:
            examples = sorted({".".join(c[: len(key)]) for c in chains if len(c) >= len(key)})[:10]
            sys.exit(
                f"Error: la fila {row['GroupName']!r} ({'.'.join(row['levels'])}) no coincide con "
                f"ninguna entidad de Land. Ejemplos disponibles: {examples}"
            )
        for i in idxs:
            previous = owner.setdefault(i, row["GroupName"])
            if previous != row["GroupName"]:
                sys.exit(
                    f"Error: {'.'.join(chains[i])} está asignado a dos grupos: "
                    f"{previous!r} y {row['GroupName']!r}."
                )

    members = {}
    for i in range(len(chains)):
        output_id = owner.get(i, hierarchical_name_id(name_chains[i], str(i)))
        members.setdefault(output_id, []).append(i)

    return {out: unary_union([fine_geoms[i] for i in idxs]) for out, idxs in members.items()}
