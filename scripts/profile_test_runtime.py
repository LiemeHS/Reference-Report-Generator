#!/usr/bin/env python3
"""One-time test profiling helper for stall triage.

This script runs pytest modules sequentially with per-file timeouts and captures
slowest-test timings. It is intended for debugging, not for normal CI gating.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass


OUTLIER_LINE_RE = re.compile(
    r"^\s*(?P<seconds>\d+(?:\.\d+)?)\s*s\s+(?:(?P<type>[a-z]+)\s+)?(?P<node>tests/.*)$"
)


@dataclass
class ModuleResult:
    name: str
    return_code: int | None
    timed_out: bool
    wall_time_sec: float
    test_durations: list[tuple[str, float]]
    raw_output: str
    outliers: list[tuple[str, float]]


def _run_pytest_module(
    module: str,
    *,
    timeout_seconds: int,
    maxfail: int,
    durations: int,
) -> ModuleResult:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        module,
        "--maxfail",
        str(maxfail),
        "--durations",
        str(durations),
        "-q",
    ]
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return ModuleResult(
            name=module,
            return_code=None,
            timed_out=True,
            wall_time_sec=time.perf_counter() - start,
            test_durations=[],
            raw_output=(
                f"{exc}\n\nSTDOUT:\n{(exc.stdout or '')}\n\nSTDERR:\n{(exc.stderr or '')}"
            ).strip(),
            outliers=[],
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = f"{stdout}\n{stderr}".rstrip()
    durations_data = _extract_durations(stdout)
    wall_time = time.perf_counter() - start
    outliers = _detect_outliers(durations_data, outlier_factor=3.0, outlier_min_seconds=1.0)
    return ModuleResult(
        name=module,
        return_code=completed.returncode,
        timed_out=False,
        wall_time_sec=wall_time,
        test_durations=durations_data,
        raw_output=combined,
        outliers=outliers,
    )


def _extract_durations(output: str) -> list[tuple[str, float]]:
    durations: list[tuple[str, float]] = []
    capture = False
    for line in output.splitlines():
        if line.strip().startswith("=== slowest"):
            capture = True
            continue
        if capture:
            if line.strip().startswith("="):
                break
            match = OUTLIER_LINE_RE.match(line)
            if not match:
                continue
            durations.append((match.group("node"), float(match.group("seconds"))))
    return durations


def _detect_outliers(
    durations: list[tuple[str, float]],
    *,
    outlier_factor: float,
    outlier_min_seconds: float,
) -> list[tuple[str, float]]:
    if len(durations) < 3:
        return []
    values = sorted(duration for _, duration in durations)
    p95_index = max(0, math.ceil(len(values) * 0.95) - 1)
    p95 = values[p95_index]
    threshold = max(outlier_min_seconds, p95 * outlier_factor)
    return [
        (node, secs)
        for node, secs in durations
        if secs >= threshold
    ]


def _print_result(result: ModuleResult, *, outlier_factor: float, outlier_min_seconds: float) -> None:
    if result.timed_out:
        print(f"[TIMEOUT] {result.name} in {result.wall_time_sec:.1f}s (>{result.wall_time_sec:.1f}s cap)")
        if result.raw_output:
            print("  Output tail:")
            for line in result.raw_output.splitlines()[-8:]:
                print(f"    {line}")
        return

    status = "FAIL" if result.return_code != 0 else "PASS"
    print(
        f"[{status}] {result.name} | "
        f"{result.wall_time_sec:.1f}s | "
        f"{len(result.test_durations)} slow-test samples"
    )
    if result.return_code not in (None, 0):
        print("  pytest output tail:")
        for line in result.raw_output.splitlines()[-12:]:
            print(f"    {line}")
    if result.outliers:
        print(f"  Outlier threshold > max({outlier_min_seconds:.1f}s, p95*{outlier_factor:.1f})")
        for node, secs in sorted(result.outliers, key=lambda item: item[1], reverse=True):
            print(f"  - {secs:.3f}s  {node}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "modules",
        nargs="*",
        default=[
            "tests/test_web_stack_preflight.py",
            "tests/test_phase7_fastapi_adapter.py",
            "tests/test_phase7_runner.py",
            "tests/test_report_serving.py",
            "tests/test_security_state.py",
            "tests/test_reference_parsing.py",
            "tests/test_reference_matching.py",
            "tests/test_report_generation.py",
        ],
        help="Pytest files or expressions to run.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Per-module timeout in seconds (hard stop on hang-like behavior).",
    )
    parser.add_argument(
        "--maxfail",
        type=int,
        default=1,
        help="Stop after this many failing tests per module.",
    )
    parser.add_argument(
        "--durations",
        type=int,
        default=20,
        help="Pytest --durations value (number of slowest tests to include).",
    )
    parser.add_argument(
        "--outlier-factor",
        type=float,
        default=3.0,
        help="Flag tests slower than this × 95th percentile as outliers.",
    )
    parser.add_argument(
        "--outlier-min",
        type=float,
        default=1.0,
        help="Absolute outlier floor in seconds.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    overall_ok = True
    results: list[ModuleResult] = []

    for module in args.modules:
        result = _run_pytest_module(
            module,
            timeout_seconds=args.timeout,
            maxfail=args.maxfail,
            durations=args.durations,
        )
        results.append(result)
        _print_result(
            result,
            outlier_factor=args.outlier_factor,
            outlier_min_seconds=args.outlier_min,
        )
        if result.timed_out or (result.return_code and result.return_code != 0):
            overall_ok = False

        print()

    timed_out = [res for res in results if res.timed_out]
    if timed_out:
        print("[ACTION] Module timeout(s) suggest a real hang; rerun that module with a live stack trace probe.")
        overall_ok = False

    if overall_ok:
        print("[OK] Profiling pass completed with no module-timeout or failures.")
        return 0

    print("[WARN] Profiling pass completed with timeouts/failures. Use failure output above for triage.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
