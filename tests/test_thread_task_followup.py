from src.models import ThreadInvocationPurpose


def test_task_followup_purpose_value():
    assert ThreadInvocationPurpose.TASK_FOLLOWUP.value == "task_followup"
    assert "task_followup" in {p.value for p in ThreadInvocationPurpose}
