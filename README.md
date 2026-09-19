# Bot de Trading para Buda.com

Bot en Python para operar en [Buda.com](https://www.buda.com) con órdenes de mercado de ejecución inmediata y órdenes límite que se reposicionan automáticamente para mantener la mejor posición en el order book.

Incluye:
- modo interactivo TUI (menú en terminal),
- modo CLI con subcomandos,
- estrategias de precio `top`, `depth` y `market`,
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
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-voice.txt
cp .env.example .env
```

El entorno `.venv` aísla las dependencias, igual que en bot-zesty. En cada terminal
nueva, ejecutá `source .venv/bin/activate` antes de iniciar el bot. Para instalar
sin micrófono, usá `requirements.txt` en lugar de `requirements-voice.txt`.
Si ya tenés `.env`, conservá ese archivo en lugar de copiar el ejemplo encima.

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
- elegir estrategia (`top`/`depth`/`market`),
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

## Asistente de texto y voz (Groq)

El menú **Asistente (texto y voz)** permite consultar saldos, puntas y los primeros
niveles del libro, y preparar compras o ventas con `top`, `depth` o `market`.
Usa los mismos motores y conversiones que la TUI manual.

Configurá estas variables en `.env` (para Groq, el entorno tiene prioridad):

```env
GROQ_API_KEY=tu_clave_de_groq
GROQ_MODEL=openai/gpt-oss-20b
```

Para texto alcanza la instalación normal. Para voz:

```bash
source .venv/bin/activate
python -m pip install -r requirements-voice.txt
python -m src.main
```

En macOS, permití acceso al micrófono a la terminal desde los ajustes del sistema.
En Linux puede ser necesario instalar PortAudio (`libportaudio2`). Sin la dependencia
de audio, podés seguir escribiendo; sin clave Groq, los menús manuales siguen funcionando.

**Ctrl+T** desde el menú principal o el menú del asistente abre directamente el
dictado, sin seleccionar **Hablar**. También podés elegir **Escribir pedido** o
**Hablar** dentro del asistente. El atajo no está activo en formularios, revisiones
ni durante la ejecución de una estrategia. Al grabar, **Enter** o
**Ctrl+T** termina y **Escape/Ctrl+C** descarta. La captura termina automáticamente
a los 30 segundos. El dictado se transcribe en español con `whisper-large-v3-turbo`,
se muestra y se envía automáticamente al asistente. No hay escucha permanente ni
respuestas habladas. El dictado incluye contexto de criptomonedas para ayudar a
reconocer siglas: podés decir «Bitcoin», «USD Coin» o «Tether», o deletrear
«be te ce», «u ese de ce» y «u ese de te». El asistente tiene instrucciones de
pedir aclaración ante siglas ambiguas, sin confundir USD con USDC o USDT.
Durante la transcripción, Ctrl+C cancela la espera y vuelve
al menú; el audio puede haber llegado a Groq.

Ejemplos:

- «¿Cuánto USDC tengo disponible?»
- «Mostrame las puntas de bitcoin».
- «Prepará una compra de bitcoin por 10000 CLP usando top en simulación».
- «Prepará una venta real de 0.001 BTC a mercado».

Los pedidos deben indicar moneda, importe, unidad y estrategia. Si falta información,
el asistente debe pedirla. Se validan localmente los parámetros y las unidades;
CLP, COP y PEN no son intercambiables. USD se convierte usando el mercado USDC
como aproximación, igual que en la TUI manual.

**Preparar no envía una orden.** El resumen muestra mercado, importe convertido,
estrategia, intervalo y modo real/simulación. La confirmación se hace exclusivamente
por teclado y tiene **No** como opción predeterminada. Decir «confirmar» en el chat
no ejecuta operaciones. Si no pedís modo real, el asistente prepara una simulación.
Los defaults son 30 segundos de monitoreo (1 para mercado); `depth` requiere ratio.

El asistente queda en pausa mientras se ejecuta el bot; Ctrl+C conserva el mecanismo
existente de detención y limpieza. Las grillas y el control de estrategias en ejecución
se manejan desde el menú principal. Esta versión no incorpora control de estrategias por voz.

Groq recibe el pedido, hasta tres intercambios recientes acotados, las monedas
habilitadas y los datos necesarios de las consultas. Las credenciales de Buda no
se incluyen. El audio y el historial no se guardan en disco por esta aplicación.
**Limpiar conversación** borra el historial; también se borra después de revisar
una operación y al salir del asistente. Las llamadas a Groq consumen la cuota de tu
cuenta, también en simulación. Los fallos de Groq no se reintentan automáticamente.

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

### `market`

Ejecuta una operación inmediata, sin reposicionamientos. Las compras usan una
cotización de precio reservado de Buda para fijar el monto quote; las ventas
envían una única orden de mercado nativa. Disponible desde la TUI y el CLI:

```bash
python3 -m src.main buy btc 100000 --strategy market --dry-run
python3 -m src.main sell btc 0.001 --strategy market --dry-run
```

Quita `--dry-run` para ejecutar la operación real.

- **Compra:** el monto sigue expresado en moneda quote. El bot solicita una
  cotización `bid_given_value`, rechaza cotizaciones incompletas o que excedan
  el monto indicado y confirma el precio reservado. La comisión informada por
  Buda es adicional y también debe caber en el saldo disponible.
- **Venta:** envía la cantidad de cripto indicada, redondeada a la precisión del mercado.
- Verifica saldo y mínimo de operación. Si no hay suficiente profundidad para
  cotizar la compra completa, no la confirma.
- Sigue el estado de la misma orden hasta finalizar; una cancelación o ejecución
  parcial no genera otra orden por el remanente. El resumen usa los montos realmente
  transados, antes de comisiones.
- `--interval` controla las consultas de estado (entre 0.5 y 5 segundos);
  no hay reposicionamiento. `--depth` no afecta el precio de mercado.
- Tras tres errores consecutivos al consultar el estado, el monitoreo termina
  conservando el ID para que la operación pueda verificarse manualmente.
- `--dry-run` valida y muestra la cantidad, sin publicar órdenes ni simular fills.
- Ante timeout o error de conexión al crear/confirmar una cotización o enviar
  una venta, no reintenta automáticamente: revisa tus órdenes en Buda antes de
  volver a ejecutar el comando.

Contratos de órdenes y unidades: [documentación oficial de Buda](https://api.buda.com/#obtener-mis-ordenes)
y [órdenes de precio reservado](https://api.buda.com/#nueva-orden-de-precio-reservado).

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
