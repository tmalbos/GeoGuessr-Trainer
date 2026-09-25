"""Descarga el DEM SRTM 90m (SRTMGL3) del mundo entero, partido en tiles
de 15x15 grados (bien por debajo del limite de 4.050.000 km2 por
request que impone la API de OpenTopography).

Uso:
    python download_global.py TU_API_KEY --out_dir maps/srtm_global
"""

import argparse
import os
import pathlib
import time
import urllib.error
import urllib.request

BASE_URL = "https://portal.opentopography.org/API/globaldem"
TILE_SIZE = 15  # grados. Con esto ningun tile supera el limite de area.


def descargar_tile(api_key, south, north, west, east, out_path, reintentos=3) -> str:
    params = (
        f"demtype=SRTMGL3&south={south}&north={north}"
        f"&west={west}&east={east}&outputFormat=GTiff&API_Key={api_key}"
    )
    url = f"{BASE_URL}?{params}"

    for intento in range(1, reintentos + 1):
        try:
            urllib.request.urlretrieve(url, out_path)
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode(errors="ignore")[:300]
            print(f"  intento {intento}/{reintentos} fallo HTTP {e.code}: {cuerpo}")
            if pathlib.Path(out_path).exists():
                pathlib.Path(out_path).unlink()
            time.sleep(5)
            continue
        except urllib.error.URLError as e:
            print(f"  intento {intento}/{reintentos} fallo de red: {e}")
            if pathlib.Path(out_path).exists():
                pathlib.Path(out_path).unlink()
            time.sleep(5)
            continue

        tamano = pathlib.Path(out_path).stat().st_size
        if tamano == 0:
            # respuesta vacia sin error HTTP = tile 100% oceano, sin tierra
            # que devolver. No es una falla, no tiene sentido reintentar.
            pathlib.Path(out_path).unlink()
            return "sin_datos"
        if tamano < 1000:
            # chico pero no vacio: probablemente un mensaje de error en texto
            contenido = pathlib.Path(out_path).read_text(encoding="utf-8", errors="ignore")
            print(f"  intento {intento}/{reintentos} respuesta rara: {contenido[:300]!r}")
            pathlib.Path(out_path).unlink()
            time.sleep(5)
            continue
        return "ok"

    return "error"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("api_key")
    parser.add_argument("--out_dir", default="srtm_global")
    parser.add_argument(
        "--espera",
        type=float,
        default=2.0,
        help="segundos de espera entre requests, para no saturar la API",
    )
    args = parser.parse_args()

    pathlib.Path(args.out_dir).mkdir(exist_ok=True, parents=True)

    # SRTM solo cubre -56 a 60 grados de latitud (no tiene datos de los polos,
    # la mision que lo genero no volo sobre esas zonas). Pedir tiles fuera de
    # ese rango siempre va a fallar, asi que ni los intentamos.
    lats = list(range(-60, 60, TILE_SIZE))
    lons = list(range(-180, 180, TILE_SIZE))
    total = len(lats) * len(lons)

    fallidos = []
    sin_datos = 0
    contador = 0
    for south in lats:
        north = south + TILE_SIZE
        for west in lons:
            east = west + TILE_SIZE
            contador += 1
            nombre = f"srtm_{south}_{west}.tif"
            out_path = os.path.join(args.out_dir, nombre)

            if pathlib.Path(out_path).exists():
                print(f"[{contador}/{total}] {nombre} ya existe, salteo")
                continue

            print(
                f"[{contador}/{total}] descargando {nombre} "
                f"(lat {south} a {north}, lon {west} a {east})"
            )
            resultado = descargar_tile(args.api_key, south, north, west, east, out_path)

            if resultado == "sin_datos":
                print("  sin tierra en esta zona (oceano), saltando")
                sin_datos += 1
            elif resultado == "error":
                fallidos.append(nombre)
                print(f"  FALLO DEFINITIVO: {nombre}")

            time.sleep(args.espera)

    print("\nListo.")
    print(f"{sin_datos} tiles eran oceano (sin datos, esperado)")
    if fallidos:
        print(
            f"{len(fallidos)} tiles fallaron por error real, volver a correr el script "
            f"los reintenta (los que ya bajaron bien se saltean):"
        )
        for f in fallidos:
            print(f"  {f}")
    else:
        print("Todos los tiles con tierra se descargaron correctamente.")


if __name__ == "__main__":
    main()
