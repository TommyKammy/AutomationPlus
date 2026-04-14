#!/usr/bin/env python3

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: diagnostics_backend.py <command>")

    command = sys.argv[1]
    payload = {
        "source_command": command,
        "read_only": True,
        "policy_model": "passthrough",
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
