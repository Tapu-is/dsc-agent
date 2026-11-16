"""todo_write tool: replaces the todo list wholesale. Rendering is the loop's job."""
from __future__ import annotations

VALID_STATUS = ("pending", "in_progress", "completed")


def todo_write(state: dict, **args) -> str:
    """todo_write(todos=[{content, status}, ...]) — wholesale replace."""
    todos = args.get("todos")
    if todos is None:
        return "error: todo_write requires a list of todos"
    if not isinstance(todos, list):
        return "error: todos must be a list"
    normalized = []
    for i, t in enumerate(todos):
        if not isinstance(t, dict) or "content" not in t:
            return f"error: todo #{i + 1} must be an object with 'content'"
        status = t.get("status", "pending")
        if status not in VALID_STATUS:
            return (
                f"error: todo #{i + 1} has invalid status {status!r}; "
                f"must be one of {VALID_STATUS}"
            )
        normalized.append({"content": str(t["content"]), "status": status})

    in_progress = [t for t in normalized if t["status"] == "in_progress"]
    if len(in_progress) > 1:
        return (
            f"error: {len(in_progress)} todos are in_progress; "
            "exactly one item should be in_progress at a time"
        )

    state["todos"] = normalized
    done = sum(1 for t in normalized if t["status"] == "completed")
    return f"todos updated: {len(normalized)} items ({done} completed)"
