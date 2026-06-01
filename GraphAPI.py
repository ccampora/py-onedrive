import time

import requests

from Authentication import get_bearer_auth_header, get_refresh_token, save_bearer_response
from Globals import LOGGER as logger

DEFAULT_TIMEOUT = 30       # seconds per request
MAX_RETRIES = 3            # max attempts for 5xx errors
MAX_RATE_LIMIT_RETRIES = 5 # max attempts when rate-limited (429)
INITIAL_BACKOFF = 1        # seconds; doubles after each 5xx retry


class GraphAPIError(Exception):
    """Raised when the Graph API returns a non-recoverable error response."""

    def __init__(self, status_code, message=""):
        self.status_code = status_code
        detail = f": {message}" if message else ""
        super().__init__(f"Graph API HTTP {status_code}{detail}")


def get(url, timeout=DEFAULT_TIMEOUT):
    """
    Make an authenticated GET request to the Microsoft Graph API.

    Retry behaviour:
      - 401  Refresh the access token once and retry. Raises on second 401.
      - 429  Sleep for Retry-After seconds (default 60) and retry, up to
             MAX_RATE_LIMIT_RETRIES times.
      - 5xx  Exponential back-off starting at INITIAL_BACKOFF seconds, up to
             MAX_RETRIES attempts.
      - Other non-200 responses are raised immediately (not retried).
      - Timeout / connection errors are raised immediately as GraphAPIError.

    Returns a requests.Response with status_code == 200.
    """
    token_refreshed = False
    rate_limit_retries = 0
    server_error_retries = 0
    backoff = INITIAL_BACKOFF

    while True:
        headers = get_bearer_auth_header()

        try:
            r = requests.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            raise GraphAPIError(0, f"Request timed out after {timeout}s: {url}")
        except requests.exceptions.ConnectionError as e:
            raise GraphAPIError(0, f"Connection error: {e}")

        if r.status_code == 200:
            return r

        if r.status_code == 401:
            if not token_refreshed:
                logger.info("401 Unauthorized — refreshing access token")
                _do_token_refresh()
                token_refreshed = True
                continue
            raise GraphAPIError(401, "Unauthorized after token refresh")

        if r.status_code == 429:
            if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES:
                raise GraphAPIError(
                    429,
                    f"Still rate-limited after {MAX_RATE_LIMIT_RETRIES} retries",
                )
            retry_after = int(r.headers.get("Retry-After", 60))
            logger.warning(
                f"Rate limited (429) — waiting {retry_after}s "
                f"(attempt {rate_limit_retries + 1}/{MAX_RATE_LIMIT_RETRIES})"
            )
            time.sleep(retry_after)
            rate_limit_retries += 1
            continue

        if 500 <= r.status_code < 600:
            if server_error_retries >= MAX_RETRIES:
                raise GraphAPIError(
                    r.status_code,
                    f"Server error after {MAX_RETRIES} retries",
                )
            logger.warning(
                f"Server error {r.status_code} — "
                f"retry {server_error_retries + 1}/{MAX_RETRIES} in {backoff}s"
            )
            time.sleep(backoff)
            backoff *= 2
            server_error_retries += 1
            continue

        # 4xx (not 401/429) and any other status — not retryable
        raise GraphAPIError(r.status_code, r.text[:500] if r.text else "")


def _do_token_refresh():
    """Refresh the OAuth2 access token and persist the new credentials."""
    try:
        response = get_refresh_token()
        save_bearer_response(response)
    except Exception as e:
        raise GraphAPIError(401, f"Token refresh failed: {e}")
