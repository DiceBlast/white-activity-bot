"""Pure, read-only Discord gateway diagnostics for the desktop monitor."""
import math
import time


def public_connection_status(connection, ready, latency_seconds, now=None):
    now = time.time() if now is None else float(now)
    state = 'connected' if ready else connection.get('state', 'starting')
    if state == 'connected' and not ready:
        state = 'reconnecting'
    if state not in ('connected', 'reconnecting', 'disconnected', 'starting'):
        state = 'starting'
    try:
        latency_ms = float(latency_seconds) * 1000.0
        if not math.isfinite(latency_ms) or latency_ms < 0 or not ready:
            latency_ms = None
        else:
            latency_ms = round(latency_ms)
    except (TypeError, ValueError):
        latency_ms = None
    return {
        'state': state, 'latency_ms': latency_ms,
        'reconnects': max(0, int(connection.get('reconnects', 0))),
        'last_ready': connection.get('last_ready'),
        'last_disconnect': connection.get('last_disconnect'),
        'last_error': connection.get('last_error'),
        'timestamp': now,
    }


def display_status(payload, snapshot_time, process_running, now=None, ttl=35):
    """Never claim connected from a stale file or when the process is stopped."""
    now = time.time() if now is None else float(now)
    if not process_running:
        return 'Stopped', None, 0
    if not isinstance(payload, dict) or snapshot_time is None:
        return 'Unknown (waiting for gateway data)', None, 0
    try:
        if not (0 <= now - float(snapshot_time) <= ttl):
            return 'Unknown (stale gateway data)', None, 0
    except (TypeError, ValueError):
        return 'Unknown (waiting for gateway data)', None, 0
    label = {'connected': 'Connected', 'reconnecting': 'Reconnecting',
             'disconnected': 'Disconnected', 'starting': 'Connecting'}.get(
                 payload.get('state'), 'Unknown')
    return label, payload.get('latency_ms'), payload.get('reconnects', 0)
