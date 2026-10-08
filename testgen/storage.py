import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

from testgen.schema import Category, ReviewItem, Status, StoryGroup, TestCase
from testgen.ingest import Story

DEFAULT_DATABASE_PATH = Path("output/review_queue.sqlite3")


def _database_id(item_id: str) -> int:
    prefix, separator, value = item_id.partition("-")
    if prefix != "TC" or not separator or not value.isdecimal():
        raise ValueError(f"Invalid review item ID: {item_id!r}")
    return int(value)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS story_groups (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            story_text TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            database_id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            test_case_json TEXT NOT NULL,
            flags_json TEXT NOT NULL,
            manually_edited INTEGER NOT NULL DEFAULT 0,
            story_group_id TEXT REFERENCES story_groups(id) ON DELETE CASCADE
        )
        """
    )
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(review_items)").fetchall()
    }
    needs_group_migration = "story_group_id" not in columns
    if "manually_edited" not in columns:
        connection.execute(
            "ALTER TABLE review_items ADD COLUMN manually_edited INTEGER NOT NULL DEFAULT 0"
        )
    if "story_group_id" not in columns:
        connection.execute(
            "ALTER TABLE review_items ADD COLUMN story_group_id TEXT REFERENCES story_groups(id)"
        )
    connection.execute(
        """
        UPDATE review_items
        SET status = ?, manually_edited = 1
        WHERE status = 'edited'
        """,
        (Status.PENDING.value,),
    )
    if needs_group_migration:
        legacy_sources = connection.execute(
            """
            SELECT DISTINCT source_ref
            FROM review_items
            WHERE story_group_id IS NULL
            """
        ).fetchall()
        for row in legacy_sources:
            source_ref = row["source_ref"]
            group_id = f"LEGACY-{uuid5(NAMESPACE_URL, source_ref).hex}"
            connection.execute(
                """
                INSERT OR IGNORE INTO story_groups (id, title, source_ref)
                VALUES (?, ?, ?)
                """,
                (group_id, source_ref, source_ref),
            )
            connection.execute(
                "UPDATE review_items SET story_group_id = ? WHERE story_group_id IS NULL AND source_ref = ?",
                (group_id, source_ref),
            )
    connection.commit()
    return connection


def _save_items(
    connection: sqlite3.Connection,
    items: list[ReviewItem],
    group_id: str | None = None,
) -> list[ReviewItem]:
    saved_items: list[ReviewItem] = []
    id_mapping: dict[str, str] = {}
    for item in items:
        cursor = connection.execute(
            """
            INSERT INTO review_items (
                status, category, source_ref, test_case_json, flags_json,
                manually_edited, story_group_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.status.value,
                item.test_case.category.value,
                item.test_case.source_ref,
                item.test_case.model_dump_json(),
                json.dumps(item.flags),
                item.manually_edited,
                group_id,
            ),
        )
        id_mapping[item.id] = f"TC-{cursor.lastrowid:03d}"

    for item in items:
        database_id = int(id_mapping[item.id].removeprefix("TC-"))
        flags = [
            re.sub(
                r"(Possible duplicate of )(TC-\d+)",
                lambda match: (
                    match.group(1) + id_mapping.get(match.group(2), match.group(2))
                ),
                flag,
            )
            for flag in item.flags
        ]
        connection.execute(
            "UPDATE review_items SET flags_json = ? WHERE database_id = ?",
            (json.dumps(flags), database_id),
        )
        saved_items.append(
            item.model_copy(
                update={
                    "id": id_mapping[item.id],
                    "flags": flags,
                    "group_id": group_id,
                }
            )
        )
    return saved_items


def _reset_id_sequence_if_empty(connection: sqlite3.Connection) -> None:
    if connection.execute(
        "SELECT 1 FROM review_items LIMIT 1"
    ).fetchone() is None:
        connection.execute(
            "DELETE FROM sqlite_sequence WHERE name = 'review_items'"
        )


