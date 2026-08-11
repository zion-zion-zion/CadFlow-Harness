from __future__ import annotations

import os


def process_exists(process_id: int) -> bool:
    """Return whether a local process handle is still alive."""

    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
