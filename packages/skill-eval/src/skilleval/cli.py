from __future__ import annotations

import argparse
from pathlib import Path

from .comparison import ComparisonError, load_reports, render_comparison
from .reporters import REPORTERS, render
from .runner import EvaluationError, run_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run an evaluation suite")
    run.add_argument("implementation", type=Path)
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--output", type=Path, help="primary report output; defaults to stdout")
    run.add_argument("--format", choices=sorted(REPORTERS), default="json")
    run.add_argument("--json", type=Path, dest="json_output")
    run.add_argument("--markdown", type=Path, dest="markdown_output")
    run.add_argument("--junit", type=Path, dest="junit_output")
    run.add_argument("--repeat", type=int)
    run.add_argument("--mode", choices=["offline", "live"])
    compare = subparsers.add_parser("compare", help="render a Markdown comparison from JSON reports")
    compare.add_argument("reports", nargs="+", type=Path)
    compare.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "compare":
        try:
            rendered = render_comparison(load_reports(args.reports))
        except ComparisonError as exc:
            parser.error(str(exc))
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    try:
        report = run_evaluation(
            args.implementation,
            args.suite,
            repeat=args.repeat,
            expected_mode=args.mode,
        )
    except (EvaluationError, OSError) as exc:
        parser.error(str(exc))
    rendered = render(report, args.format)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    explicit_outputs = {
        "json": args.json_output,
        "markdown": args.markdown_output,
        "junit": args.junit_output,
    }
    for format_name, path in explicit_outputs.items():
        if path is not None:
            path.write_text(render(report, format_name), encoding="utf-8")
    if args.output is None and not any(explicit_outputs.values()):
        print(rendered, end="")
    return 0 if report["results"]["task_success_rate"] == 1 else 1
