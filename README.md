# Bot de Trading para Buda.com

Bot en Python para operar `BTC-CLP` y `USDC-CLP` con órdenes límite que se reposicionan automáticamente para mantener precio objetivo.

Incluye:
- modo interactivo TUI (menú en terminal),
- modo CLI con subcomandos,
- estrategia `top` y `depth`,
- soporte realtime por WebSocket con fallback a REST.

## Requisitos

- Python 3.10+
- API key/secret de Buda: https://www.buda.com/api-keys

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env
```

Configura `.env`:

```env
BUDA_API_KEY=tu_api_key
BUDA_API_SECRET=tu_api_secret
```

## Modos de uso

### 1) TUI interactiva (por defecto)

Si ejecutas sin subcomando, se abre el menú interactivo:

```bash
python3 -m src.main
```

Desde la TUI puedes:
- comprar o vender BTC/USDC,
- elegir estrategia (`top`/`depth`),
- configurar intervalo y `dry-run`,
- ingresar montos en CLP, USD o crypto (conversión automática usando ticker),
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
python3 -m src.main buy btc 100000 --interval 60 --dry-run
```

#### Vender

```bash
python3 -m src.main sell btc 0.001
python3 -m src.main sell usdc 50 --strategy depth --depth 0.9
python3 -m src.main sell btc 0.001 --interval 60 --dry-run
```

#### Utilidades

```bash
python3 -m src.main balance
python3 -m src.main balance clp
python3 -m src.main orderbook btc-clp
python3 -m src.main orderbook usdc-clp
```

## Estrategias de precio

- `top` (default):
  - compra: `best_bid + tick`
  - venta: `best_ask - tick`
- `depth`:
  - compra: acumula volumen bid desde precio bajo a alto hasta `depth_ratio`
  - venta: acumula volumen ask desde precio alto a bajo hasta `depth_ratio`

Parámetros:
- `--strategy top|depth`
- `--depth` entre `0` y `1` (default `0.9`)

## Mercados, mínimos y ticks

- Mercados soportados: `btc-clp`, `usdc-clp`
- Monto mínimo compra (en CLP):
  - `BTC-CLP`: `2000 CLP`
  - `USDC-CLP`: `10 CLP`
- Monto mínimo venta (en crypto):
  - `BTC-CLP`: `0.00002 BTC`
  - `USDC-CLP`: `0.01 USDC`
- Tick de precio:
  - `BTC-CLP`: `1 CLP`
  - `USDC-CLP`: `0.01 CLP`

## Realtime (WebSocket) y fallback

El bot usa WebSocket para:
- order book realtime (`book@...`)
- estado de órdenes (`orders@...`, cuando hay `pubsub_key`)

Comportamiento:
- espera snapshot inicial del book,
- si el stream está stale, usa REST automáticamente,
- realiza sanity check periódico por REST para refrescar snapshot.

Debug de mensajes WS:

```bash
BUDA_WS_DEBUG=1 BUDA_WS_DEBUG_LIMIT=5 python3 -m src.main buy usdc 300
```

## Manejo de ejecución

- Hace tracking de ejecuciones parciales.
- Si cambia el precio objetivo, cancela y vuelve a publicar con el remanente.
- En `Ctrl+C`, intenta cancelar orden activa y muestra resumen final.
- En `dry-run`, no publica ni cancela órdenes reales.

## Dependencias principales

- `requests`
- `websocket-client`
- `certifi`
- `rich`
- `InquirerPy`

## Estructura del proyecto

```text
bot-buda/
├── .env.example
├── requirements.txt
└── src/
    ├── main.py
    ├── config.py
    ├── auth.py
    ├── api.py
    ├── bot.py
    ├── ws.py
    ├── utils.py
    └── tui/
        ├── __init__.py
        ├── app.py
        ├── prompts.py
        └── display.py
```

## Troubleshooting

### `CERTIFICATE_VERIFY_FAILED` en WS

1. Verifica instalación de dependencias:
   ```bash
   pip install -r requirements.txt
   ```
2. Reintenta.

### `Realtime book not ready` o `Realtime book stale`

- `not ready`: todavía no llegó snapshot/update inicial, se usa REST temporalmente.
- `stale`: no hubo updates recientes, se hace fallback a REST.
