"""WebSocket client and state for Buda.com realtime channels."""

import json
import ssl
import threading
import time
from decimal import Decimal
from typing import Dict, Optional, Tuple

import certifi
import websocket


class OrderBookState:
    """In-memory order book state with top-of-book access."""

    def __init__(self) -> None:
        self._bids: Dict[Decimal, Decimal] = {}
        self._asks: Dict[Decimal, Decimal] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._top_changed = threading.Event()
        self._last_snapshot_ts = 0.0
        self._last_update_ts = 0.0

    def apply_snapshot(self, bids, asks) -> None:
        """Replace the entire book."""
        with self._lock:
            self._bids = self._parse_side(bids)
            self._asks = self._parse_side(asks)
            now = time.time()
            self._last_snapshot_ts = now
            self._last_update_ts = now
            self._ready.set()
            self._top_changed.set()

    def apply_change(self, side: str, price: str, amount: str) -> None:
        """Apply a single price level change."""
        price_dec = Decimal(str(price))
        amount_dec = Decimal(str(amount))
        with self._lock:
            book = self._bids if side == "bid" else self._asks
            if amount_dec <= 0:
                book.pop(price_dec, None)
            else:
                book[price_dec] = amount_dec
            now = time.time()
            self._last_update_ts = now
            if not self._ready.is_set():
                self._ready.set()
            self._top_changed.set()

    def get_best(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Return (best_bid, best_ask) or None if not ready."""
        if not self._ready.is_set():
            return None
        with self._lock:
            if not self._bids or not self._asks:
                return None
            best_bid = max(self._bids.keys())
            best_ask = min(self._asks.keys())
            return best_bid, best_ask

    def reset(self) -> None:
        """Clear book and readiness state."""
        with self._lock:
            self._bids.clear()
            self._asks.clear()
            self._last_snapshot_ts = 0.0
            self._last_update_ts = 0.0
            self._ready.clear()

    def wait_ready(self, timeout: float) -> bool:
        """Wait for initial snapshot."""
        return self._ready.wait(timeout)

    def wait_for_top_change(self, timeout: float) -> bool:
        """Wait for a top-of-book change or timeout."""
        changed = self._top_changed.wait(timeout)
        if changed:
            self._top_changed.clear()
        return changed

    def is_stale(self, max_age: float) -> bool:
        """Return True if the book hasn't updated recently."""
        if not self._ready.is_set():
            return True
        return (time.time() - self._last_update_ts) > max_age

    def age_seconds(self) -> float:
        """Return seconds since last update."""
        if not self._ready.is_set():
            return float("inf")
        return time.time() - self._last_update_ts

    @staticmethod
    def _parse_side(side):
        levels: Dict[Decimal, Decimal] = {}
        for entry in side or []:
            price, amount = entry[0], entry[1]
            price_dec = Decimal(str(price))
            amount_dec = Decimal(str(amount))
            if amount_dec > 0:
                levels[price_dec] = amount_dec
        return levels


class OrderState:
    """In-memory order state updated from realtime events."""

    def __init__(self) -> None:
        self._orders: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def update_from_event(self, order: dict) -> None:
        order_id = order.get("id")
        if not order_id:
            return
        with self._lock:
            self._orders[order_id] = order

    def get_order(self, order_id: str) -> Optional[dict]:
        with self._lock:
            return self._orders.get(order_id)


class RealtimeClient:
    """Manage realtime connections for book and orders."""

    def __init__(
        self,
        market_id: str,
        pubsub_key: Optional[str] = None,
        debug: bool = False,
        debug_limit: int = 5,
    ) -> None:
        self.market_id = market_id.lower().replace("-", "")
        self.pubsub_key = pubsub_key
        self.book_state = OrderBookState()
        self.order_state = OrderState()
        self._stop = threading.Event()
        self._ws_lock = threading.Lock()
        self._ws_apps: list[websocket.WebSocketApp] = []
        self._threads: list[threading.Thread] = []
        self._debug = debug
        self._debug_limit = debug_limit
        self._debug_counts = {"book": 0, "orders": 0}
        self._debug_lock = threading.Lock()

    def start(self) -> None:
        """Start realtime listeners."""
        self._stop.clear()
        self._threads.append(
            self._start_channel(self._book_url(), self._on_book, self._on_book_open)
        )
        if self.pubsub_key:
            self._threads.append(self._start_channel(self._orders_url(), self._on_orders))

    def stop(self) -> None:
        """Stop realtime listeners."""
        self._stop.set()
        with self._ws_lock:
            for ws_app in list(self._ws_apps):
                ws_app.close()

    def _start_channel(self, url: str, handler, on_open=None) -> threading.Thread:
        thread = threading.Thread(
            target=self._run_channel,
            args=(url, handler, on_open),
            daemon=True
        )
        thread.start()
        return thread

    def _run_channel(self, url: str, handler, on_open) -> None:
        backoff = 1
        while not self._stop.is_set():
            print(f"WebSocket connecting: {url}")
            ws_app = websocket.WebSocketApp(
                url,
                on_message=handler,
                on_error=self._on_error,
                on_open=on_open,
            )
            with self._ws_lock:
                self._ws_apps.append(ws_app)
            ws_app.run_forever(
                ping_interval=30,
                ping_timeout=10,
                sslopt={
                    "cert_reqs": ssl.CERT_REQUIRED,
                    "ca_certs": certifi.where(),
                },
            )
            with self._ws_lock:
                if ws_app in self._ws_apps:
                    self._ws_apps.remove(ws_app)
            if self._stop.is_set():
                break
            print(f"WebSocket disconnected: {url}. Reconnecting in {backoff}s.")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _book_url(self) -> str:
        return f"wss://realtime.buda.com/sub?channel=book%40{self.market_id}"

    def _orders_url(self) -> str:
        return f"wss://realtime.buda.com/sub?channel=orders%40{self.pubsub_key}"

    def _on_error(self, _ws, error) -> None:
        print(f"WebSocket error: {error}")

    def _on_book(self, _ws, message: str) -> None:
        self._debug_message("book", message)
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        event = payload.get("ev")
        data = payload.get("data", {})

        if event == "book-sync":
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            self.book_state.apply_snapshot(bids, asks)
        elif event == "book-changed":
            # Accept change array format: ["bids","price","amount"]
            if "change" in payload and isinstance(payload["change"], list):
                change = payload["change"]
                if len(change) >= 3:
                    side_raw, price, amount = change[0], change[1], change[2]
                    side = "bid" if side_raw == "bids" else "ask" if side_raw == "asks" else None
                    if side:
                        self.book_state.apply_change(side, price, amount)
                return

            # Accept both single-level and multi-level changes in data.
            if "bids" in data or "asks" in data:
                for entry in data.get("bids", []):
                    if len(entry) >= 2:
                        self.book_state.apply_change("bid", entry[0], entry[1])
                for entry in data.get("asks", []):
                    if len(entry) >= 2:
                        self.book_state.apply_change("ask", entry[0], entry[1])
            else:
                side = data.get("side")
                price = data.get("price")
                amount = data.get("amount")
                if side in ("bid", "ask") and price is not None and amount is not None:
                    self.book_state.apply_change(side, price, amount)

    def _on_book_open(self, _ws) -> None:
        # Force a fresh snapshot after reconnect.
        self.book_state.reset()

    def _on_orders(self, _ws, message: str) -> None:
        self._debug_message("orders", message)
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        event = payload.get("ev")
        data = payload.get("data", {})
        if event in ("order-created", "order-updated"):
            if isinstance(data, dict) and "order" in data:
                self.order_state.update_from_event(data["order"])
            elif isinstance(data, dict):
                self.order_state.update_from_event(data)

    def _debug_message(self, channel: str, message: str) -> None:
        if not self._debug:
            return
        with self._debug_lock:
            if self._debug_counts.get(channel, 0) >= self._debug_limit:
                return
            self._debug_counts[channel] = self._debug_counts.get(channel, 0) + 1
        print(f"WS {channel} raw: {message}")
