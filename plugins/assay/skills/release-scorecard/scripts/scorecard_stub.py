#!/usr/bin/env python3
"""scorecard_stub.py — release-scorecard is outlined, not implemented, in this template.

There is no pre-registration validator, BSTS counterfactual, or
difference-in-differences estimator behind this skill. Running this script
(or invoking `assay:release-scorecard`) refuses cleanly rather than issuing a
causal verdict nothing here actually computed.

See ../SKILL.md for the method, the pre-registration format
(../templates/pre-registration.template.yaml + .schema.json — both complete
and useful groundwork on their own), and exactly what implementing the
estimators would require.

Exit code 9 is reserved across this template's four outlined skills to mean
"not implemented here".
"""

from __future__ import annotations

import sys

NOT_IMPLEMENTED_EXIT = 9

MESSAGE = """\
release-scorecard is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
"""


def main() -> int:
    sys.stderr.write(MESSAGE)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    sys.exit(main())
