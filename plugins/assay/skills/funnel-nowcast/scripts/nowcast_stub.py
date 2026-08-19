#!/usr/bin/env python3
"""nowcast_stub.py — funnel-nowcast is outlined, not implemented, in this template.

There is no chain-ladder estimator behind this skill. Running this script (or
invoking `assay:funnel-nowcast`) refuses cleanly instead of doing one of the
two dishonest things available to it: silently doing nothing, or fitting a
development curve to this repo's synthetic fixture data and returning a
plausible-looking number that describes the random generator, not a business.

See ../SKILL.md for:
  - the method (development-factor / chain-ladder, r_hat = r_obs(t) / f_s(t))
  - why this specific skill is not a stub away from working
  - the exact dependencies (numpy, scipy), config (`nowcast:` block), and
    calibration work a real implementation would need

Exit code 9 is reserved across this template's four outlined skills to mean
"not implemented here" — distinct from the 2/3/4/5 codes the functional
skills use for ADC-missing, dependency-missing, identity-refused, and
allow-list-refused respectively (see `_shared/identity.py`, `_shared/spine.py`).
"""

from __future__ import annotations

import sys

NOT_IMPLEMENTED_EXIT = 9

MESSAGE = """\
funnel-nowcast is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
"""


def main() -> int:
    sys.stderr.write(MESSAGE)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    sys.exit(main())
