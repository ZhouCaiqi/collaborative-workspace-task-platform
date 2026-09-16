from alembic_helpers import include_object


def test_include_object_excludes_only_task_migration_mapping_table():
    assert include_object(
        object_=object(),
        name="task_collaboration_user_workspace_map",
        type_="table",
        reflected=True,
        compare_to=None,
    ) is False

    assert include_object(
        object_=object(),
        name="tasks",
        type_="table",
        reflected=True,
        compare_to=None,
    ) is True
    assert include_object(
        object_=object(),
        name="workspace_id",
        type_="column",
        reflected=True,
        compare_to=None,
    ) is True
