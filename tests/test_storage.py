import pytest

from testgen.schema import Category, ReviewItem, Status, TestCase as Case
from testgen.ingest import Story
from testgen.storage import (
    accept_review_item,
    delete_review_item,
    edit_review_item,
    list_review_items,
    list_story_groups,
    reject_review_item,
    rename_story_group,
    save_review_items,
    save_story_group,
    delete_story_group,
)


def _item(item_id: str, category: Category, status: Status = Status.PENDING):
    return ReviewItem(
        id=item_id,
        status=status,
        flags=["Needs review"],
        test_case=Case(
            title="Login with valid credentials",
            category=category,
            source_ref="US-01",
            steps=["Open login", "Submit valid credentials"],
            expected_results=[
                "The login page is displayed",
                "The account dashboard is displayed",
            ],
            test_data={"username": "sam@example.com"},
        ),
    )


def test_save_and_load_review_items_persists_all_fields(tmp_path):
    database = tmp_path / "nested" / "review.sqlite3"
    original = _item("temporary-id", Category.FUNCTIONAL)

    saved = save_review_items([original], database)

    assert saved[0].id == "TC-001"
    assert list_review_items(database) == saved


def test_story_group_saves_metadata_and_aggregated_counts(tmp_path):
    database = tmp_path / "review.sqlite3"
    story = Story(
        id="US-01",
        title="Login",
        text="As a user, I want to log in.",
        domain="Authentication",
    )
    items = [
        _item("TC-001", Category.FUNCTIONAL, Status.ACCEPTED),
        _item("TC-002", Category.FUNCTIONAL, Status.PENDING),
    ]

    saved = save_story_group(story, items, database)
    groups = list_story_groups(database)

    assert len(groups) == 1
    assert groups[0].title == story.title
    assert groups[0].source_ref == story.id
    assert groups[0].story_text == story.text
    assert groups[0].case_count == 2
    assert groups[0].accepted_count == 1
    assert groups[0].pending_count == 1
    assert {item.group_id for item in saved} == {groups[0].id}
    assert list_review_items(database, group_id=groups[0].id) == saved


def test_story_groups_are_distinct_even_when_the_story_reference_repeats(tmp_path):
    database = tmp_path / "review.sqlite3"
    story = Story(id="US-01", title="Login", text="Login story")

    first = save_story_group(story, [_item("TC-001", Category.FUNCTIONAL)], database)
    second = save_story_group(story, [_item("TC-001", Category.FUNCTIONAL)], database)

    assert first[0].group_id != second[0].group_id
    assert len(list_story_groups(database)) == 2


def test_story_group_title_can_be_renamed(tmp_path):
    database = tmp_path / "review.sqlite3"
    story = Story(id="US-01", title="Long story text", text="Story body")
    saved = save_story_group(story, [_item("TC-001", Category.FUNCTIONAL)], database)

    rename_story_group(saved[0].group_id, "  round-up-savings · Guest checkout  ", database)

    assert list_story_groups(database)[0].title == "round-up-savings · Guest checkout"


def test_renaming_story_group_rejects_empty_title(tmp_path):
    database = tmp_path / "review.sqlite3"
    story = Story(id="US-01", title="Login", text="Login story")
    saved = save_story_group(story, [_item("TC-001", Category.FUNCTIONAL)], database)

    with pytest.raises(ValueError, match="cannot be empty"):
        rename_story_group(saved[0].group_id, "  ", database)


def test_deleting_group_removes_all_cases_including_accepted(tmp_path):
    database = tmp_path / "review.sqlite3"
    story = Story(id="US-01", title="Login", text="Login story")
    saved = save_story_group(
        story,
        [
            _item("TC-001", Category.FUNCTIONAL, Status.ACCEPTED),
            _item("TC-002", Category.FUNCTIONAL, Status.PENDING),
        ],
        database,
    )

    deleted_count = delete_story_group(saved[0].group_id, database)

    assert deleted_count == 2
    assert list_review_items(database) == []
    assert list_story_groups(database) == []
    assert save_review_items(
        [_item("temporary-id", Category.FUNCTIONAL)], database
    )[0].id == "TC-001"


def test_deleting_group_works_after_legacy_foreign_key_migration(tmp_path):
    import json
    import sqlite3

    database = tmp_path / "legacy-review.sqlite3"
    story = Story(id="US-01", title="Login", text="Login story")
    case = _item("temporary-id", Category.FUNCTIONAL, Status.ACCEPTED).test_case
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE review_items (
                database_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                category TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                test_case_json TEXT NOT NULL,
                flags_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO review_items (
                status, category, source_ref, test_case_json, flags_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                Status.ACCEPTED.value,
                case.category.value,
                story.id,
                case.model_dump_json(),
                json.dumps([]),
            ),
        )

    migrated_item = list_review_items(database)[0]
    assert migrated_item.group_id is not None

    deleted_count = delete_story_group(migrated_item.group_id, database)

    assert deleted_count == 1
    assert list_review_items(database) == []
    assert list_story_groups(database) == []


def test_persisted_ids_are_unique_across_generation_runs(tmp_path):
    database = tmp_path / "review.sqlite3"

    first = save_review_items([_item("TC-001", Category.FUNCTIONAL)], database)
    second = save_review_items([_item("TC-001", Category.API)], database)

    assert first[0].id == "TC-001"
    assert second[0].id == "TC-002"
    assert [item.id for item in list_review_items(database)] == ["TC-001", "TC-002"]


