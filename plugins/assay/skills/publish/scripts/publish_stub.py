#!/usr/bin/env python3
"""publish_stub.py — publish is outlined, not implemented, in this template.

There is no WikiAdapter implementation behind this skill — no page fetch, no
page create, no page update, against any wiki product. Running this script
(or invoking `assay:publish`) refuses cleanly rather than pretending to
compose and preview a page it could never actually write.

See ../SKILL.md for the adapter interface a real implementation would need
(get_page / create_page / update_page), the approval-gate workflow, and
../wiki-registry.example.yaml for the genericized page-registry shape.

Exit code 9 is reserved across this template's four outlined skills to mean
"not implemented here".
"""

from __future__ import annotations

import sys

NOT_IMPLEMENTED_EXIT = 9

MESSAGE = """\
publish is not implemented in this template.
See SKILL.md in this directory for the adapter interface and exactly
what implementing it would require.
"""


def main() -> int:
    sys.stderr.write(MESSAGE)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    sys.exit(main())
