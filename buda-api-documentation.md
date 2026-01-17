# Buda.com API Documentation

Documentación completa de la API de Buda.com, exchange de criptomonedas latinoamericano que opera en Chile, Colombia, Perú y Argentina.

---

## Tabla de Contenidos

1. [Información General](#1-información-general)
2. [Autenticación](#2-autenticación)
3. [Endpoints Públicos](#3-endpoints-públicos)
4. [Endpoints Privados](#4-endpoints-privados)
5. [Tipos de Órdenes](#5-tipos-de-órdenes)
6. [Rate Limits](#6-rate-limits)
7. [WebSocket API](#7-websocket-api)
8. [Códigos de Error](#8-códigos-de-error)
9. [Paginación](#9-paginación)

---

## 1. Información General

### Descripción

La API de Buda.com es un puente que facilita la comunicación entre sistemas externos y la plataforma. Ofrece tanto **REST API** como **WebSocket API** para uso individual o empresarial.

### URL Base

```
https://api.buda.com/api/v2
```

### Formato de Respuestas

Todas las respuestas están en formato JSON. Las respuestas exitosas incluyen el recurso solicitado, mientras que los errores incluyen un objeto con `code` y `message`.

```json
{
  "resource_name": {
    "field1": "value1",
    "field2": "value2"
  }
}
```

### Identificación de Mercados

Los mercados se identifican por pares de monedas: `<base_currency>-<quote_currency>`

Ejemplos:
- `btc-clp` (Bitcoin / Peso Chileno)
- `eth-btc` (Ethereum / Bitcoin)
- `btc-cop` (Bitcoin / Peso Colombiano)
- `btc-pen` (Bitcoin / Sol Peruano)
- `btc-ars` (Bitcoin / Peso Argentino)

---

## 2. Autenticación

### Sistema HMAC-SHA384

Los endpoints privados requieren autenticación mediante firma HMAC-SHA384.

### Headers Requeridos

| Header | Descripción |
|--------|-------------|
| `X-SBTC-APIKEY` | Tu API Key pública |
| `X-SBTC-NONCE` | Timestamp Unix en microsegundos (debe ser incremental) |
| `X-SBTC-SIGNATURE` | Firma HMAC-SHA384 del mensaje |

### Generación de la Firma

La firma se genera a partir de la siguiente cadena:

```
{HTTP_METHOD} {path} {base64_encoded_body} {nonce}
```

**Componentes:**
- `HTTP_METHOD`: GET, POST, PUT, DELETE
- `path`: Ruta del endpoint (ej: `/api/v2/balances/btc`)
- `base64_encoded_body`: Body de la petición codificado en Base64 (vacío si no hay body)
- `nonce`: El mismo valor del header `X-SBTC-NONCE`

### Ejemplo en Python

```python
import hmac
import hashlib
import base64
import time
import requests

API_KEY = "tu_api_key"
API_SECRET = "tu_api_secret"
BASE_URL = "https://api.buda.com/api/v2"

def generate_signature(method, path, body=""):
    nonce = str(int(time.time() * 1e6))

    if body:
        encoded_body = base64.b64encode(body.encode()).decode()
    else:
        encoded_body = ""

    message = f"{method} {path} {encoded_body} {nonce}"

    signature = hmac.new(
        API_SECRET.encode(),
        message.encode(),
        hashlib.sha384
    ).hexdigest()

    return nonce, signature

def make_request(method, path, body=None):
    nonce, signature = generate_signature(method, path, body or "")

    headers = {
        "X-SBTC-APIKEY": API_KEY,
        "X-SBTC-NONCE": nonce,
        "X-SBTC-SIGNATURE": signature,
        "Content-Type": "application/json"
    }

    url = BASE_URL + path

    if method == "GET":
        response = requests.get(url, headers=headers)
    elif method == "POST":
        response = requests.post(url, headers=headers, data=body)
    elif method == "PUT":
        response = requests.put(url, headers=headers, data=body)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers)

    return response.json()

# Ejemplo de uso
balances = make_request("GET", "/api/v2/balances")
print(balances)
```

---

## 3. Endpoints Públicos

Estos endpoints no requieren autenticación.

### 3.1 Markets - Listar Mercados

```
GET /markets
GET /markets/{market_id}
```

**Respuesta:**
```json
{
  "markets": [
    {
      "id": "btc-clp",
      "name": "btc-clp",
      "base_currency": "btc",
      "quote_currency": "clp",
      "minimum_order_amount": ["0.00001", "BTC"],
      "taker_fee": "0.008",
      "maker_fee": "0.004"
    }
  ]
}
```

### 3.2 Ticker - Estado del Mercado

```
GET /markets/{market_id}/ticker
```

Obtiene el estado actual del mercado: último precio, spread bid/ask, volumen y variación de precio.

**Respuesta:**
```json
{
  "ticker": {
    "market_id": "btc-clp",
    "last_price": ["45000000", "CLP"],
    "min_ask": ["45100000", "CLP"],
    "max_bid": ["44900000", "CLP"],
    "volume": ["15.5", "BTC"],
    "price_variation_24h": "0.025",
    "price_variation_7d": "0.08"
  }
}
```

### 3.3 All Tickers - Todos los Mercados

```
GET /tickers
```

Retorna el ticker de todos los mercados disponibles.

### 3.4 Order Book - Libro de Órdenes

```
GET /markets/{market_id}/order_book
```

**Respuesta:**
```json
{
  "order_book": {
    "asks": [
      ["45100000", "0.5"],
      ["45200000", "1.2"]
    ],
    "bids": [
      ["44900000", "0.8"],
      ["44800000", "2.0"]
    ]
  }
}
```

### 3.5 Trades - Historial de Transacciones

```
GET /markets/{market_id}/trades
```

**Parámetros opcionales:**
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `timestamp` | integer | Filtrar desde este timestamp |
| `limit` | integer | Número máximo de resultados (max: 100) |

**Respuesta:**
```json
{
  "trades": {
    "entries": [
      {
        "timestamp": "2024-01-15T10:30:00.000Z",
        "amount": ["0.5", "BTC"],
        "price": ["45000000", "CLP"],
        "direction": "buy"
      }
    ]
  }
}
```

### 3.6 Volume - Volumen Transado

```
GET /markets/{market_id}/volume
```

Retorna el volumen transado en períodos de 24 horas y 7 días, separado por asks/bids.

### 3.7 Quotations - Simular Órdenes

```
POST /markets/{market_id}/quotations
```

Simula la ejecución de una orden usando el estado actual del order book.

**Body:**
```json
{
  "type": "bid_given_size",
  "amount": "1.0",
  "limit": "50000000"
}
```

**Tipos de cotización:**

| Tipo | Descripción |
|------|-------------|
| `bid_given_size` | ¿Cuánta quote currency necesito para comprar X base currency? |
| `bid_given_spent_quote` | ¿Cuánta base currency obtengo gastando X quote currency? |
| `ask_given_size` | ¿Cuánta quote currency obtengo vendiendo X base currency? |
| `ask_given_earned_quote` | ¿Cuánta base currency necesito vender para obtener X quote currency? |

### 3.8 Fees - Comisiones

```
GET /currencies/{currency}/fees/deposit
GET /currencies/{currency}/fees/withdrawal
```

Obtiene los costos de depósito/retiro para una moneda.

---

## 4. Endpoints Privados

Requieren autenticación mediante headers HMAC.

### 4.1 Información de Usuario

```
GET /me
```

**Respuesta:**
```json
{
  "user": {
    "email": "usuario@email.com",
    "category": "verified",
    "display_name": "Usuario",
    "account_data": {
      "names": "Juan",
      "surnames": "Pérez",
      "nationality": "CL",
      "document_number": "12345678-9"
    },
    "monthly_volume": ["1000000", "CLP"],
    "pubsub_key": "key_for_websocket"
  }
}
```

### 4.2 Balances

```
GET /balances
GET /balances/{currency}
```

**Respuesta:**
```json
{
  "balances": [
    {
      "id": "btc",
      "amount": ["1.5", "BTC"],
      "available_amount": ["1.2", "BTC"],
      "frozen_amount": ["0.3", "BTC"],
      "pending_withdraw_amount": ["0.0", "BTC"]
    }
  ]
}
```

### 4.3 Órdenes

#### Listar Órdenes

```
GET /markets/{market_id}/orders
```

**Parámetros opcionales:**
| Parámetro | Descripción |
|-----------|-------------|
| `per` | Resultados por página (default: 20, max: 300) |
| `page` | Número de página |
| `state` | Filtrar por estado |

#### Crear Orden

```
POST /markets/{market_id}/orders
```

**Body para orden limit:**
```json
{
  "type": "Bid",
  "price_type": "limit",
  "limit": "45000000",
  "amount": "0.5",
  "order_type": "gtc",
  "client_id": "mi-orden-001"
}
```

**Body para orden market:**
```json
{
  "type": "Ask",
  "price_type": "market",
  "amount": "0.5"
}
```

**Campos:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| `type` | string | `Bid` (compra) o `Ask` (venta) |
| `price_type` | string | `limit` o `market` |
| `limit` | string | Precio límite (solo para limit orders) |
| `amount` | string | Cantidad en base currency |
| `order_type` | string | `gtc`, `ioc`, `fok`, `post_only`, `gtd` |
| `client_id` | string | ID único para idempotencia (opcional) |

**Respuesta:**
```json
{
  "order": {
    "id": 12345,
    "type": "Bid",
    "state": "received",
    "price_type": "limit",
    "order_type": "gtc",
    "limit": ["45000000", "CLP"],
    "amount": ["0.5", "BTC"],
    "original_amount": ["0.5", "BTC"],
    "traded_amount": ["0.0", "BTC"],
    "total_exchanged": ["0", "CLP"],
    "paid_fee": ["0", "CLP"],
    "fee_currency": "CLP",
    "client_id": "mi-orden-001",
    "created_at": "2024-01-15T10:30:00.000Z"
  }
}
```

#### Consultar Orden

```
GET /orders/{id}
GET /orders/by-client-id/{client_id}
```

#### Cancelar Orden

```
PUT /orders/{id}
PUT /orders/by-client-id/{client_id}
```

**Body:**
```json
{
  "state": "canceling"
}
```

#### Cancelar Todas las Órdenes

```
DELETE /orders
```

**Parámetros opcionales:**
| Parámetro | Descripción |
|-----------|-------------|
| `market` | Cancelar solo en este mercado |
| `type` | `Bid` o `Ask` |

#### Órdenes en Lote (Batch)

```
POST /orders
```

**Body:**
```json
{
  "diff": [
    {
      "mode": "place",
      "market_id": "btc-clp",
      "type": "Bid",
      "price_type": "limit",
      "limit": "45000000",
      "amount": "0.5"
    },
    {
      "mode": "cancel",
      "id": 12345
    }
  ]
}
```

### 4.4 Depósitos

#### Listar Depósitos

```
GET /currencies/{currency}/deposits
```

#### Crear Depósito Fiat

```
POST /currencies/{currency}/deposits
```

**Body:**
```json
{
  "amount": "100000",
  "simulate": false
}
```

#### Generar Dirección Crypto

```
POST /currencies/{currency}/receive_addresses
```

```
GET /currencies/{currency}/receive_addresses/{id}
```

### 4.5 Retiros

#### Listar Retiros

```
GET /currencies/{currency}/withdrawals
```

#### Crear Retiro

```
POST /currencies/{currency}/withdrawals
```

**Body para retiro fiat:**
```json
{
  "amount": "100000",
  "withdrawal_data": {
    "bank_code": "001",
    "account_number": "123456789",
    "account_type": "checking"
  },
  "amount_includes_fee": false,
  "simulate": false
}
```

**Body para retiro crypto:**
```json
{
  "amount": "0.5",
  "withdrawal_data": {
    "target_address": "bc1qxy..."
  },
  "recipient_data": {
    "name": "Nombre Destinatario"
  }
}
```

**Estados de depósitos/retiros:**
- `pending_confirmation`
- `confirmed`
- `rejected`
- `retained`

### 4.6 Lightning Network

#### Crear Invoice (Depósito)

```
POST /lightning_network_invoices
```

**Body:**
```json
{
  "amount_satoshis": 100000,
  "currency": "clp",
  "memo": "Pago por servicio",
  "expiry_seconds": 3600
}
```

#### Pagar Invoice (Retiro)

```
POST /reserves/ln-btc/withdrawals
```

**Body:**
```json
{
  "amount": "100000",
  "withdrawal_data": {
    "payment_request": "lnbc..."
  },
  "simulate": false
}
```

### 4.7 Remesas Internacionales (Cross Border Payments)

#### Crear Cotización de Remesa

```
POST /remittances
```

**Body:**
```json
{
  "origin_amount": "1000",
  "origin_currency": "clp",
  "destination_currency": "cop",
  "client_reference_id": "ref-001",
  "recipient_data": {
    "account_holder_name": "Juan Pérez",
    "account_holder_type": "individual",
    "bank_code": "001",
    "account_number": "123456789",
    "account_type": "savings",
    "document_type": "CC",
    "document_number": "12345678"
  }
}
```

> **Nota:** `origin_amount` y `destination_amount` son mutuamente excluyentes.

#### Aceptar Remesa

```
PUT /remittances/{id}
```

**Body:**
```json
{
  "state": "accepted"
}
```

#### Consultar Remesa

```
GET /remittances/{id}
GET /remittances
```

#### Listar Bancos Disponibles

```
GET /currencies/{currency}/banks
```

---

## 5. Tipos de Órdenes

### 5.1 Por Precio

| Tipo | Descripción |
|------|-------------|
| **Limit** | Se ejecuta al precio especificado o mejor |
| **Market** | Se ejecuta inmediatamente al mejor precio disponible |

### 5.2 Stop Orders

| Tipo | Descripción |
|------|-------------|
| **Stop-Market** | Orden market que se activa cuando el precio alcanza el `stop_price` |
| **Stop-Limit** | Orden limit que se activa cuando el precio alcanza el `stop_price` |

**Parámetros adicionales para Stop Orders:**
- `stop_price`: Precio de activación
- `stop_type`: `stop_loss` o `take_profit`

### 5.3 Subtipos de Órdenes Limit

| Subtipo | Nombre Completo | Descripción |
|---------|-----------------|-------------|
| `gtc` | Good-Till-Cancelled | Permanece activa hasta ser ejecutada o cancelada manualmente |
| `ioc` | Immediate-Or-Cancel | Ejecuta inmediatamente lo que sea posible y cancela el resto |
| `fok` | Fill-Or-Kill | Se ejecuta completamente o se cancela en su totalidad |
| `post_only` | Post-Only / Maker-Only | Solo se ejecuta si agrega liquidez al book (nunca taker) |
| `gtd` | Good-Till-Date | Permanece activa hasta una fecha específica |

### 5.4 Estados de Órdenes

| Estado | Descripción |
|--------|-------------|
| `received` | Orden recibida, pendiente de procesamiento |
| `pending` | En proceso de validación |
| `active` | Activa en el order book |
| `traded` | Completamente ejecutada |
| `canceled` | Cancelada sin ejecución |
| `canceled_and_traded` | Parcialmente ejecutada y luego cancelada |
| `unprepared` | Orden stop esperando activación |

---

## 6. Rate Limits

### 6.1 Límites Generales

| Tipo de Acceso | Límite |
|----------------|--------|
| Sin autenticación | 120 requests/minuto (por IP) |
| Con autenticación | 375 requests/minuto (por API Key) |

### 6.2 Límites de Trading (por mercado)

| Trading Tier | Volumen 30 días | Límite |
|--------------|-----------------|--------|
| Tier < 4 | < $100,000 USD | 100 requests/minuto |
| Tier ≥ 4 | ≥ $100,000 USD | 250 requests/minuto |

### 6.3 Límites Especiales

| Endpoint | Límite |
|----------|--------|
| Cross Border Payments Quote | 20,000 USD equivalente por minuto |
| WebSocket | 20 mensajes/segundo (recomendado) |

### 6.4 Manejo de Rate Limits

Cuando se excede el límite, la API retorna HTTP 429. Se recomienda:
- Implementar backoff exponencial
- Cachear respuestas cuando sea posible
- Usar WebSocket para datos en tiempo real

---

## 7. WebSocket API

### URL Base

```
wss://realtime.buda.com/sub?channel={channel}
```

### 7.1 Canales Públicos

#### Order Book

```
wss://realtime.buda.com/sub?channel=book%40btcclp
```

**Eventos:**
- `book-changed`: Cambio en el order book
- `book-sync`: Sincronización completa del book

#### Trades

```
wss://realtime.buda.com/sub?channel=trades%40btcclp
```

**Eventos:**
- `trade-created`: Nueva transacción ejecutada

### 7.2 Canales Privados

Requieren el `pubsub_key` obtenido del endpoint `/me`.

#### Balances

```
wss://realtime.buda.com/sub?channel=balances%40{pubsub_key}
```

**Eventos:**
- `balance-updated`: Cambio en balance

#### Órdenes

```
wss://realtime.buda.com/sub?channel=orders%40{pubsub_key}
```

**Eventos:**
- `order-created`: Nueva orden creada
- `order-updated`: Estado de orden actualizado

#### Depósitos

```
wss://realtime.buda.com/sub?channel=deposits%40{pubsub_key}
```

**Eventos:**
- `deposit-confirmed`: Depósito confirmado

### 7.3 Formato de Eventos

```json
{
  "ev": "trade-created",
  "ts": "1705312200.123456789",
  "mk": "BTC-CLP",
  "data": {
    "amount": "0.5",
    "price": "45000000",
    "direction": "buy"
  }
}
```

| Campo | Descripción |
|-------|-------------|
| `ev` | Nombre del evento |
| `ts` | Timestamp Unix en nanosegundos |
| `mk` | ID del mercado |
| `data` | Datos específicos del evento |

---

## 8. Códigos de Error

### 8.1 Códigos HTTP

| Código | Significado |
|--------|-------------|
| `200` | OK - Solicitud exitosa |
| `201` | Created - Recurso creado exitosamente |
| `400` | Bad Request - Sintaxis malformada o JSON inválido |
| `401` | Unauthorized - API Key inválida o firma incorrecta |
| `403` | Forbidden - Permisos insuficientes |
| `404` | Not Found - Recurso no existe |
| `405` | Method Not Allowed - Método HTTP incorrecto |
| `406` | Not Acceptable - Formato no soportado |
| `410` | Gone - Recurso ya no disponible |
| `422` | Unprocessable Entity - Payload con formato inválido |
| `429` | Too Many Requests - Rate limit excedido |
| `500` | Internal Server Error - Error del servidor |
| `503` | Service Unavailable - Mantenimiento |

### 8.2 Formato de Error

```json
{
  "code": "insufficient_balance",
  "message": "No tienes suficiente balance para realizar esta operación"
}
```

### 8.3 Códigos de Error Comunes

| Código | Descripción |
|--------|-------------|
| `invalid_signature` | La firma HMAC no es válida |
| `invalid_nonce` | El nonce no es incremental |
| `insufficient_balance` | Balance insuficiente |
| `order_not_found` | Orden no encontrada |
| `market_not_found` | Mercado no existe |
| `amount_too_small` | Monto menor al mínimo |
| `price_out_of_range` | Precio fuera del rango permitido |
| `rate_limit_exceeded` | Límite de requests excedido |

---

## 9. Paginación

Los endpoints que retornan listas soportan paginación.

### Parámetros

| Parámetro | Default | Máximo | Descripción |
|-----------|---------|--------|-------------|
| `per` | 20 | 300 | Resultados por página |
| `page` | 1 | - | Número de página |

### Respuesta con Metadata

```json
{
  "orders": [...],
  "meta": {
    "current_page": 1,
    "total_pages": 5,
    "total_count": 100
  }
}
```

### Ejemplo de Iteración

```python
def get_all_orders(market_id):
    all_orders = []
    page = 1

    while True:
        response = make_request(
            "GET",
            f"/api/v2/markets/{market_id}/orders?per=300&page={page}"
        )

        orders = response.get("orders", [])
        all_orders.extend(orders)

        meta = response.get("meta", {})
        if page >= meta.get("total_pages", 1):
            break

        page += 1

    return all_orders
```

---

## Referencias

- [Documentación Oficial API Buda.com](https://api.buda.com/en/)
- [Buda.com](https://www.buda.com/)
- [GitHub - buda-promise (wrapper Node.js)](https://github.com/ajunge/buda-promise)
- [GitHub - tulip (cliente Go)](https://github.com/igomez10/tulip)

---

*Última actualización: Enero 2026*
