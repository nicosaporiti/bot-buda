"""Authentication module for Buda.com API using HMAC-SHA384."""

import base64
import hashlib
import hmac
import time
from typing import Optional


def generate_nonce() -> str:
    """
    Generate a unique nonce based on current timestamp in microseconds.

    Returns:
        String representation of timestamp in microseconds.
    """
    return str(int(time.time() * 1_000_000))


def generate_signature(
    api_secret: str,
    method: str,
    path: str,
    nonce: str,
    body: Optional[str] = None
) -> str:
    """
    Generate HMAC-SHA384 signature for Buda.com API request.

    The signature is computed over the message:
    "{METHOD} {PATH} {BASE64_BODY} {NONCE}"

    Args:
        api_secret: The API secret key.
        method: HTTP method (GET, POST, PUT, DELETE).
        path: API endpoint path (e.g., /api/v2/balances/clp).
        nonce: Unique nonce for the request.
        body: Request body as JSON string (optional).

    Returns:
        Base64-encoded HMAC-SHA384 signature.
    """
    # Construct the message to sign
    # Format: "METHOD PATH BASE64_BODY NONCE" (with body)
    # Format: "METHOD PATH NONCE" (without body)
    if body:
        encoded_body = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        message = f"{method} {path} {encoded_body} {nonce}"
    else:
        message = f"{method} {path} {nonce}"

    # Compute HMAC-SHA384
    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha384
    ).hexdigest()

    return signature


def get_auth_headers(
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    body: Optional[str] = None
) -> dict:
    """
    Generate complete authentication headers for Buda.com API request.

    Args:
        api_key: The API key.
        api_secret: The API secret key.
        method: HTTP method (GET, POST, PUT, DELETE).
        path: API endpoint path (e.g., /api/v2/balances/clp).
        body: Request body as JSON string (optional).

    Returns:
        Dictionary of headers to include in the request.
    """
    nonce = generate_nonce()
    signature = generate_signature(api_secret, method, path, nonce, body)

    headers = {
        "X-SBTC-APIKEY": api_key,
        "X-SBTC-NONCE": nonce,
        "X-SBTC-SIGNATURE": signature,
        "Content-Type": "application/json",
    }

    return headers
