"""Genera un raster multibanda para inspeccion punto por punto en QGIS.

Bandas de salida (features.tif):
  1) Elevacion (m)
  2) Relieve local: max - min dentro del radio definido (m)
  3) Textura de superficie: densidad de "puntos de inflexion" (subida->bajada
     o bajada->subida) dentro del radio, expresada como % de pixeles del
     entorno que son un cambio de curvatura. Mas alto = terreno mas
     "quebrado"/ondulado seguido; mas bajo = perfil mas liso/continuo.

Como usarlo en QGIS:
  1. Layer -> Add Layer -> Add Raster Layer -> features.tif
  2. Activar la herramienta "Identify Features" (icono con una 'i', o Ctrl+Shift+I)
  3. Click en cualquier punto del mapa -> se abre un panel mostrando el valor
     de Band 1, Band 2 y Band 3 en ese pixel exacto.
  4. Anotar (Band 2, Band 3) junto con tu clasificacion visual del punto
     (Plano / Ondulante / Hilly / Montana chica / Montana grande) para
     despues definir los umbrales.

Uso:
    python features_export.py area.tif --out features.tif --radio 1500
"""

import argparse

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy.ndimage import maximum_filter, minimum_filter, uniform_filter


def preparar_dem_proyectado(src_path):
    """Si el archivo de entrada ya esta en un CRS proyectado (metros, como
    un mosaico global en EPSG:6933), lo devuelve tal cual. Si esta en
    grados (lat/lon), lo reproyecta a la UTM correspondiente.
    """
    with rasterio.open(src_path) as src:
        ya_proyectado = not src.crs.is_geographic
        crs_actual = src.crs

    if ya_proyectado:
        print(f"Entrada ya esta en CRS proyectado ({crs_actual}), no se reproyecta.")
        return src_path

    utm_path = src_path.replace(".tif", "_utm.tif")
    return reproject_to_utm(src_path, utm_path)


def reproject_to_utm(src_path, dst_path):
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


def compute_relief_local(dem, win):
    max_local = maximum_filter(dem, size=win)
    min_local = minimum_filter(dem, size=win)
    return max_local - min_local


def compute_texture(dem, pixel_size_m, win):
    """Densidad de cambios de curvatura (subida->bajada / bajada->subida)
    dentro de la ventana local, en % de pixeles del entorno.
    """
    # curvatura aproximada (segunda derivada) en x e y
    gy, gx = np.gradient(dem, pixel_size_m)
    gyy, _ = np.gradient(gy, pixel_size_m)
    _, gxx = np.gradient(gx, pixel_size_m)
    curvature = gxx + gyy  # aprox. laplaciano

    sign = np.sign(curvature)
    # un pixel es "punto de inflexion" si su signo difiere del vecino
    # inmediato en x o en y
    flip_x = sign[:, :-1] != sign[:, 1:]
    flip_y = sign[:-1, :] != sign[1:, :]

    flip_map = np.zeros(dem.shape, dtype=np.float32)
    flip_map[:, :-1] += flip_x
    flip_map[:-1, :] += flip_y

    # densidad local: % de pixeles del entorno que son punto de inflexion
    return uniform_filter(flip_map, size=win) * 100.0 / 2.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dem_path")
    parser.add_argument("--out", default="features.tif")
    parser.add_argument(
        "--radio", type=float, default=1500, help="radio en metros para relieve y textura local"
    )
    args = parser.parse_args()

    dem_path = preparar_dem_proyectado(args.dem_path)

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        pixel_size_m = src.transform[0]
        profile = src.profile

    win = 2 * max(1, round(args.radio / pixel_size_m)) + 1

    relief_local = compute_relief_local(dem, win)
    texture = compute_texture(dem, pixel_size_m, win)

    profile.update(count=3, dtype=rasterio.float32)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(dem, 1)
        dst.write(relief_local, 2)
        dst.write(texture, 3)
        dst.set_band_description(1, "Elevacion (m)")
        dst.set_band_description(2, f"Relieve local en {args.radio:.0f}m (m)")
        dst.set_band_description(3, f"Textura de superficie en {args.radio:.0f}m (%)")

    print(f"Listo: {args.out}")
    print("Band 1 = Elevacion | Band 2 = Relieve local | Band 3 = Textura de superficie")


if __name__ == "__main__":
    main()
