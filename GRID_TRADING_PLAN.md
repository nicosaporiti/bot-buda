# Plan de implementacion: estrategia de trading de grilla

## Objetivo

Agregar una estrategia spot de grilla al bot de Buda.com para operar multiples ordenes limite en un rango de precios. La estrategia debe poder correr primero en `dry-run`, mostrar exactamente que ordenes publicaria, y solo operar con fondos autorizados por el usuario.

La grilla no reemplaza las estrategias actuales `top` y `depth`. Se implementa como un motor separado porque el bot actual mantiene una sola orden activa por lado, mientras que una grilla necesita administrar muchas ordenes abiertas, fills parciales y ordenes espejo.

## Definiciones

- `quote_budget`: monto maximo del saldo disponible en la moneda quote que el bot puede usar para compras de la grilla. En `btc-clp`, el quote es `CLP`; en `btc-cop`, el quote es `COP`; en `btc-pen`, el quote es `PEN`.
- `base_budget`: monto maximo del saldo disponible en la moneda base que el bot puede usar para ventas iniciales. En `btc-clp`, el base es `BTC`.
- `lower`: precio inferior del rango.
- `upper`: precio superior del rango.
- `levels`: cantidad de niveles de precio dentro del rango.
- `range_pct`: porcentaje usado para calcular automaticamente `lower` y `upper` desde el precio actual.
- `max_open_orders`: limite de ordenes abiertas simultaneamente por la grilla.

`quote_budget` no transfiere fondos ni cambia el saldo de la cuenta. Es un limite interno: el bot no debe abrir ordenes de compra cuyo costo total comprometido supere ese monto. Las compras abiertas congelan saldo en Buda; cuando una compra se ejecuta, parte de ese presupuesto pasa a inventario base y se habilita una venta espejo.

## Modos de grilla

La estrategia debe poder lanzarse desde la CLI y, como experiencia final, desde el TUI. La CLI se implementa primero porque permite probar `dry-run`, tests y casos borde con menos superficie interactiva. El TUI debe llamar al mismo motor de grilla, no duplicar logica de estrategia.

### 1. Rango manual

El usuario fija el rango y el bot calcula los niveles.

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

### 2. Rango automatico

El usuario fija el ancho del rango y el bot lo centra en el precio actual.

```bash
python3 -m src.main grid btc \
  --range-pct 10 \
  --levels 12 \
  --quote-budget 500000 \
  --max-open-orders 6 \
  --interval 10 \
  --dry-run
```

Si el precio actual es `100.000.000` y `--range-pct 10`, el bot calcula:

- `lower = 90.000.000`
- `upper = 110.000.000`

Luego genera `levels` niveles entre ambos precios, redondeados al tick del mercado.

## Comportamiento de la estrategia

MVP recomendado: grilla spot long.

1. Obtener precio actual desde order book o ticker.
2. Calcular niveles con rango manual o automatico.
3. Publicar ordenes de compra bajo el precio actual usando `quote_budget`.
4. Si se ejecuta una compra en nivel `i`, publicar una venta en nivel `i + 1`.
5. Si se ejecuta una venta en nivel `i + 1`, publicar una nueva compra en nivel `i`.
6. Mantener como maximo `max_open_orders` abiertas.
7. En `Ctrl+C`, cancelar todas las ordenes activas de la grilla y mostrar resumen.

Con `base_budget`, el bot tambien puede publicar ventas iniciales sobre el precio actual. Sin `base_budget`, la grilla parte solo con compras bajo el precio actual.

## Sizing de ordenes

Para el MVP:

- Dividir `quote_budget` entre la cantidad de niveles de compra iniciales permitidos.
- Para cada compra, calcular `base_amount = quote_per_order / price`.
- Redondear `base_amount` hacia abajo usando `market_config.base_decimals`.
- Redondear precios usando `market_config.price_tick`.
- Rechazar ordenes bajo `market_config.min_order_amount`.
- Rechazar configuraciones donde el presupuesto por nivel no alcanza el minimo.

Ejemplo:

- Mercado: `btc-clp`
- `quote_budget = 500000`
- compras iniciales: 5
- presupuesto por compra: `100000 CLP`

Cada orden de compra usa hasta `100000 CLP`, ajustada por redondeo de precio y cantidad.

## Controles de riesgo

Validaciones antes de operar:

- `lower < upper`
- `levels >= 2`
- `range_pct > 0` cuando se usa rango automatico
- niveles unicos despues de redondear al tick
- `quote_budget > 0`
- `quote_budget <= saldo quote disponible`
- `base_budget <= saldo base disponible` cuando se use
- cada orden cumple el minimo del mercado
- `max_open_orders > 0`
- no publicar ordenes que crucen el spread

Controles durante ejecucion:

- no superar `quote_budget` comprometido en compras abiertas y compras ejecutadas no vendidas
- no superar `base_budget` comprometido en ventas abiertas
- cancelar todas las ordenes activas de la grilla al detener
- capturar fills parciales sin duplicar ordenes espejo
- tolerar WebSocket stale usando fallback REST

## Arquitectura propuesta

