# GeoGuessr Pinpoint Trainer (extensión de Chrome)

Subproyecto independiente: no depende del backend ni del frontend de la app principal.
Al empezar cada ronda de un mapa comunitario que listes, centra el minimapa con el zoom
elegido y un offset aleatorio respecto al lugar real, para entrenar el pinpointing.

## Instalar
1. `chrome://extensions` → activar "Modo desarrollador".
2. "Cargar descomprimida" → elegir esta carpeta (`extension/`).
3. Abrir el popup, activar, elegir zoom/offset y escribir los nombres de los mapas (uno por línea, exactos).
4. Recargar la pestaña de GeoGuessr (también después de cada actualización de la extensión).

## Archivos
- `manifest.json`: Manifest V3.
- `src/inject.js` (mundo MAIN): engancha `google.maps.Map` para capturar el minimapa (el que está
  dentro de un elemento `guess-map*`), lee la ubicación real de `/api/v3/games/{token}` y, cuando el
  minimapa aparece en pantalla, hace `setZoom` + `setCenter`. Muestra un cartelito de estado abajo
  al centro (verde = aplicado, amarillo = esperando, rojo = problema, gris = desactivada);
  su botón ↻ reaplica en la ronda actual (con las mismas reglas de abajo).
- `src/bridge.js`: lee `chrome.storage` y le pasa la config a `inject.js`.
- `popup/`: configuración. `icons/`: íconos.

## Reglas
Todas se verifican juntas, en cada aplicación (función `active()` en `inject.js`):
- Solo corre en `/game/{token}` (nunca duelos ni modos en vivo), solo si está activada y solo si
  `mapName` está en la lista.
- Offset: % de la mitad de la ventana del minimapa (referencia 232 px = 775 × 0.6 / 2, la misma
  del análisis de replays). 0 % deja el lugar real al centro; 90 % casi en el borde.
- Si tocás el minimapa (click o rueda) deja de forzar la vista.

## Notas para mantener
- GeoGuessr reutiliza el mismo mapa entre rondas y lo desconecta de la página en los resultados:
  por eso `inject.js` nunca descarta minimapas capturados y aplica recién cuando el minimapa
  vuelve a ser visible.
- Depende de detalles internos de GeoGuessr (clase `guess-map`, forma de `/api/v3/games/{token}`).
  Si cambian, el cartelito pasa a rojo.
