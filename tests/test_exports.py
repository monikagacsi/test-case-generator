import csv
import io
import json

from testgen.exports import export_csv, export_json, export_markdown
from testgen.schema import (
    ApiDetails,
    Category,
    ReviewItem,
    Status,
    TestCase as Case,
)


def _item(item_id: str, status: Status) -> ReviewItem:
    return ReviewItem(
        id=item_id,
        status=status,
        flags=["Possible duplicate | review"],
        test_case=Case(
            title="Create user with valid details",
            category=Category.API,
            priority="high",
            source_ref="US-01",
            preconditions=["Admin is signed in"],
            steps=["Send a POST request", "Verify the response"],
            test_data={"name": "Zoë", "email": "zoe@example.com"},
            expected_results=[
                "The server accepts the create-user request",
                "The created user details are returned",
            ],
            api=ApiDetails(
                method="POST",
                path="/users",
                request_body={"name": "Zoë"},
                expected_status=201,
            ),
        ),
    )


def _items() -> list[ReviewItem]:
    return [
        _item("TC-001", Status.PENDING),
        ReviewItem(
            id="TC-002",
            status=Status.ACCEPTED,
            manually_edited=True,
            flags=["Possible duplicate | review"],
            test_case=_item("TC-002", Status.ACCEPTED).test_case,
        ),
        ReviewItem(
            id="TC-003",
            status=Status.PENDING,
            manually_edited=True,
            test_case=_item("TC-003", Status.PENDING).test_case,
        ),
        _item("TC-004", Status.REJECTED),
    ]


def test_json_export_includes_only_accepted_cases():
    exported = json.loads(export_json(_items()))

    assert [item["id"] for item in exported] == ["TC-002"]
    assert [item["status"] for item in exported] == ["accepted"]
    assert exported[0]["manually_edited"] is True
    assert exported[0]["test_case"]["api"]["expected_status"] == 201
    assert exported[0]["flags"] == ["Possible duplicate | review"]


def test_csv_export_includes_only_approved_cases_and_round_trips_nested_fields():
    reader = csv.DictReader(io.StringIO(export_csv(_items())))
    rows = list(reader)

    assert [row["id"] for row in rows] == ["TC-002"]
    assert rows[0]["status"] == "accepted"
    assert rows[0]["manually_edited"] == "True"
    assert json.loads(rows[0]["steps"]) == [
        "Send a POST request",
        "Verify the response",
    ]
    assert json.loads(rows[0]["expected_results"]) == [
        "The server accepts the create-user request",
        "The created user details are returned",
    ]
    assert json.loads(rows[0]["test_data"]) == {
        "name": "Zoë",
        "email": "zoe@example.com",
    }
    assert json.loads(rows[0]["api"])["expected_status"] == 201
    assert json.loads(rows[0]["flags"]) == ["Possible duplicate | review"]


def test_markdown_export_is_readable_and_escapes_table_delimiters():
    exported = export_markdown(_items())

    assert exported.startswith("# Approved test cases")
    assert "## TC-002: Create user with valid details" in exported
    assert "## TC-003: Create user with valid details" not in exported
    assert "TC-001" not in exported
    assert "TC-004" not in exported
    assert "- Possible duplicate \\| review" in exported
    assert "1. Send a POST request" in exported
    assert "- **Manually edited:** Yes" in exported
    assert "1. The server accepts the create-user request" in exported
    assert "2. The created user details are returned" in exported
    assert '"expected_status": 201' in exported


def test_exports_handle_empty_approved_set():
    items = [_item("TC-001", Status.PENDING), _item("TC-002", Status.REJECTED)]

    assert json.loads(export_json(items)) == []
    assert list(csv.DictReader(io.StringIO(export_csv(items)))) == []
    assert export_markdown(items) == (
        "# Approved test cases\n\nNo accepted test cases.\n"
    )
