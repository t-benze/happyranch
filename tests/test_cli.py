import sys
from unittest.mock import MagicMock, patch

from src.models import TaskType


def test_parse_args():
    # Import inline to avoid config issues
    sys.argv = [
        "run_product_crew.py",
        "--task", "implement_feature",
        "--brief", "Add Alipay support for international cards",
    ]
    from scripts.run_product_crew import parse_args

    args = parse_args()
    assert args.task == "implement_feature"
    assert args.brief == "Add Alipay support for international cards"


def test_parse_args_bug_fix():
    sys.argv = [
        "run_product_crew.py",
        "--task", "bug_fix",
        "--brief", "Fix broken payment links",
    ]
    from scripts.run_product_crew import parse_args

    args = parse_args()
    assert args.task == "bug_fix"
