# Bot de Trading para Buda.com

Bot en Python para operar en [Buda.com](https://www.buda.com) con órdenes límite que se reposicionan automáticamente para mantener la mejor posición en el order book.

Incluye:
- modo interactivo TUI (menú en terminal),
- modo CLI con subcomandos,
- estrategias de precio `top` y `depth`,
- estrategia de grilla (`grid`) con rango manual o automático,
- order book en tiempo real por WebSocket con fallback a REST,
- tracking de ejecuciones parciales con resumen final.

## Requisitos

- Python 3.10+
- API key/secret de Buda: https://www.buda.com/api-keys

## Uso seguro

Los comandos `buy` y `sell` publican y cancelan órdenes reales cuando no usas `--dry-run`. Antes de operar con montos reales, prueba el flujo con `--dry-run` y revisa que `BUDA_QUOTE_CURRENCY` apunte a la moneda quote correcta (`clp`, `cop` o `pen`).

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env
```

Configura `.env`:

```env
BUDA_API_KEY=tu_api_key
BUDA_API_SECRET=tu_api_secret
BUDA_QUOTE_CURRENCY=clp  # Opciones: clp, cop, pen
```

## Mercados soportados

Los mercados se cargan dinámicamente desde la API según la moneda quote configurada (`BUDA_QUOTE_CURRENCY`).

**CLP:** btc-clp, eth-clp, ltc-clp, bch-clp, usdc-clp, usdt-clp
**COP:** btc-cop, eth-cop, ltc-cop, bch-cop, usdc-cop, usdt-cop
**PEN:** btc-pen, eth-pen, ltc-pen, bch-pen, usdc-pen, usdt-pen

### Precisión y ticks de precio

| Moneda | Decimales | Tick de precio (CLP) | Tick (COP) | Tick (PEN) |
|--------|-----------|----------------------|------------|------------|
| BTC    | 8         | 1                    | 1          | 0.01       |
| ETH    | 8         | 1                    | 1          | 0.01       |
| LTC    | 8         | 1                    | 1          | 0.01       |
| BCH    | 8         | 1                    | 1          | 0.01       |
| USDC   | 6         | 0.01                 | 0.01       | 0.0001     |
| USDT   | 6         | 0.01                 | 0.01       | 0.0001     |

Los montos mínimos por mercado se obtienen dinámicamente desde la API.

## Modos de uso

### 1) TUI interactiva (por defecto)

Si ejecutas sin subcomando, se abre el menú interactivo:

```bash
python3 -m src.main
```

Desde la TUI puedes:
- comprar o vender cualquier crypto disponible,
- elegir estrategia (`top`/`depth`),
- configurar intervalo y `dry-run`,
- ingresar montos en moneda quote (CLP/COP/PEN), USD o crypto (conversión automática usando ticker),
- consultar balances y order book.

### 2) CLI por subcomandos

```bash
python3 -m src.main --help
python3 -m src.main buy --help
python3 -m src.main sell --help
```

#### Comprar

```bash
python3 -m src.main buy btc 100000
python3 -m src.main buy usdc 50000 --strategy depth --depth 0.9
python3 -m src.main buy eth 200000 --interval 60 --dry-run
```

#### Vender

```bash
python3 -m src.main sell btc 0.001
python3 -m src.main sell usdc 50 --strategy depth --depth 0.9
python3 -m src.main sell eth 0.1 --interval 60 --dry-run
```

#### Utilidades

```bash
python3 -m src.main balance          # Todos los balances
python3 -m src.main balance clp      # Balance específico
python3 -m src.main orderbook btc-clp
python3 -m src.main orderbook usdc-clp
```

## Estrategia de grilla (`grid`)

La grilla mantiene múltiples órdenes límite en un rango de precios. Cuando se ejecuta una compra en un nivel, publica una venta en el siguiente; cuando se ejecuta una venta, publica una compra en el nivel anterior.

> **Importante:** la grilla mueve fondos reales cuando no usas `--dry-run`. Antes de operar con dinero real, prueba siempre el flujo en `dry-run` y revisa los niveles generados.

### Rango manual

Tú fijas `--lower` y `--upper`, el bot genera `--levels` precios redondeados al tick del mercado:

```bash
python3 -m src.main grid btc \
  --lower 90000000 \
  --upper 110000000 \
  --levels 12 \
  --quote-budget 500000 \
  --max-open-orders 6 \
  --interval 10 \
  --dry-run
```

### Rango automático

Tú fijas `--range-pct`; el bot lo centra en el precio medio del order book:

```bash
python3 -m src.main grid btc \
  --range-pct 10 \
  --levels 12 \
  --quote-budget 500000 \
  --max-open-orders 6 \
  --interval 10 \
  --dry-run
```

### Parámetros

| Flag | Descripción |
|------|-------------|
| `--lower` / `--upper` | Bordes del rango (modo manual) |
| `--range-pct` | Distancia porcentual desde el precio actual hacia `lower` y `upper`; `10` = `-10%` / `+10%` (banda total `20%`) |
| `--levels` | Cantidad de niveles (>= 2) |
| `--quote-budget` | Tope de moneda quote a comprometer en compras |
| `--base-budget` | Tope de base para ventas iniciales (default `0`) |
| `--max-open-orders` | Máximo de órdenes abiertas en simultáneo (default `6`) |
| `--interval` | Intervalo de monitoreo en segundos (default `10`) |
| `--dry-run` | No publica órdenes reales, sólo muestra la grilla |

### Comportamiento

- Sin `--base-budget`, la grilla parte sólo con compras bajo el precio actual.
- Con `--base-budget`, también se publican ventas iniciales arriba del precio actual.
- `quote_budget` se reparte entre las compras iniciales permitidas (`min(niveles_compra, max_open_orders)`).
- Cualquier orden que cruzaría el spread es omitida: las iniciales se difieren a una cola y se reintentan en cada tick; los espejos se reintentan en el siguiente tick.
- Si **ninguna** orden inicial logra colocarse, la grilla aborta con error claro en vez de quedar inactiva en silencio.
- En `Ctrl+C`, cancela todas las órdenes activas, espera la confirmación de cancelación de cada una y muestra resumen (compras, ventas, inventario neto, PnL bruto en quote).
- El bot rechaza configuraciones donde el monto por nivel queda bajo el mínimo de mercado o donde los niveles colapsan al redondear al tick.

### Riesgos

- La grilla reserva saldo en Buda con cada orden abierta; verifica que `--quote-budget` no exceda tu saldo disponible.
- Si el precio sale del rango, la grilla deja de operar en ese lado hasta volver al rango.
- No persiste estado entre reinicios: cancela manualmente cualquier orden colgada antes de relanzar.
- No incorpora fees en el cálculo de PnL; el resumen muestra PnL bruto.

## Estrategias de precio

### `top` (default)

Posiciona la orden un tick por encima (compra) o por debajo (venta) de la mejor oferta:
- **Compra:** `best_bid + tick`
- **Venta:** `best_ask - tick`

Cuando el volumen de la mejor punta coincide con el remanente de la orden
activa, el bot reconoce ese nivel como propio y usa la siguiente punta como
referencia. Si esa segunda punta se aleja, también recotiza hacia ella para
quedar a un solo tick, en vez de mantener un precio innecesariamente agresivo.
Si el volumen está agregado o momentáneamente desincronizado, conserva el precio
hasta tener una referencia inequívoca.
Después de cancelar, recuerda temporalmente ese nivel como propio para que la
propagación tardía del book no revierta la nueva cotización.

Si ese precio cruzaría el spread, el bot conserva el `best_bid` o `best_ask` actual para evitar ejecución inmediata.

### `depth`

Calcula el precio objetivo acumulando volumen en el order book hasta alcanzar un ratio del volumen total:
- **Compra:** acumula bids de bajo a alto hasta `depth_ratio` del volumen total
- **Venta:** acumula asks de alto a bajo hasta `depth_ratio` del volumen total

Parámetros:
- `--strategy top|depth`
- `--depth` entre `0` y `1` (default `0.9`)

## Realtime (WebSocket) y fallback

El bot usa WebSocket para recibir:
- **Order book** en tiempo real (`book@{market}`)
- **Estado de órdenes** propias (`orders@{pubsub_key}`, si está disponible)

Comportamiento:

- espera snapshot inicial del book antes de operar,
- si recibe deltas antes del snapshot o después de reconectar, solicita de
  inmediato un snapshot completo por REST,
- si el stream está stale (sin updates recientes), cae a REST automáticamente,
- realiza sanity check periódico cada 120s por REST para refrescar el snapshot.

Debug de mensajes WS:

```bash
BUDA_WS_DEBUG=1 BUDA_WS_DEBUG_LIMIT=5 python3 -m src.main buy usdc 300
```

## Manejo de ejecución

- Tracking de ejecuciones parciales (monto ejecutado, crypto recibido, precio promedio).
- Si cambia el precio objetivo, cancela la orden activa y republica con el remanente.
- Intervalo mínimo de 0.5s entre reposicionamientos.
- En `Ctrl+C` (SIGINT/SIGTERM), cancela la orden activa y muestra resumen final.
- En `dry-run`, no publica ni cancela órdenes reales.

## Estructura del proyecto

```text
bot-buda/
├── .env.example
├── requirements.txt
├── buda-api-documentation.md
└── src/
    ├── main.py          # Entry point, CLI con argparse
    ├── config.py         # Carga credenciales desde .env
    ├── auth.py           # Firma HMAC-SHA384
    ├── api.py            # Cliente REST con retry y rate limiting
    ├── bot.py            # Lógica de trading: estrategias, monitoring loop
    ├── grid.py           # Motor de grilla (estrategia separada)
    ├── grid_types.py     # Dataclasses GridConfig / GridLevel / GridOrder
    ├── market.py         # Registro dinámico de mercados desde la API
    ├── ws.py             # Cliente WebSocket (order book + órdenes)
    ├── utils.py          # Formateo y utilidades
    └── tui/
        ├── __init__.py
        ├── app.py        # Loop principal TUI
        ├── prompts.py    # Prompts interactivos (InquirerPy)
        └── display.py    # Display con Rich (tablas, paneles)
```

## Dependencias

- `requests` — cliente HTTP
- `websocket-client` — conexión WebSocket
- `certifi` — verificación SSL
- `rich` — formateo de terminal (colores, tablas, paneles)
- `InquirerPy` — prompts interactivos para la TUI

## Troubleshooting

### `CERTIFICATE_VERIFY_FAILED` en WS

```bash
pip install -r requirements.txt  # Asegura certifi actualizado
```

### `Realtime book not ready` / `Realtime book stale`

- **not ready**: todavía no llegó el snapshot inicial, se usa REST temporalmente.
- **stale**: no hubo updates recientes, se hace fallback a REST.
