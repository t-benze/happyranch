#!/usr/bin/env python3
"""CLI entry point for running Product & Engineering Crew tasks."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.infrastructure.database import Database
from src.models import TaskType
from src.orchestrator.orchestrator import Orchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Product & Engineering Crew task"
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=["implement_feature", "bug_fix", "payment_change"],
        help="Type of task to run",
    )
    parser.add_argument(
        "--brief",
        required=True,
        help="Task description / brief",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database (default: opc.db in project root)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db_path = Path(args.db) if args.db else settings.get_db_path()
    db = Database(db_path)

    orchestrator = Orchestrator(db=db, settings=settings)

    task_type = TaskType(args.task)
    task_id = orchestrator.create_task(task_type, args.brief)

    logging.info("Created task %s (%s): %s", task_id, args.task, args.brief)
    logging.info("Running task...")

    result = orchestrator.run_task(task_id)

    logging.info("Task %s completed with status: %s", task_id, result)

    # Print summary
    task = db.get_task(task_id)
    print(f"\n{'='*60}")
    print(f"Task ID:    {task_id}")
    print(f"Type:       {args.task}")
    print(f"Status:     {result}")
    print(f"Revisions:  {task.revision_count}")
    print(f"{'='*60}")

    db.close()


if __name__ == "__main__":
    main()
