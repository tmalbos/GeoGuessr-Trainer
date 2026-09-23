"""Aplica los umbrales de Relieve local y Textura sobre features.tif y genera
un raster de una sola banda con valores discretos 0-4 (una categoria cada uno).

Categorias:
    0 = Plano
    1 = Ondulante
    2 = Hilly
    3 = Montanas pequenas
    4 = Montanas grandes

Como se combinan las 2 variables: cada pixel obtiene un indice de categoria
segun Relieve local, y otro segun Textura (usando los umbrales de cada tabla).
La categoria final es el MAYOR de los dos indices (el mas conservador: si
cualquiera de las 2 variables dice "esto ya es mas accidentado", gana esa).
Si preferis otro criterio, cambia --combine a 'relieve', 'textura' o 'promedio'.

Uso:
    python categorize.py features.tif --out categorias.tif
"""

import argparse

import numpy as np
import rasterio

# Umbrales de referencia (limite inferior de cada categoria)
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
    parser.add_argument(
        "--combine", choices=["max", "relieve", "textura", "promedio"], default="relieve"
    )
    args = parser.parse_args()

    with rasterio.open(args.features_path) as src:
        relieve = src.read(2)
        src.read(3)
        profile = src.profile

    cat_relieve = a_categoria(relieve, UMBRALES_RELIEVE)

    if args.combine == "max":
        categoria = np.maximum(cat_relieve)  # , cat_textura)
    elif args.combine == "relieve":
        categoria = cat_relieve
    # elif args.combine == "textura":
    #     categoria = cat_textura
    # else:  # promedio
    #     categoria = np.round((cat_relieve.astype(np.float32) + cat_textura) / 2).astype(np.uint8)

    profile.update(count=1, dtype=rasterio.uint8, nodata=255)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(categoria, 1)
        dst.update_tags(1, CATEGORIAS=", ".join(f"{i}={n}" for i, n in enumerate(NOMBRES)))

    print(f"Listo: {args.out}")
    for i, n in enumerate(NOMBRES):
        print(f"{i} = {n}")


if __name__ == "__main__":
    main()
