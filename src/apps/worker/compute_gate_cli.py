"""Command-line entry point for the local-only strategy Compute Gate."""
from __future__ import annotations

import argparse
import json
import os

from core.backtest_contracts import BacktestQueueError
from core.backtest_operations import BacktestOperations
from src.apps.worker.backtest_runtime import BacktestRuntime, WorkerSettings, build_local_queue
from src.apps.worker.compute_gate import (
    EXECUTABLE_GATE_STATES,
    ComputeGate,
    ComputeGateError,
    ComputeGateSettings,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only strategy Compute Gate")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--once", action="store_true", help="import bounded drop requests at most once")
    actions.add_argument("--cancel-job", help="cancel one local queue job as an operator")
    parser.add_argument("--execute-one", action="store_true", help="after import, process at most one local queue job")
    parser.add_argument("--operator-id")
    parser.add_argument("--cancel-request-id")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--reason-code")
    args = parser.parse_args(argv)
    try:
        if args.cancel_job:
            worker = WorkerSettings.from_environment()
            queue = build_local_queue(worker)
            if not all((args.operator_id, args.cancel_request_id, args.manifest_sha256, args.reason_code)):
                raise ComputeGateError("operator cancel requires identity, request, manifest and reason code")
            result = BacktestOperations(queue).cancel_system(
                args.cancel_job,
                operator_subject=args.operator_id,
                request_id=args.cancel_request_id,
                reason_code=args.reason_code,
                expected_manifest_sha256=args.manifest_sha256,
            )
        else:
            settings = ComputeGateSettings.from_environment()
            queue = build_local_queue(settings.worker_settings())
            service = ComputeGate(queue, settings)
            result = service.run_once()
            if args.execute_one and result["state"] in EXECUTABLE_GATE_STATES:
                execution_gate = service.execution_gate_state()
                if execution_gate == "ready":
                    outcome = BacktestRuntime(queue, settings.worker_settings()).run_once()
                    result = {**result, "execution": {"state": outcome.state, "job_id": outcome.job_id}}
                else:
                    result = {**result, "execution": {"state": execution_gate, "job_id": None}}
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (ComputeGateError, BacktestQueueError) as exc:
        print(f"compute gate refused: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
