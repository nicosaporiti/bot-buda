"""Buda.com API client module."""

import json
import time
from typing import Any, Optional

import requests

from .auth import get_auth_headers
from .config import Config


class BudaAPIError(Exception):
    """Base exception for Buda API errors."""

    def __init__(self, message: str, status_code: int = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AuthenticationError(BudaAPIError):
    """Raised when authentication fails (401)."""
    pass


class RateLimitError(BudaAPIError):
    """Raised when rate limit is exceeded (429)."""
    pass


class InsufficientBalanceError(BudaAPIError):
    """Raised when balance is insufficient for the operation."""
    pass


class BudaClient:
    """Client for interacting with Buda.com API."""

    def __init__(self, config: Config):
        """
        Initialize the Buda API client.

        Args:
            config: Configuration instance with API credentials.
        """
        self.config = config
        self.base_url = Config.BASE_URL
        self.session = requests.Session()
        self._max_retries = 3
        self._retry_delay = 5  # seconds

    def _make_request(
        self,
        method: str,
        endpoint: str,
        body: Optional[dict] = None,
        authenticated: bool = True
    ) -> dict:
        """
        Make an HTTP request to the Buda API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint (e.g., /balances/clp).
            body: Request body as dictionary (optional).
            authenticated: Whether to include auth headers.

        Returns:
            Response JSON as dictionary.

        Raises:
            AuthenticationError: If authentication fails.
            RateLimitError: If rate limit is exceeded.
            BudaAPIError: For other API errors.
        """
        url = f"{self.base_url}{endpoint}"
        path = f"/api/v2{endpoint}"

        body_str = json.dumps(body) if body else None

        for attempt in range(self._max_retries):
            try:
                if authenticated:
                    headers = get_auth_headers(
                        self.config.api_key,
                        self.config.api_secret,
                        method,
                        path,
                        body_str
                    )
                else:
                    headers = {"Content-Type": "application/json"}

                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body_str,
                    timeout=30
                )

                # Handle rate limiting
                if response.status_code == 429:
                    if attempt < self._max_retries - 1:
                        retry_after = int(response.headers.get("Retry-After", self._retry_delay))
                        print(f"Rate limited. Waiting {retry_after}s before retry...")
                        time.sleep(retry_after)
                        continue
                    raise RateLimitError(
                        "Rate limit exceeded. Please try again later.",
                        status_code=429
                    )

                # Handle authentication errors
                if response.status_code == 401:
                    raise AuthenticationError(
                        "Authentication failed. Check your API key and secret.",
                        status_code=401,
                        response=response.json() if response.text else None
                    )

                # Handle other errors
                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("message", str(error_data))
                    except (json.JSONDecodeError, ValueError):
                        error_msg = response.text or f"HTTP {response.status_code}"

                    # Check for insufficient balance
                    if "insufficient" in error_msg.lower() or "balance" in error_msg.lower():
                        raise InsufficientBalanceError(
                            error_msg,
                            status_code=response.status_code,
                            response=error_data if 'error_data' in dir() else None
                        )

                    raise BudaAPIError(
                        f"API error: {error_msg}",
                        status_code=response.status_code,
                        response=error_data if 'error_data' in dir() else None
                    )

                # Return successful response
                if response.text:
                    return response.json()
                return {}

            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    print(f"Request timeout. Retrying in {self._retry_delay}s...")
                    time.sleep(self._retry_delay)
                    continue
                raise BudaAPIError("Request timeout after multiple retries.")

            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries - 1:
                    print(f"Connection error. Retrying in {self._retry_delay}s...")
                    time.sleep(self._retry_delay)
                    continue
                raise BudaAPIError(f"Connection error: {e}")

        raise BudaAPIError("Max retries exceeded.")

    def get_balance(self, currency: str) -> dict:
        """
        Get the balance for a specific currency.

        Args:
            currency: Currency code (e.g., 'clp', 'btc', 'usdc').

        Returns:
            Balance information including available and frozen amounts.
        """
        response = self._make_request("GET", f"/balances/{currency.lower()}")
        return response.get("balance", response)

    def get_balances(self) -> list[dict]:
        """
        Get balances for all currencies.

        Returns:
            List of balance dictionaries.
        """
        response = self._make_request("GET", "/balances")
        if isinstance(response, dict) and isinstance(response.get("balances"), list):
            return response["balances"]
        if isinstance(response, list):
            return response
        return []

    def get_order_book(self, market_id: str) -> dict:
        """
        Get the order book for a specific market.

        Args:
            market_id: Market identifier (e.g., 'btc-clp', 'usdc-clp').

        Returns:
            Order book with bids and asks.
        """
        response = self._make_request(
            "GET",
            f"/markets/{market_id.lower()}/order_book",
            authenticated=False
        )
        return response.get("order_book", response)

    def get_market(self, market_id: str) -> dict:
        """
        Get market information.

        Args:
            market_id: Market identifier (e.g., 'btc-clp', 'usdc-clp').

        Returns:
            Market information including minimum order size.
        """
        response = self._make_request(
            "GET",
            f"/markets/{market_id.lower()}",
            authenticated=False
        )
        return response.get("market", response)

    def create_limit_order(
        self,
        market_id: str,
        order_type: str,
        amount: str,
        limit_price: str
    ) -> dict:
        """
        Create a limit order.

        Args:
            market_id: Market identifier (e.g., 'btc-clp').
            order_type: Order type ('Bid' for buy, 'Ask' for sell).
            amount: Amount of base currency to trade.
            limit_price: Limit price for the order.

        Returns:
            Created order information.
        """
        body = {
            "order": {
                "type": order_type,
                "price_type": "limit",
                "amount": str(amount),
                "limit": str(limit_price)
            }
        }

        response = self._make_request(
            "POST",
            f"/markets/{market_id.lower()}/orders",
            body=body
        )
        return response.get("order", response)

    def get_order(self, order_id: str) -> dict:
        """
        Get information about a specific order.

        Args:
            order_id: The order ID.

        Returns:
            Order information including state and traded amount.
        """
        response = self._make_request("GET", f"/orders/{order_id}")
        return response.get("order", response)

    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an active order.

        Args:
            order_id: The order ID to cancel.

        Returns:
            Updated order information.
        """
        body = {"state": "canceling"}
        response = self._make_request("PUT", f"/orders/{order_id}", body=body)
        return response.get("order", response)

    def get_my_orders(self, market_id: str, state: str = None) -> list:
        """
        Get list of own orders for a market.

        Args:
            market_id: Market identifier (e.g., 'btc-clp').
            state: Filter by state (e.g., 'pending', 'traded', 'canceled').

        Returns:
            List of orders.
        """
        endpoint = f"/markets/{market_id.lower()}/orders"
        if state:
            endpoint += f"?state={state}"
        response = self._make_request("GET", endpoint)
        return response.get("orders", response)