### Nuevos archivos

- `src/grid.py`: motor principal de la grilla.
- `src/grid_types.py` o dataclasses dentro de `src/grid.py`: configuracion, niveles, ordenes y estado.

### Cambios en archivos existentes

- `src/main.py`: agregar subcomando `grid`.
- `src/api.py`: extender `create_limit_order` para aceptar `client_id` si Buda lo soporta.
- `src/ws.py`: reutilizar estado de order book y ordenes.
- `README.md`: documentar uso de `grid`, `quote_budget`, modo automatico y riesgos.

No modificar `TradingBot.execute_buy_order()` ni `TradingBot.execute_sell_order()` salvo para extraer helpers claramente compartidos. La grilla debe vivir como estrategia separada.

## Modelo de datos sugerido

```python
@dataclass(frozen=True)
class GridConfig:
    market_config: MarketConfig
    lower_price: Decimal | None
    upper_price: Decimal | None
    range_pct: Decimal | None
    levels: int
    quote_budget: Decimal
    base_budget: Decimal
    max_open_orders: int
    interval: int
    dry_run: bool

@dataclass(frozen=True)
class GridLevel:
    index: int
    price: Decimal

@dataclass
class GridOrder:
    order_id: str
    side: str
    level_index: int
    amount: Decimal
    price: Decimal
    traded_amount: Decimal
    state: str
```

## Loop de ejecucion

1. Validar configuracion y balances.
2. Iniciar realtime book y orders si hay `pubsub_key`.
3. Calcular niveles.
4. Calcular ordenes iniciales.
5. Publicar ordenes iniciales o mostrarlas si `dry-run`.
6. En cada intervalo o evento:
   - leer estado de ordenes activas,
   - detectar fills nuevos,
   - actualizar inventario y presupuesto comprometido,
   - publicar orden espejo cuando corresponda,
   - mantener `max_open_orders`,
   - refrescar book por REST si realtime esta stale.
7. Al terminar:
   - cancelar ordenes activas si no es `dry-run`,
   - imprimir resumen de compras, ventas, inventario y PnL bruto estimado.

## Iteraciones de implementacion

### Iteracion 1: calculo y dry-run

- Crear generador de niveles manual y automatico.
- Crear sizing de ordenes iniciales.
- Validar balances, ticks, decimales y minimos.
- Agregar `grid --dry-run`.
- Tests unitarios de calculo.

Resultado esperado: el usuario puede ver la grilla completa sin publicar ordenes.

### Iteracion 2: ejecucion real basica

- Publicar compras iniciales.
- Trackear ordenes abiertas.
- Cancelar todas las ordenes al detener.
- Mostrar resumen final.

Resultado esperado: grilla real de compras iniciales, aun sin ciclos completos buy/sell.

### Iteracion 3: ordenes espejo

- Al llenarse una compra, publicar venta en el siguiente nivel.
- Al llenarse una venta, publicar compra en el nivel anterior.
- Manejar fills parciales sin duplicar espejo.

Resultado esperado: grilla funcional que recicla inventario.

### Iteracion 4: persistencia y recuperacion

- Agregar `client_id` por orden si Buda lo soporta.
- Guardar estado local en `.grid-state/` o archivo configurable.
- Permitir reanudar una grilla despues de reiniciar.

Resultado esperado: el bot puede recuperar ordenes propias sin confundirse con ordenes manuales del usuario.

### Iteracion 5: TUI

- Agregar opcion "Grilla" al menu.
- Prompts para rango manual/automatico, niveles y presupuestos.
- Confirmacion visual antes de ejecutar.
- Lanzar el mismo `GridTradingBot` usado por la CLI.
- Soportar `dry-run` desde el TUI antes de permitir ejecucion real.

Resultado esperado: la grilla se puede operar desde el menu interactivo.

## Tests minimos

- `range_pct` calcula `lower` y `upper` correctamente.
- niveles manuales incluyen extremos y respetan tick.
- niveles duplicados por redondeo fallan con error claro.
- sizing respeta `base_decimals`.
- presupuesto por nivel bajo minimo falla.
- `quote_budget` no puede superar saldo disponible.
- compra ejecutada en nivel `i` crea venta en nivel `i + 1`.
- venta ejecutada en nivel `i + 1` crea compra en nivel `i`.
- fill parcial no crea orden espejo duplicada.
- `dry-run` no llama `create_limit_order` ni `cancel_order`.

## Decisiones pendientes

1. `quote_budget` debe reservarse contra compras abiertas solamente, o tambien contra compras ejecutadas aun no vendidas. Recomendacion: contar ambas para limitar exposicion total.
2. Para rango automatico, usar ticker `last_price` o mid-price del order book. Recomendacion: usar mid-price si hay bid/ask validos, fallback a ticker.
3. Persistencia en MVP. Recomendacion: no en Iteracion 1; si se opera real, agregar `client_id` y persistencia antes de dejarla corriendo sin supervision.
4. Fees. Recomendacion: agregar `--fee-bps` para estimar PnL, pero no bloquear MVP si solo se muestra PnL bruto.
