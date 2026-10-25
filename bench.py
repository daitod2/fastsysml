"""Benchmark of the Python binding (run with src on PYTHONPATH):

    python bench.py parse              # parse only: all files of sysml-v2-release (one per call, 2 passes)
    python bench.py resolve            # SimpleVehicleModel.sysml: patched/plain x lazy/all, results compared
    python bench.py compare            # all files of sysml/src and kerml/src, one per call: lazy vs all
"""

import argparse
import os
import sys
import time
from pathlib import Path

from fastsysml import External, SysMLParser, Unresolved

# A clone of https://github.com/Systems-Modeling/SysML-v2-Release (SYSML_V2_RELEASE, or next to this repository).
RELEASE = Path(os.environ.get("SYSML_V2_RELEASE", Path(__file__).resolve().parents[1] / "sysml-v2-release"))
VEHICLE = RELEASE / "sysml/src/examples/Vehicle Example/SysML v2 Spec Annex A SimpleVehicleModel.sysml"


def corpus():
    files = []
    for folder in ("sysml/src", "kerml/src", "sysml.library"):
        files += sorted(p for p in (RELEASE / folder).rglob("*") if p.suffix in (".sysml", ".kerml"))
    return files


def bench_parse():
    files = corpus()
    lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    t = time.perf_counter()
    parser = SysMLParser()
    print(f"startup: {time.perf_counter() - t:.2f} s ({parser.startup})")
    for run in (1, 2):
        java_parse = java_export = 0.0
        elements = errors = 0
        t = time.perf_counter()
        for path in files:
            model = parser.parse_file(path)
            java_parse += model.timing["parse"]
            java_export += model.timing["export"]
            elements += len(model)
            errors += len(model.errors)
        wall = time.perf_counter() - t
        print(f"pass {run}: {len(files)} files, {lines} lines, {elements} elements, {errors} syntax errors: "
              f"{wall:.2f} s total ({len(files) / wall:.0f} files/s, {lines / wall:.0f} lines/s); "
              f"in Java: parse {java_parse / 1000:.2f} s, export {java_export / 1000:.2f} s")
    parser.close()


def normalize(model):
    """References and issues of a model, independent of element ids and object identity."""
    def target(value):
        if isinstance(value, External):
            return ("external", value.qualified_name or value.uri.rsplit("sysml.library", 1)[-1])
        if isinstance(value, Unresolved):
            return ("unresolved", value.text)
        return ("local", value.id)
    refs = [(e.id, name, tuple(target(v) for v in values)) for e in model for name, values in e.refs.items()]
    issues = sorted((i.severity, i.line, i.column, i.message) for i in model.issues)
    return refs, issues


def bench_resolve():
    results = {}
    for mode in ("patched", "plain"):
        t = time.perf_counter()
        with SysMLParser(mode=mode) as parser:
            startup = time.perf_counter() - t
            for resolve in ("lazy", "lazy", "all"):
                t = time.perf_counter()
                model = parser.parse_file(VEHICLE, resolve=resolve, validate=True)
                wall = time.perf_counter() - t
                timing = {k: round(v) for k, v in model.timing.items()}
                print(f"{mode:8} resolve={resolve:4}: {wall:6.2f} s (Java ms: {timing}), "
                      f"{len(model)} elements, {len(model.errors)} errors")
                results[(mode, resolve)] = normalize(model)
            print(f"{mode:8} startup {startup:.2f} s")
    reference = results[("plain", "all")]
    for key, value in results.items():
        print(f"{key}: {'same references and issues as plain/all' if value == reference else 'DIFFERENT'}")


def bench_compare():
    files = [p for p in corpus() if "sysml.library" not in p.parts]
    with SysMLParser() as parser:
        parser.load_library()
        totals = {}
        different = []
        results = {}
        for resolve in ("lazy", "all"):
            t = time.perf_counter()
            for path in files:
                results[(resolve, path)] = normalize(parser.parse_file(path, resolve=resolve, validate=True))
            totals[resolve] = time.perf_counter() - t
        for path in files:
            if results[("lazy", path)] != results[("all", path)]:
                different.append(path.relative_to(RELEASE))
        errors = sum(len(results[("lazy", p)][1]) for p in files)
        print(f"{len(files)} files (resolve + validate, one per call): lazy {totals['lazy']:.1f} s, "
              f"all {totals['all']:.1f} s; issues: {errors}")
        print(f"files whose references or issues differ between lazy and all: {len(different)}")
        for path in different:
            print("  ", path)


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("what", choices=("parse", "resolve", "compare"))
    what = arguments.parse_args().what
    {"parse": bench_parse, "resolve": bench_resolve, "compare": bench_compare}[what]()


if __name__ == "__main__":
    sys.exit(main())
