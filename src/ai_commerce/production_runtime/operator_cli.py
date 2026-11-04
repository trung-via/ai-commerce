"""Human-readable, bounded CLI; no authenticated Brain or provider ingress."""
from __future__ import annotations

import json

from . import operator as op
from .attended_cli import WAIT_BRAIN, HOLD, UNKNOWN

argparse = __import__("argparse")

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise op.w.WorkerHold("CLI_ARGUMENTS_INVALID")


def main(argv=None):
    try:
        parser = _Parser(description="One attended offline operator invocation; private local SESSION only after setup")
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        setup = commands.add_parser("setup")
        for name in ("operator-dir", "private-dir", "export-dir", "directive", "source-sha", "asset-root"):
            setup.add_argument("--" + name, required=True, dest="directive_path" if name == "directive" else name.replace("-", "_"))
        setup.add_argument("--asset", required=True, action="append", dest="assets")
        setup.add_argument("--rights", action="append", default=[])
        for name in ("run", "status", "reconcile", "handoff", "metrics", "learning-template"):
            cmd = commands.add_parser(name)
            cmd.add_argument("--session", required=True, dest="session_path")
            if name in {"run", "reconcile"}:
                cmd.add_argument("--human-attended", action="store_true")
            if name == "reconcile":
                cmd.add_argument("--stopped-generation", type=int)
        args = vars(parser.parse_args(argv))
        command = args.pop("command")
        if command == "setup":
            result = op.setup(**args)
        else:
            result = op.invoke(command, **args)
    except Exception as exc:
        code = "CLI_ARGUMENTS_INVALID" if type(exc) is op.w.WorkerHold and str(exc) == "CLI_ARGUMENTS_INVALID" else op.error_code(exc)
        result = op.response(code)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return UNKNOWN if result["state"] == "UNKNOWN" else HOLD if result["state"] == "HOLD" else WAIT_BRAIN


if __name__ == "__main__":
    raise SystemExit(main())
