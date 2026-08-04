"""Model B (NXL-88, ADR-7) subprocess entrypoint trampoline.

Invoked as: `<interpreter> _bootstrap.py <script_path> <entrypoint> <params_json_path>`

Deliberately standard-library only, zero imports of `nexolith` itself: the
configured interpreter may be a completely separate venv (e.g. one with
`pyspark` installed and no `nexolith` package at all) -- the whole reason
Model B scripts run as a subprocess in the first place. This file is the
fixed, Nexolith-authored code that gets executed; it dynamically imports
the user's own script by path (`importlib.util`, never `eval`/`exec` on a
string) and calls the documented entrypoint, exactly mirroring Model A's
`nexolith.jobs.loader` approach but kept import-free of the rest of the
package on purpose.
"""

import importlib.util
import json
import sys
import traceback
from types import SimpleNamespace


def main(argv: list[str]) -> int:
    script_path, entrypoint, params_path = argv[1], argv[2], argv[3]

    with open(params_path, encoding="utf-8") as handle:
        parameters = json.load(handle)

    spec = importlib.util.spec_from_file_location("nexolith_script_job", script_path)
    if spec is None or spec.loader is None:
        print(f"Could not load script: {script_path}", file=sys.stderr)
        return 1
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        print(f"Script raised while importing: {script_path}", file=sys.stderr)
        traceback.print_exc()
        return 1

    func = getattr(module, entrypoint, None)
    if func is None or not callable(func):
        print(f"No callable entrypoint '{entrypoint}' in {script_path}", file=sys.stderr)
        return 1

    context = SimpleNamespace(parameters=parameters)
    try:
        func(context)
    except Exception:
        print(f"Script entrypoint '{entrypoint}' raised in {script_path}", file=sys.stderr)
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
