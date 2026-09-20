MIGRATION_ONLY_TABLES = frozenset({
    "task_collaboration_user_workspace_map",
})


def include_object(object_, name, type_, reflected, compare_to):
    """Exclude only migration-owned helper tables from autogenerate."""
    return not (type_ == "table" and name in MIGRATION_ONLY_TABLES)
