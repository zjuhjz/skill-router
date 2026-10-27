"""CLI equivalent of the three MCP tools, plus doctor and restore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillrouter.engine import describe_capability, doctor, invoke, list_capabilities, restore, warmup
from skillrouter.utf8 import force_utf8


def _print_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _build() -> argparse.ArgumentParser:
    # Help text is ASCII on purpose. PowerShell 5 decodes captured native
    # output with the OEM code page, so Chinese help becomes mojibake even
    # when the process writes UTF-8. JSON results stay UTF-8.
    parser = argparse.ArgumentParser(prog="skillrouter", description="skillcall router")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list", help="list capabilities and installed skills")
    list_cmd.add_argument("--json", action="store_true")

    describe = sub.add_parser("describe", help="show one capability schema")
    describe.add_argument("capability")

    call = sub.add_parser("invoke", help="invoke a capability")
    call.add_argument("capability")
    call.add_argument("--input", help="inline JSON object")
    call.add_argument("--input-file", dest="input_file", help="UTF-8 JSON file (avoids shell quoting)")
    call.add_argument("--url", help="shorthand for an input object {\"url\": VALUE}")
    call.add_argument("--save-to", dest="save_to")
    call.add_argument("--allow-paid", action="store_true")
    call.add_argument("--yes", action="store_true", help="trust skills that ask for confirmation")
    call.add_argument("--prefer", dest="prefer_skill")
    call.add_argument("--max-attempts", type=int)

    health = sub.add_parser("doctor", help="run public health-check URLs")
    health.add_argument("--report", help="write skillcall.health.v1 to this file")
    health.add_argument("--json", action="store_true")
    health.add_argument("--yes", action="store_true")
    health.add_argument("--skill")
    health.add_argument("--capability")
    health.add_argument("--platform")
    health.add_argument("--warmup", action="store_true", help="download models before health checks")

    warm = sub.add_parser("warmup", help="download skill models outside the invoke timeout")
    warm.add_argument("skill_id", nargs="?", help="one skill id; default is every skill that declares warmup")

    sub.add_parser("mcp", help="serve the MCP server on stdio")

    back = sub.add_parser("restore", help="put a deprecated skill back into ranking")
    back.add_argument("skill_id")
    back.add_argument("capability", nargs="?")
    back.add_argument("platform", nargs="?")
    return parser


def _list_text(payload: dict) -> str:
    lines = []
    for item in payload.get("capabilities") or []:
        lines.append(f"{item['id']}  {item.get('summary') or ''}")
        for platform in item.get("platforms") or []:
            lines.append(f"  {platform['id']}  health={platform.get('health')}")
            for skill in platform.get("skills") or []:
                needs = ",".join(skill.get("needs_env") or []) or "-"
                lines.append(
                    f"    {skill['id']} {skill.get('version')} {skill.get('cost')} "
                    f"{skill.get('health')} needs={needs}"
                )
        if not item.get("platforms"):
            lines.append("  （没有已安装的 Skill）")
    for error in payload.get("discovery_errors") or []:
        lines.append(f"! {error}")
    return "\n".join(lines) + ("\n" if lines else "没有已发布的能力\n")


def _doctor_text(report: dict) -> str:
    lines = [
        f"skillcall.health.v1  network={report.get('network', {}).get('kind')}",
    ]
    for check in report.get("checks") or []:
        flag = "ok" if check.get("ok") else (check.get("code") or "fail")
        lines.append(
            f"{flag:16} {check.get('skill')}  {check.get('capability')}  {check.get('platform')}  {check.get('latency_ms')}ms"
        )
    if not report.get("checks"):
        lines.append("没有可体检的 Skill")
    for error in report.get("discovery_errors") or []:
        lines.append(f"! {error}")
    return "\n".join(lines) + "\n"


def _load_invoke_input(args: argparse.Namespace) -> tuple[dict | None, str | None]:
    chosen = [name for name in ("input", "input_file", "url") if getattr(args, name)]
    if len(chosen) != 1:
        return None, "pass exactly one of --input, --input-file, or --url"
    if args.url:
        return {"url": args.url}, None
    if args.input_file:
        path = Path(args.input_file)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return None, f"cannot read input file: {exc}"
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"input file is not JSON: {exc}"
    else:
        try:
            raw = json.loads(args.input)
        except json.JSONDecodeError as exc:
            return None, f"input is not JSON: {exc}"
    if not isinstance(raw, dict):
        return None, "input must be a JSON object"
    return raw, None


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = _build().parse_args(argv)
    if args.cmd == "mcp":
        from skillrouter.mcp_server import serve

        serve()
        return 0
    if args.cmd == "list":
        payload = list_capabilities()
        if args.json:
            _print_json(payload)
        else:
            sys.stdout.write(_list_text(payload))
        return 0
    if args.cmd == "describe":
        payload = describe_capability(args.capability)
        _print_json(payload)
        return 0 if payload.get("ok") else 1
    if args.cmd == "restore":
        payload = restore(args.skill_id, args.capability, args.platform)
        _print_json(payload)
        return 0 if payload.get("ok") else 1
    if args.cmd == "warmup":
        payload = warmup(args.skill_id)
        _print_json(payload)
        return 0 if payload.get("ok") else 1
    if args.cmd == "doctor":
        if args.warmup:
            warm_report = warmup(args.skill)
        else:
            warm_report = None
        report = doctor(yes=args.yes, skill_id=args.skill, capability=args.capability, platform=args.platform)
        if warm_report is not None:
            report["warmup"] = warm_report.get("results")
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.json or args.report:
            if args.json or not args.report:
                _print_json(report)
            else:
                sys.stdout.write(_doctor_text(report))
        else:
            sys.stdout.write(_doctor_text(report))
        return 0
    raw, error = _load_invoke_input(args)
    if error:
        _print_json({"ok": False, "error": {"code": "INVALID_INPUT", "message": error}})
        return 1
    options = {}
    if args.save_to:
        options["save_to"] = args.save_to
    if args.allow_paid:
        options["allow_paid"] = True
    if args.prefer_skill:
        options["prefer_skill"] = args.prefer_skill
    if args.max_attempts is not None:
        options["max_attempts"] = args.max_attempts
    payload = invoke(args.capability, raw, options=options, yes=args.yes)
    _print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