def save_review_items(
    items: list[ReviewItem],
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[ReviewItem]:
    """Persist generated review items and return them with stable database IDs."""
    with closing(_connect(database_path)) as connection:
        with connection:
            return _save_items(connection, items)


def save_story_group(
    story: Story,
    items: list[ReviewItem],
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    group_title: str | None = None,
) -> list[ReviewItem]:
    """Persist a test case group and all its generated cases atomically."""
    group_id = f"SG-{uuid4().hex}"
    with closing(_connect(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO story_groups (id, title, source_ref, story_text, domain)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    group_title or story.title,
                    story.id,
                    story.text,
                    story.domain,
                ),
            )
            return _save_items(connection, items, group_id)


def list_story_groups(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[StoryGroup]:
    """List test case groups with counts by review status."""
    with closing(_connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                groups.*,
                COUNT(items.database_id) AS case_count,
                SUM(CASE WHEN items.status = ? THEN 1 ELSE 0 END) AS accepted_count,
                SUM(CASE WHEN items.status = ? THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN items.status = ? THEN 1 ELSE 0 END) AS rejected_count,
                SUM(CASE WHEN items.manually_edited = 1 THEN 1 ELSE 0 END)
                    AS manually_edited_count
            FROM story_groups AS groups
            LEFT JOIN review_items AS items ON items.story_group_id = groups.id
            GROUP BY groups.id
            ORDER BY groups.created_at DESC, groups.id DESC
            """,
            (Status.ACCEPTED.value, Status.PENDING.value, Status.REJECTED.value),
        ).fetchall()
    return [StoryGroup(**dict(row)) for row in rows]


def rename_story_group(
    group_id: str,
    title: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Rename a persisted test case group."""
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Test case group title cannot be empty.")
    with closing(_connect(database_path)) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE story_groups SET title = ? WHERE id = ?",
                (normalized_title, group_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No test case group found with ID {group_id!r}")


def delete_story_group(
    group_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Permanently delete a test case group and all of its review items."""
    with closing(_connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            cursor = connection.execute(
                "SELECT COUNT(*) AS case_count FROM review_items WHERE story_group_id = ?",
                (group_id,),
            )
            case_count = cursor.fetchone()["case_count"]
            connection.execute(
                "DELETE FROM review_items WHERE story_group_id = ?",
                (group_id,),
            )
            deleted = connection.execute(
                "DELETE FROM story_groups WHERE id = ?",
                (group_id,),
            )
            if deleted.rowcount == 0:
                raise KeyError(f"No test case group found with ID {group_id!r}")
            _reset_id_sequence_if_empty(connection)
    return case_count


def list_review_items(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    status: Status | None = None,
    category: Category | None = None,
    group_id: str | None = None,
) -> list[ReviewItem]:
    """Load persisted review items, optionally filtering by status or category."""
    query = """
        SELECT database_id, status, test_case_json, flags_json, manually_edited,
               story_group_id
        FROM review_items
    """
    filters: list[str] = []
    parameters: list[str] = []
    if status is not None:
        filters.append("status = ?")
        parameters.append(status.value)
    if category is not None:
        filters.append("category = ?")
        parameters.append(category.value)
    if group_id is not None:
        filters.append("story_group_id = ?")
        parameters.append(group_id)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY database_id"

    with closing(_connect(database_path)) as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        ReviewItem(
            id=f"TC-{row['database_id']:03d}",
            status=row["status"],
            manually_edited=bool(row["manually_edited"]),
            group_id=row["story_group_id"],
            test_case=json.loads(row["test_case_json"]),
            flags=json.loads(row["flags_json"]),
        )
        for row in rows
    ]


def _set_status(
    item_id: str,
    status: Status,
    database_path: str | Path,
) -> ReviewItem:
    database_id = _database_id(item_id)
    with closing(_connect(database_path)) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE review_items SET status = ? WHERE database_id = ?",
                (status.value, database_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No review item found with ID {item_id!r}")
    return next(
        item
        for item in list_review_items(database_path)
        if item.id == f"TC-{database_id:03d}"
    )


def accept_review_item(
    item_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> ReviewItem:
    """Mark a persisted review item as accepted."""
    return _set_status(item_id, Status.ACCEPTED, database_path)


def reject_review_item(
    item_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> ReviewItem:
    """Mark a persisted review item as rejected."""
    return _set_status(item_id, Status.REJECTED, database_path)


def delete_review_item(
    item_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Permanently delete a pending or rejected review item."""
    database_id = _database_id(item_id)
    with closing(_connect(database_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                DELETE FROM review_items
                WHERE database_id = ? AND status IN (?, ?)
                """,
                (database_id, Status.PENDING.value, Status.REJECTED.value),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT status FROM review_items WHERE database_id = ?",
                    (database_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"No review item found with ID {item_id!r}")
                raise ValueError(
                    f"Review item {item_id!r} cannot be deleted while "
                    f"its status is {row['status']!r}."
                )
            _reset_id_sequence_if_empty(connection)


def edit_review_item(
    item_id: str,
    test_case: TestCase,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> ReviewItem:
    """Replace a persisted test case, mark it as manually edited, and set pending."""
    database_id = _database_id(item_id)
    with closing(_connect(database_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                UPDATE review_items
                SET status = ?, category = ?, source_ref = ?, test_case_json = ?,
                    manually_edited = 1
                WHERE database_id = ?
                """,
                (
                    Status.PENDING.value,
                    test_case.category.value,
                    test_case.source_ref,
                    test_case.model_dump_json(),
                    database_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No review item found with ID {item_id!r}")
    return next(
        item
        for item in list_review_items(database_path)
        if item.id == f"TC-{database_id:03d}"
    )
