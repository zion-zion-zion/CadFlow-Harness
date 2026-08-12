"""Small immutable records shared by Agent Run boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolUseRecord:
    """A safe audit record that does not retain tool arguments or file data."""

    sequence: int
    tool_name: str
    target: str
    reference_names: tuple[str, ...] = ()
