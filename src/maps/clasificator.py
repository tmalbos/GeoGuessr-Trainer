"""Clasificador topografico rapido (estilo GeoGuessr).
Clasifica un area en: Llanura, Ondulaciones, Sierras, Montanas.

Logica:
- "Local" (radio chico, ~1.5 km): textura del terreno donde estas parado.
  Usa pendiente media y relieve relativo (max-min) para separar Llanura
  de Ondulaciones.
- "Corona" (radio grande, ~15 km): compara la elevacion mas alta visible
  en ese radio contra tu propia elevacion. Si hay algo mucho mas alto
  cerca (una sierra o montana), el punto se re-clasifica como tal aunque
  el terreno local sea chato (tu caso de "veo una montana a 5 km").

Uso:
    python clasificador.py area.tif --out clases.tif
"""

import argparse

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy.ndimage import maximum_filter, minimum_filter, uniform_filter


def reproject_to_utm(src_path, dst_path):
    """SRTM viene en grados (lat/lon). Lo reproyectamos a UTM (metros)
    para que los radios en km sean radios reales, no grados.
    """
    with rasterio.open(src_path) as src:
        lon = (src.bounds.left + src.bounds.right) / 2
        lat = (src.bounds.top + src.bounds.bottom) / 2
        zone = int((lon + 180) / 6) + 1
        epsg = 32600 + zone if lat >= 0 else 32700 + zone
        dst_crs = f"EPSG:{epsg}"

        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update({"crs": dst_crs, "transform": transform, "width": width, "height": height})

        with rasterio.open(dst_path, "w", **kwargs) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
    return dst_path


def compute_features(dem, pixel_size_m, r_local_m=1500, r_corona_m=15000):
    r_local_px = max(1, round(r_local_m / pixel_size_m))
    r_corona_px = max(1, round(r_corona_m / pixel_size_m))

    # Ventanas cuadradas (size) en vez de mascara circular (footprint):
    # el algoritmo de scipy para 'size' es muchisimo mas liviano en memoria
    # y tiempo que uno con footprint arbitrario, sobre todo con radios grandes.
    # La diferencia practica entre circulo y cuadrado es minima para esto.
    win_local = 2 * r_local_px + 1
    win_corona = 2 * r_corona_px + 1

    gy, gx = np.gradient(dem, pixel_size_m)
    slope = np.degrees(np.arctan(np.sqrt(gx**2 + gy**2)))
    slope_local = uniform_filter(slope, size=win_local)

    max_local = maximum_filter(dem, size=win_local)
    min_local = minimum_filter(dem, size=win_local)
    relief_local = max_local - min_local

    # prominencia: lo mas alto visible en el radio grande, contra tu propia altura
    max_corona = maximum_filter(dem, size=win_corona)
    relief_corona = max_corona - dem

    return slope_local, relief_local, relief_corona


def classify(
    slope_local,
    relief_local,
    relief_corona,
    slope_llanura=2.0,
    relief_llanura=30.0,
    corona_sierra=150.0,
    corona_montana=600.0,
):
    # 0=Llanura 1=Ondulaciones 2=Sierras 3=Montanas
    clase = np.zeros(slope_local.shape, dtype=np.uint8)

    ondulado = (slope_local >= slope_llanura) | (relief_local >= relief_llanura)
    clase[ondulado] = 1

    # Ondulacion + relieve local muy debil -> se re-funde con Llanura (tu regla)
    reclasificar = (clase == 1) & (relief_local < relief_llanura * 0.5)
    clase[reclasificar] = 0

    # Override por relieve visible cerca (sierra/montana aunque el piso sea chato)
    clase[(relief_corona >= corona_sierra) & (relief_corona < corona_montana)] = 2
    clase[relief_corona >= corona_montana] = 3

    return clase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dem_path")
    parser.add_argument("--out", default="clases.tif")
    parser.add_argument(
        "--r_local", type=float, default=1500, help="radio local en metros (default 1500 = 1.5km)"
    )
    parser.add_argument(
        "--r_corona",
        type=float,
        default=15000,
        help="radio de deteccion de relieve cercano en metros",
    )
    parser.add_argument(
        "--slope_llanura",
        type=float,
        default=2.0,
        help="grados: por encima de esto ya no es Llanura",
    )
    parser.add_argument(
        "--relief_llanura",
        type=float,
        default=30.0,
        help="metros: relieve local por encima de esto ya no es Llanura",
    )
    parser.add_argument(
        "--corona_sierra",
        type=float,
        default=150.0,
        help="metros: prominencia minima para considerar Sierra cercana",
    )
    parser.add_argument(
        "--corona_montana",
        type=float,
        default=600.0,
        help="metros: prominencia minima para considerar Montana cercana",
    )
    args = parser.parse_args()

    utm_path = args.dem_path.replace(".tif", "_utm.tif")
    reproject_to_utm(args.dem_path, utm_path)

    with rasterio.open(utm_path) as src:
        dem = src.read(1).astype(np.float32)
        pixel_size_m = src.transform[0]
        profile = src.profile

    slope_local, relief_local, relief_corona = compute_features(
        dem, pixel_size_m, args.r_local, args.r_corona
    )

    clase = classify(
        slope_local,
        relief_local,
        relief_corona,
        args.slope_llanura,
        args.relief_llanura,
        args.corona_sierra,
        args.corona_montana,
    )

    profile.update(dtype=rasterio.uint8, count=1, nodata=255)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(clase, 1)

    print(f"Listo: {args.out}")
    print("0=Llanura 1=Ondulaciones 2=Sierras 3=Montanas")


if __name__ == "__main__":
    main()
