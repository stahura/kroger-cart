"""
Requests session with retry and exponential backoff.
Handles transient errors (429, 500, 502, 503, 504) automatically.
"""

import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def create_session() -> requests.Session:
    """Create a requests session with retry logic.

    Retries up to 3 times with exponential backoff (1s, 2s, 4s)
    on rate-limit (429) and server error (5xx) responses.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "PUT", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    logger.debug("HTTP session created with retry (3 attempts, backoff=1s)")
    return session
