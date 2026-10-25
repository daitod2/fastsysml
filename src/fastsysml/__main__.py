"""Command line: python -m fastsysml {install,info,parse,browse} ..."""

from __future__ import annotations

import argparse
import shutil
import sys

from . import SysMLParser, _runtime


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fastsysml")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="install the runtime files (pilot implementation jar and library)")
    install.add_argument("--from", dest="package", metavar="FILE",
                         help=f"a local copy of {_runtime.PACKAGE_NAME} (default: download it from conda-forge)")
    commands.add_parser("info", help="show where the runtime files are and which java is used")
    parse = commands.add_parser("parse", help="parse files and print a summary of the models and their issues")
    parse.add_argument("files", nargs="+")
    parse.add_argument("--resolve", choices=("none", "lazy", "all"), default="none")
    parse.add_argument("--validate", action="store_true")
    parse.add_argument("--mode", choices=("patched", "userside", "plain"), default="patched")
    browse = commands.add_parser("browse", help="browse files in a web browser (a local server)")
    browse.add_argument("files", nargs="+")
    browse.add_argument("--port", type=int, default=8765, help="the port to listen on, on localhost (0: any free one)")
    browse.add_argument("--no-open", dest="open", action="store_false", help="do not open a web browser")
    browse.add_argument("--no-validate", dest="validate", action="store_false", help="do not run the validation")
    browse.add_argument("--mode", choices=("patched", "userside", "plain"), default="patched")
    args = parser.parse_args(argv)

    if args.command == "install":
        print(_runtime.install(args.package))
    elif args.command == "info":
        directory = _runtime.cache_dir()
        print(f"pilot implementation: {_runtime.PILOT_VERSION}")
        print(f"runtime directory:    {directory} ({'installed' if _runtime.is_installed() else 'not installed'})")
        print(f"java:                 {shutil.which('java')}")
    elif args.command == "parse":
        with SysMLParser(mode=args.mode) as sysml:
            for model in sysml.parse_files(args.files, resolve=args.resolve, validate=args.validate):
                print(f"{model.uri}: {len(model)} elements, {len(model.errors)} errors, "
                      f"{len(model.issues) - len(model.errors)} other issues")
                for issue in model.issues:
                    print(f"  {issue.severity} {issue.line}:{issue.column} {issue.message}")
    elif args.command == "browse":
        from .browser import Workspace
        from .browser.server import serve
        with Workspace(mode=args.mode) as workspace:
            print(f"Loading {len(args.files)} file(s)...", flush=True)
            workspace.load(args.files, validate=args.validate)
            serve(workspace, args.port, args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
