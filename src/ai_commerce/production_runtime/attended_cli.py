"""Bounded Human-attended CLI. No background mode or arbitrary tool argument."""
from __future__ import annotations

import json

from . import attended_worker as worker

argparse = __import__("argparse")
sys = __import__("sys")

WAIT_BRAIN = 0
HOLD = 20
UNKNOWN = 21


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error can reflect private argv values.
        raise worker.WorkerHold("CLI_ARGUMENTS_INVALID")


def main(argv=None):
    try:
        parser = _Parser(description="One Human-attended offline PULSE invocation")
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        for name in ("run", "inspect", "resume", "export"):
            cmd = commands.add_parser(name)
            cmd.add_argument("--human-attended", required=True, action="store_true")
            cmd.add_argument("--directive", required=True, dest="directive_path")
            cmd.add_argument("--source-sha", required=True)
            cmd.add_argument("--asset-root", required=True)
            cmd.add_argument("--asset", required=True, action="append", dest="assets")
            cmd.add_argument("--rights", action="append", default=[])
            cmd.add_argument("--private-dir", required=True)
            if name in {"inspect", "resume"}:
                cmd.add_argument("--expected-generation", type=int)
            if name == "resume":
                cmd.add_argument("--reconcile-stopped-generation", type=int)
            if name == "export":
                cmd.add_argument("--destination", required=True)
        args = vars(parser.parse_args(argv))
        command = args.pop("command")
        args["assets"], args["rights"] = tuple(args["assets"]), tuple(args["rights"])
        result = {"run": worker.run_once, "inspect": worker.inspect_once,
                  "resume": worker.resume_once, "export": worker.export_once}[command](**args)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return WAIT_BRAIN if result["state"] == "WAIT_BRAIN" else HOLD
    except Exception as exc:
        # Never reflect decoder, SQLite, prompt, file or arbitrary exception text.
        code = str(exc) if type(exc) is worker.WorkerHold else "ATTENDED_OFFLINE_HOLD"
        if not (0 < len(code) <= 80 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in code)):
            code = "ATTENDED_OFFLINE_HOLD"
        state = "UNKNOWN_EFFECT" if code == "UNKNOWN_EFFECT" else "HOLD"
        print(json.dumps({"state": state, "code": code}, separators=(",", ":")), file=sys.stderr)
        return UNKNOWN if state == "UNKNOWN_EFFECT" else HOLD


if __name__ == "__main__":
    raise SystemExit(main())
