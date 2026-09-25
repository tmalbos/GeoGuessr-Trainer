"""Aplica los umbrales de Relieve local sobre features.tif y genera
un raster de una sola banda con valores discretos 0-4 (una categoria cada uno).

Categorias:
    0 = Plano
    1 = Ondulante
    2 = Hilly
    3 = Montanas pequenas
    4 = Montanas grandes

Solo usa Relieve local (banda 2 de features.tif). La textura (banda 3)
ya no se usa en la clasificacion -- se elimino la logica de corona/textura,
asi que se dejo de leer para no confundir.

Uso:
    python categorize.py features.tif --out categorias.tif
"""

import argparse

import numpy as np
import rasterio

# Umbrales de referencia (limite inferior de cada categoria), recalibrados con FABDEM
UMBRALES_RELIEVE = [0, 22, 50, 140, 800]  # metros

NOMBRES = ["Plano", "Ondulante", "Hilly", "Montanas pequenas", "Montanas grandes"]


def a_categoria(valor, umbrales):
    # devuelve el indice de categoria (0-4) segun en que rango cae el valor
    idx = np.zeros(valor.shape, dtype=np.uint8)
    for i, u in enumerate(umbrales):
        idx[valor >= u] = i
    return idx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("features_path")
    parser.add_argument("--out", default="categorias.tif")
    args = parser.parse_args()

    with rasterio.open(args.features_path) as src:
        relieve = src.read(2)
        profile = src.profile

    categoria = a_categoria(relieve, UMBRALES_RELIEVE)

    profile.update(count=1, dtype=rasterio.uint8, nodata=255)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(categoria, 1)
        dst.update_tags(1, CATEGORIAS=", ".join(f"{i}={n}" for i, n in enumerate(NOMBRES)))

    print(f"Listo: {args.out}")
    for i, n in enumerate(NOMBRES):
        print(f"{i} = {n}")


if __name__ == "__main__":
    main()
