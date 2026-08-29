"""Result type for typed outcomes at recoverable failure boundaries.

The functional core returns ``Result[ValueT, ErrorT]`` instead of raising;
the imperative shell catches exceptions at IO boundaries and converts them
into the relevant error variant.

See AGENTS.md "Result type" for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ok[ValueT]:
    value: ValueT


@dataclass(frozen=True, slots=True)
class Err[ErrorT]:
    error: ErrorT


type Result[ValueT, ErrorT] = Ok[ValueT] | Err[ErrorT]