def test_duplicate_flags_are_remapped_to_persisted_ids(tmp_path):
    database = tmp_path / "review.sqlite3"
    save_review_items([_item("TC-001", Category.FUNCTIONAL)], database)
    batch = [
        _item("TC-001", Category.FUNCTIONAL),
        _item("TC-002", Category.FUNCTIONAL).model_copy(
            update={"flags": ["Possible duplicate of TC-001 (1.00 similarity)"]}
        ),
    ]

    saved = save_review_items(batch, database)

    assert saved[0].id == "TC-002"
    assert saved[1].id == "TC-003"
    assert saved[1].flags == ["Possible duplicate of TC-002 (1.00 similarity)"]
    assert list_review_items(database)[-1].flags == saved[1].flags


def test_list_review_items_filters_by_status_and_category(tmp_path):
    database = tmp_path / "review.sqlite3"
    save_review_items(
        [
            _item("TC-001", Category.FUNCTIONAL, Status.ACCEPTED),
            _item("TC-002", Category.API, Status.PENDING),
            _item("TC-003", Category.API, Status.ACCEPTED),
        ],
        database,
    )

    filtered = list_review_items(
        database,
        status=Status.ACCEPTED,
        category=Category.API,
    )

    assert [item.id for item in filtered] == ["TC-003"]


def test_listing_an_uninitialized_database_returns_empty_list(tmp_path):
    database = tmp_path / "review.sqlite3"

    assert list_review_items(database) == []
    assert database.exists()


def test_accept_and_reject_statuses_persist(tmp_path):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items(
        [
            _item("TC-001", Category.FUNCTIONAL),
            _item("TC-002", Category.API),
        ],
        database,
    )

    accepted = accept_review_item(saved[0].id, database)
    rejected = reject_review_item(saved[1].id, database)

    assert accepted.status == Status.ACCEPTED
    assert rejected.status == Status.REJECTED
    assert [item.status for item in list_review_items(database)] == [
        Status.ACCEPTED,
        Status.REJECTED,
    ]


@pytest.mark.parametrize("status", [Status.PENDING, Status.REJECTED])
def test_delete_removes_pending_or_rejected_item(tmp_path, status):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items(
        [_item("TC-001", Category.FUNCTIONAL, status)],
        database,
    )

    delete_review_item(saved[0].id, database)

    assert list_review_items(database) == []
    assert save_review_items(
        [_item("temporary-id", Category.FUNCTIONAL)], database
    )[0].id == "TC-001"


def test_delete_rejects_accepted_item(tmp_path):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items(
        [_item("TC-001", Category.FUNCTIONAL, Status.ACCEPTED)],
        database,
    )

    with pytest.raises(ValueError, match="cannot be deleted"):
        delete_review_item(saved[0].id, database)

    assert list_review_items(database) == saved


def test_edit_replaces_case_sets_pending_and_marks_manually_edited(tmp_path):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items([_item("TC-001", Category.FUNCTIONAL)], database)
    updated_case = Case(
        title="Login rejects incorrect password",
        category=Category.NEGATIVE,
        source_ref="US-02",
        steps=["Open the login page", "Submit an incorrect password"],
        expected_results=[
            "The login page is displayed",
            "An authentication error is displayed",
        ],
    )

    edited = edit_review_item(saved[0].id, updated_case, database)

    assert edited.status == Status.PENDING
    assert edited.manually_edited is True
    assert edited.test_case == updated_case
    assert edited.flags == ["Needs review"]
    assert list_review_items(database) == [edited]


def test_editing_accepted_case_returns_it_to_pending(tmp_path):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items(
        [_item("TC-001", Category.FUNCTIONAL, Status.ACCEPTED)],
        database,
    )

    edited = edit_review_item(saved[0].id, saved[0].test_case, database)

    assert edited.status == Status.PENDING
    assert edited.manually_edited is True


def test_accepting_manually_edited_pending_case_keeps_marker(tmp_path):
    database = tmp_path / "review.sqlite3"
    saved = save_review_items([_item("TC-001", Category.FUNCTIONAL)], database)
    edited = edit_review_item(saved[0].id, saved[0].test_case, database)

    accepted = accept_review_item(edited.id, database)

    assert accepted.status == Status.ACCEPTED
    assert accepted.manually_edited is True


def test_legacy_edited_database_rows_migrate_to_pending_marker(tmp_path):
    import json
    import sqlite3

    database = tmp_path / "legacy.sqlite3"
    test_case = _item("temporary-id", Category.FUNCTIONAL).test_case
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE review_items (
                database_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                category TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                test_case_json TEXT NOT NULL,
                flags_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO review_items (
                status, category, source_ref, test_case_json, flags_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "edited",
                test_case.category.value,
                test_case.source_ref,
                test_case.model_dump_json(),
                json.dumps([]),
            ),
        )

    migrated = list_review_items(database)

    assert len(migrated) == 1
    assert migrated[0].status == Status.PENDING
    assert migrated[0].manually_edited is True


@pytest.mark.parametrize(
    ("operation", "item_id", "error"),
    [
        (accept_review_item, "not-an-id", ValueError),
        (reject_review_item, "TC-999", KeyError),
        (delete_review_item, "TC-999", KeyError),
    ],
)
def test_status_updates_report_invalid_or_missing_items(
    tmp_path, operation, item_id, error
):
    database = tmp_path / "review.sqlite3"

    with pytest.raises(error):
        operation(item_id, database)


def test_editing_missing_item_reports_not_found(tmp_path):
    database = tmp_path / "review.sqlite3"
    test_case = _item("TC-001", Category.FUNCTIONAL).test_case

    with pytest.raises(KeyError, match="TC-999"):
        edit_review_item("TC-999", test_case, database)
