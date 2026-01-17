# Bot de Trading para Buda.com

Bot CLI en Python para comprar BTC o USDC en Buda.com con órdenes límite que mantienen la mejor posición de compra (best bid) automáticamente.

## Instalación

```bash
# Instalar dependencias
pip install -r requirements.txt

# Configurar credenciales
cp .env.example .env
```

Editar `.env` con tus API keys de Buda.com (https://www.buda.com/api-keys):

```
BUDA_API_KEY=tu_api_key
BUDA_API_SECRET=tu_api_secret
```

## Comandos Disponibles

### Comprar Criptomonedas

```bash
# Comprar BTC con 100,000 CLP
python3 -m src.main buy btc 100000

# Comprar USDC con 50,000 CLP
python3 -m src.main buy usdc 50000

# Cambiar intervalo de monitoreo a 60 segundos (default: 30)
python3 -m src.main buy btc 100000 --interval 60

# Modo simulación (no ejecuta órdenes reales)
python3 -m src.main buy btc 100000 --dry-run
```

### Consultar Balance

```bash
# Ver balance en CLP
python3 -m src.main balance clp

# Ver balance en BTC
python3 -m src.main balance btc

# Ver balance en USDC
python3 -m src.main balance usdc
```

### Ver Order Book

```bash
# Order book de BTC-CLP
python3 -m src.main orderbook btc-clp

# Order book de USDC-CLP
python3 -m src.main orderbook usdc-clp
```

### Ayuda

```bash
python3 -m src.main --help
python3 -m src.main buy --help
```

## Cómo Funciona

1. **Verificación de saldo**: Confirma que tienes suficiente CLP
2. **Análisis del order book**: Obtiene el mejor precio de compra (best bid) y venta (best ask)
3. **Cálculo de precio óptimo**: Coloca la orden a `best_bid + 1 CLP` para ser el primero en la fila
4. **Colocación de orden límite**: Crea una orden de compra (Bid) al precio calculado
5. **Monitoreo continuo**: Cada 30 segundos (configurable):
   - Si la orden se ejecutó completamente → termina con resumen
   - Si seguimos siendo best bid → espera
   - Si nos superaron → cancela, trackea ejecución parcial, y coloca nueva orden con el monto restante
   - Si el monto restante es menor al mínimo → termina con resumen

## Características

- Mantiene automáticamente la mejor posición de compra
- Reposiciona la orden si otro comprador ofrece más
- **Manejo de ejecuciones parciales**: si parte de la orden se ejecuta antes de reposicionar, el bot continúa solo con el monto restante
- Cancela órdenes pendientes al salir con `Ctrl+C` y muestra resumen de ejecución
- Manejo de rate limits con reintentos automáticos
- Modo dry-run para probar sin riesgo

## Manejo de Ejecuciones Parciales

El bot trackea correctamente las ejecuciones parciales durante el proceso de compra:

1. **Durante el monitoreo**: Si la orden se ejecuta parcialmente, el bot registra el crypto recibido y CLP gastado
2. **Al reposicionar**: Si nos superan en precio, el bot cancela la orden y coloca una nueva solo con el CLP restante
3. **Monto mínimo**: Si el CLP restante es menor al mínimo (BTC: 2,000 / USDC: 1,000), el bot termina exitosamente
4. **Resumen final**: Al terminar (ya sea por completar la orden o por `Ctrl+C`), muestra un resumen con:
   - CLP total gastado
   - Crypto total recibido
   - Precio promedio de compra

### Ejemplo de Ejecución Parcial

```
[!] Outbid! Our price: $84.429.573 CLP, Best bid: $84.429.580 CLP
[+] Partial execution before cancel: 0.00005000 BTC
[*] Progress: $4.221 CLP / $10.000 CLP (42.2%)
[*] Crypto received: 0.00005000 BTC
[*] Remaining: $5.779 CLP
[*] New optimal price: $84.429.581 CLP
[*] Order amount: 0.00006847 BTC ($5.779 CLP)
[+] New order placed! ID: 123457
```

### Resumen Final

```
[*] ==================================================
[*] EXECUTION SUMMARY
[*] ==================================================
[*] Target: $10.000 CLP
[+] Executed: $10.000 CLP
[+] Crypto received: 0.00011844 BTC
[*] Average price: $84.429.576 CLP
[*] ==================================================
```

## Estructura del Proyecto

```
bot-buda/
├── .env                    # Credenciales (no commitear)
├── .env.example            # Template de credenciales
├── requirements.txt        # Dependencias
└── src/
    ├── main.py             # CLI entry point
    ├── config.py           # Carga de configuración
    ├── auth.py             # Autenticación HMAC-SHA384
    ├── api.py              # Cliente API de Buda
    ├── bot.py              # Lógica de trading
    └── utils.py            # Funciones auxiliares
```

## Ejemplo de Uso

```bash
$ python3 -m src.main buy btc 10000

[*] Buda.com Trading Bot
[*] ========================================

[*] Starting BTC buy bot
[*] Target spend: $10.000 CLP
[*] Market: BTC-CLP
[*] Check interval: 30s

[*] Checking CLP balance...
[+] Available: $10.000 CLP

[*] Fetching order book...
[*] Best bid: $84.429.572 CLP
[*] Best ask: $84.939.358 CLP
[*] Spread: $509.786 CLP

[*] Optimal price: $84.429.573 CLP
[*] Order amount: 0.00011844 BTC
[*] Estimated total: $9.999 CLP

[*] Placing initial order...
[+] Order placed! ID: 123456

[*] Starting monitoring loop. Press Ctrl+C to stop.

[*] Checking position...
[+] Still best bid at $84.429.573 CLP
```

## Notas

- El monto siempre se especifica en CLP
- El bot calcula automáticamente cuánto crypto puede comprar
- **Monto mínimo BTC:** 2,000 CLP
- **Monto mínimo USDC:** 1,000 CLP
