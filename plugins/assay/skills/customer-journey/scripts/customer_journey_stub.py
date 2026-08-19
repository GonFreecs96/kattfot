#!/usr/bin/env python3
"""customer_journey_stub.py — customer-journey is outlined, not implemented, in this template.

There is no customer-grain rollup for this skill to query: this template
commits a session-grain (cart) spine and a weekly aggregate, neither of which
is a customer-grain fixture, and no customer spine is registered in
`config/modules.yaml`. Running this script (or invoking
`assay:customer-journey`) refuses cleanly rather than approximating a
customer rollup with a `GROUP BY` over the cart spine, which would silently
mishandle guest checkout and per-stage maturity — see ../SKILL.md, "Why this
needs its own rollup, not a GROUP BY".

Exit code 9 is reserved across this template's four outlined skills to mean
"not implemented here".
"""

from __future__ import annotations

import sys

NOT_IMPLEMENTED_EXIT = 9

MESSAGE = """\
customer-journey is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
"""


def main() -> int:
    sys.stderr.write(MESSAGE)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    sys.exit(main())
