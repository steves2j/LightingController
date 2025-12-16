"""Runtime patches for known upstream issues."""

from __future__ import annotations

import logging

from aiohttp import tcp_helpers

_LOGGER = logging.getLogger(__name__)


def ensure_safe_tcp_keepalive() -> None:
    """Wrap aiohttp TCP keepalive to swallow unsupported socket errors."""
    if getattr(tcp_helpers, "_s2j_led_driver_patched", False):
        return

    original = tcp_helpers.tcp_keepalive

    def _safe_tcp_keepalive(transport):
        try:
            return original(transport)
        except OSError as err:  # pragma: no cover - defensive patch
            _LOGGER.debug("Ignoring tcp_keepalive error: %s", err)

    tcp_helpers.tcp_keepalive = _safe_tcp_keepalive  # type: ignore[assignment]
    setattr(tcp_helpers, "_s2j_led_driver_patched", True)
