import pytest
from unittest.mock import patch, MagicMock, call
import requests as requests_lib

from GraphAPI import get, GraphAPIError, MAX_RETRIES, MAX_RATE_LIMIT_RETRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status_code, body=b"", headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.content = body
    r.text = body.decode() if isinstance(body, bytes) else body
    r.headers = headers or {}
    return r


def _mock_responses(*responses):
    return patch("GraphAPI.requests.get", side_effect=list(responses))


# ---------------------------------------------------------------------------
# 200 — happy path
# ---------------------------------------------------------------------------

def test_get_200_returns_response():
    resp = _response(200, b"data")
    with _mock_responses(resp):
        with patch("GraphAPI.get_bearer_auth_header", return_value={}):
            r = get("https://graph.microsoft.com/v1.0/me/drive")
    assert r.status_code == 200
    assert r.content == b"data"


# ---------------------------------------------------------------------------
# 401 — token refresh
# ---------------------------------------------------------------------------

def test_get_401_refreshes_and_retries():
    first = _response(401)
    second = _response(200, b"ok")
    with (
        _mock_responses(first, second),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI._do_token_refresh") as mock_refresh,
    ):
        r = get("https://graph.microsoft.com/test")
    mock_refresh.assert_called_once()
    assert r.status_code == 200


def test_get_401_twice_raises_after_refresh():
    with (
        _mock_responses(_response(401), _response(401)),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI._do_token_refresh"),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 401


def test_get_token_refresh_failure_raises():
    with (
        _mock_responses(_response(401)),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI._do_token_refresh", side_effect=GraphAPIError(401, "refresh failed")),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 429 — rate limiting
# ---------------------------------------------------------------------------

def test_get_429_sleeps_retry_after_and_retries():
    rate_limited = _response(429, headers={"Retry-After": "5"})
    ok = _response(200, b"ok")
    with (
        _mock_responses(rate_limited, ok),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep") as mock_sleep,
    ):
        r = get("https://graph.microsoft.com/test")
    mock_sleep.assert_called_once_with(5)
    assert r.status_code == 200


def test_get_429_uses_default_retry_after_when_header_missing():
    rate_limited = _response(429, headers={})
    ok = _response(200, b"ok")
    with (
        _mock_responses(rate_limited, ok),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep") as mock_sleep,
    ):
        get("https://graph.microsoft.com/test")
    mock_sleep.assert_called_once_with(60)


def test_get_429_raises_after_max_rate_limit_retries():
    responses = [_response(429, headers={"Retry-After": "1"})] * (MAX_RATE_LIMIT_RETRIES + 1)
    with (
        _mock_responses(*responses),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep"),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# 5xx — server errors with exponential backoff
# ---------------------------------------------------------------------------

def test_get_500_retries_and_succeeds():
    server_err = _response(500)
    ok = _response(200, b"ok")
    with (
        _mock_responses(server_err, ok),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep") as mock_sleep,
    ):
        r = get("https://graph.microsoft.com/test")
    assert r.status_code == 200
    mock_sleep.assert_called_once()


def test_get_500_uses_exponential_backoff():
    responses = [_response(500)] * MAX_RETRIES + [_response(200)]
    with (
        _mock_responses(*responses),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep") as mock_sleep,
    ):
        get("https://graph.microsoft.com/test")
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    # Backoff should double: 1, 2, 4, ...
    for i in range(1, len(sleep_calls)):
        assert sleep_calls[i] == sleep_calls[i - 1] * 2


def test_get_500_raises_after_max_retries():
    responses = [_response(500)] * (MAX_RETRIES + 1)
    with (
        _mock_responses(*responses),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep"),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 500


def test_get_503_treated_same_as_500():
    responses = [_response(503)] * (MAX_RETRIES + 1)
    with (
        _mock_responses(*responses),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
        patch("GraphAPI.time.sleep"),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Non-retryable errors
# ---------------------------------------------------------------------------

def test_get_404_raises_immediately():
    with (
        _mock_responses(_response(404, b"not found")),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 404


def test_get_403_raises_immediately():
    with (
        _mock_responses(_response(403, b"forbidden")),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Network / timeout errors
# ---------------------------------------------------------------------------

def test_get_timeout_raises_graph_api_error():
    with (
        patch("GraphAPI.requests.get", side_effect=requests_lib.exceptions.Timeout),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 0


def test_get_connection_error_raises_graph_api_error():
    with (
        patch("GraphAPI.requests.get", side_effect=requests_lib.exceptions.ConnectionError("refused")),
        patch("GraphAPI.get_bearer_auth_header", return_value={}),
    ):
        with pytest.raises(GraphAPIError) as exc_info:
            get("https://graph.microsoft.com/test")
    assert exc_info.value.status_code == 0


# ---------------------------------------------------------------------------
# GraphAPIError message
# ---------------------------------------------------------------------------

def test_graph_api_error_includes_status_and_message():
    err = GraphAPIError(429, "too many requests")
    assert "429" in str(err)
    assert "too many requests" in str(err)


def test_graph_api_error_without_message():
    err = GraphAPIError(500)
    assert "500" in str(err)


# ---------------------------------------------------------------------------
# Operations integration — sync_metadata and download_file use GraphAPI
# ---------------------------------------------------------------------------

def test_sync_metadata_uses_graph_api(tmp_path):
    """sync_metadata should go through GraphAPI.get, not requests.get directly."""
    from unittest.mock import patch
    from MetadataIndex import MetadataIndex
    from Operations import sync_metadata

    db = tmp_path / "db"
    db.mkdir()
    inode_file = tmp_path / ".inodes"

    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(db)),
        patch("MetadataIndex.INODE_FILE", str(inode_file)),
        patch("Operations.graph_get") as mock_graph,
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
        patch("Operations.should_download_simple", return_value=True),
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"value": [], "@odata.deltaLink": "delta://v1"}
        mock_graph.return_value = mock_resp

        idx = MetadataIndex()
        sync_metadata(idx)

    mock_graph.assert_called_once()


def test_download_file_uses_graph_api():
    """download_file should go through GraphAPI.get."""
    from Operations import download_file

    with patch("Operations.graph_get") as mock_graph:
        mock_resp = MagicMock()
        mock_resp.content = b"file bytes"
        mock_graph.return_value = mock_resp

        result = download_file("DRIVE!abc")

    assert result == b"file bytes"
    mock_graph.assert_called_once()


def test_download_file_converts_graph_api_error_to_io_error():
    from Operations import download_file
    from GraphAPI import GraphAPIError

    with patch("Operations.graph_get", side_effect=GraphAPIError(503, "service unavailable")):
        with pytest.raises(IOError):
            download_file("DRIVE!abc")
