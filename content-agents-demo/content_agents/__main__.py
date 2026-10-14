"""Command line entry point.

  python -m content_agents serve                 # dashboard on http://localhost:8765
  python -m content_agents run --auto "topic"    # whole pipeline in the terminal
"""

from __future__ import annotations

import argparse
import time

from .config import Settings
from .pipeline import Pipeline

DEFAULT_TOPICS = [
    "AI tools that save small businesses time on marketing",
    "Why most Instagram Reels flop in the first 3 seconds",
]
WAITING = {"awaiting_angle_approval", "awaiting_footage", "awaiting_final_approval"}


def cmd_run(args: argparse.Namespace) -> None:
    settings = Settings()
    pipeline = Pipeline(settings)
    pipeline.start_watcher()
    run = pipeline.start(args.topics or DEFAULT_TOPICS, auto_approve=args.auto)
    print(f"Run {run.id} · LLM: {pipeline.llm.mode} · workspace: {settings.workspace}\n")
    seen, simulated = 0, False
    while True:
        for ev in pipeline.store.events(run.id, seen):
            seen = ev["id"]
            print(f"  [{ev['agent']:>13}] {ev['message']}")
        run = pipeline.store.get_run(run.id)
        if run.status in {"published", "failed"} and not pipeline.is_busy(run.id):
            break
        if run.status == "awaiting_footage" and not simulated and not pipeline.is_busy(run.id):
            if args.footage:
                from pathlib import Path
                pipeline.attach_footage(run.id, Path(args.footage).resolve())
            else:
                pipeline.simulate_footage(run.id)
            simulated = True
        if run.status in WAITING - {"awaiting_footage"} and not args.auto:
            print(f"\nRun is waiting at '{run.status}'. Use the dashboard (python -m content_agents serve).")
            break
        time.sleep(0.5)
    for ev in pipeline.store.events(run.id, seen):
        print(f"  [{ev['agent']:>13}] {ev['message']}")
    print(f"\nStatus: {run.status}")
    if run.edit:
        print(f"Video:  {settings.run_dir(run.id) / run.edit.output_file}")
    pipeline.shutdown()


def cmd_serve(args: argparse.Namespace) -> None:
    from .server import serve

    serve(host=args.host, port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="content_agents", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the pipeline in the terminal")
    run_p.add_argument("topics", nargs="*", help="raw topic ideas (defaults to two sample topics)")
    run_p.add_argument("--auto", action="store_true", help="auto-approve the angle and the drafts")
    run_p.add_argument("--footage", help="path to a real video to use instead of the simulated take")
    run_p.set_defaults(func=cmd_run)

    serve_p = sub.add_parser("serve", help="start the dashboard")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
