#!/usr/bin/env python3
"""Deploy clean committed child checkouts from this MkChad workspace."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import sys
import uuid

import mkchad_deploy as deploy
import mkchad_release as release


class _StreamLog:
    """Send direct deployer output through the release redaction and log writer."""

    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, text: str) -> int:
        """Write one text chunk to its terminal stream and the current run log."""
        if text:
            release._write_stream(self.stream, self.log, text.encode("utf-8", errors="replace"))
        return len(text)

    def flush(self):
        """Flush the terminal stream and persistent run log."""
        self.stream.flush()
        self.log.flush()


def _fallback_result(workspace: pathlib.Path, run_id: str, started_at: str, log_path: pathlib.Path, diagnostic: str, image: pathlib.Path | None) -> dict:
    """Build a bounded developer failure result before source admission completes."""
    return {
        "mode": "developer", "run_id": run_id,
        "workspace": {"path": str(workspace), "revision": None, "dirty": None},
        "started_at": started_at, "completed_at": release._timestamp(), "outcome": "failed", "phase": "preflight",
        "components": [], "artifact": {"architecture": os.uname().machine, "requested_sha256": None, "observed_sha256": None},
        "image": {"choice": "omitted" if image is None else "install", "supplied_sha256": None, "active_sha256": None, "decision": "preserved" if image is None else "unavailable"},
        "actions": [], "failed_step": "developer-admission", "diagnostic": release._redact(diagnostic)[:4096], "log_path": str(log_path),
    }


def _matching_result(path: pathlib.Path, run_id: str, log_path: pathlib.Path, status: int) -> bool:
    """Return whether the direct deployer published this invocation's result."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return document.get("mode") == "developer" and document.get("run_id") == run_id and document.get("log_path") == str(log_path) and (status == 0) == (document.get("outcome") == "succeeded")


def main(argv: list[str] | None = None) -> int:
    """Run one local developer deployment under the shared invocation lock.

    The command accepts no release selector. It validates the current parent
    workspace, stages local child commits through the deployer, and atomically
    promotes the direct developer result to the shared latest-result path.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--image", type=pathlib.Path)
    parser.add_argument("--replace-partial", action="store_true")
    args = parser.parse_args(argv)
    state_root = release._state_root()
    workspace = pathlib.Path.cwd().resolve()
    try:
        with release._invocation_lock(state_root):
            logs = state_root / "logs"
            release._rotate_logs(logs)
            run_id = str(uuid.uuid4())
            started_at = release._timestamp()
            log_path = logs / f"run-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{run_id}.log"
            result_path = state_root / f".result-{run_id}.json"
            latest_result = state_root / "latest-result.json"
            with log_path.open("x", encoding="utf-8") as log, contextlib.redirect_stdout(_StreamLog(sys.stdout, log)), contextlib.redirect_stderr(_StreamLog(sys.stderr, log)):
                try:
                    status = deploy.execute_developer_deployment(workspace, args.image.absolute() if args.image is not None else None, log_path=log_path, result_path=result_path, run_id=run_id, replace_partial=args.replace_partial)
                    if _matching_result(result_path, run_id, log_path, status):
                        os.replace(result_path, latest_result)
                        return status
                    release._publish_result(latest_result, _fallback_result(workspace, run_id, started_at, log_path, "developer deployer did not publish a matching terminal result", args.image))
                    return status if status else 2
                except (deploy.DeploymentError, OSError) as exc:
                    release._write_stream(sys.stderr, log, ("mkchad-develop: " + str(exc) + "\n").encode())
                    release._publish_result(latest_result, _fallback_result(workspace, run_id, started_at, log_path, str(exc), args.image))
                    return 2
                except KeyboardInterrupt:
                    result = _fallback_result(workspace, run_id, started_at, log_path, "developer deployment interrupted", args.image)
                    result["outcome"] = "interrupted"
                    release._publish_result(latest_result, result)
                    return 2
                finally:
                    result_path.unlink(missing_ok=True)
    except release.InvocationBusy:
        print("mkchad-develop: another invocation is already running", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
