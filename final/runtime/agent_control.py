class WorkflowCancelled(Exception):
    """Cooperative stop before the task commit boundary; no execution facts are erased."""
