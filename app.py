import json
import sqlite3
from pathlib import Path

import streamlit as st
from google.genai.errors import APIError
from pydantic import ValidationError
import requests

from testgen.exports import export_csv, export_json, export_markdown
from testgen.generate import (
    PartialGenerationError,
    generate_and_save_stories,
    generate_for_openapi,
)
from testgen.ingest import (
    MAX_STORY_TEXT_CHARS,
    MAX_STORY_TITLE_CHARS,
    Story,
    parse_stories,
    story_from_text,
)
from testgen.openapi import parse_openapi
from testgen.schema import ApiDetails, Category, Priority, ReviewItem, Status, TestCase
from testgen.storage import (
    accept_review_item,
    delete_review_item,
    delete_story_group,
    edit_review_item,
    list_review_items,
    list_story_groups,
    rename_story_group,
    reject_review_item,
    save_review_items,
)


def _lines(value: list[str]) -> str:
    return "\n".join(value)


def _matches_review_filters(
    item: ReviewItem,
    status: Status | None,
    category: Category | None,
) -> bool:
    return (
        (status is None or item.status == status)
        and (category is None or item.test_case.category == category)
    )


def _source_label(source_ref: str) -> str:
    if source_ref.startswith("US-"):
        return f"User Story {source_ref.removeprefix('US-')}"
    return source_ref


def _queue_story_generation() -> None:
    st.session_state["story_generation_pending"] = True


def _queue_markdown_generation() -> None:
    st.session_state["markdown_generation_pending"] = True


def _stories_from_markdown_upload(content: bytes) -> list[Story]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("The Markdown file must be UTF-8 encoded.") from error
    stories = parse_stories(text)
    if not stories:
        raise ValueError("No user stories were found in the Markdown file.")
    return stories


def _build_edited_case(item: ReviewItem, values: dict[str, object]) -> TestCase:
    test_data_text = str(values["test_data"]).strip()
    test_data = json.loads(test_data_text) if test_data_text else None

    api = None
    if values["has_api"]:
        request_body_text = str(values["request_body"]).strip()
        request_body = json.loads(request_body_text) if request_body_text else None
        api = ApiDetails(
            method=str(values["method"]).strip(),
            path=str(values["path"]).strip(),
            request_body=request_body,
            expected_status=int(values["expected_status"]),
        )

    return TestCase(
        title=str(values["title"]).strip(),
        category=Category(str(values["category"])),
        priority=Priority(str(values["priority"])),
        source_ref=str(values["source_ref"]).strip(),
        preconditions=[
            line.strip()
            for line in str(values["preconditions"]).splitlines()
            if line.strip()
        ],
        steps=[
            line.strip()
            for line in str(values["steps"]).splitlines()
            if line.strip()
        ],
        expected_results=[
            line.strip()
            for line in str(values["expected_results"]).splitlines()
            if line.strip()
        ],
        test_data=test_data,
        api=api,
    )


def _render_item(item: ReviewItem) -> None:
    case = item.test_case
    heading = f"{item.id} · {case.title}"
    with st.expander(heading, expanded=item.status == Status.PENDING):
        st.caption(
            f"{case.category.value.title()} · {case.priority.value.title()} priority"
            f" · {_source_label(case.source_ref)} · {item.status.value.title()}"
        )
        if item.manually_edited:
            st.badge("Manually edited", color="orange")
        if item.flags:
            with st.container(key=f"warning-highlight-{item.id}"):
                st.warning("\n".join(f"- {flag}" for flag in item.flags))
        else:
            st.success("No validation flags")

        st.markdown("**Preconditions**")
        st.write("\n".join(f"- {value}" for value in case.preconditions) or "None")
        st.markdown("**Steps and expected results**")
        for index, (step, expected_result) in enumerate(
            zip(case.steps, case.expected_results, strict=True), start=1
        ):
            step_column, result_column = st.columns(2)
            step_column.markdown(f"**Step {index}**")
            step_column.write(step)
            result_column.markdown(f"**Expected result {index}**")
            result_column.write(expected_result)
        if case.test_data is not None:
            st.markdown("**Test data**")
            st.json(case.test_data)
        if case.api is not None:
            st.markdown("**API details**")
            st.json(case.api.model_dump(mode="json"))

        can_delete = item.status in {Status.PENDING, Status.REJECTED}
        action_columns = st.columns(3 if can_delete else 2)
        with action_columns[0].container(key=f"accept-action-{item.id}"):
            accept_clicked = st.button(
                "Accept",
                key=f"accept-{item.id}",
                type="primary",
                disabled=item.status == Status.ACCEPTED,
                use_container_width=True,
            )
        if accept_clicked:
            try:
                accept_review_item(item.id)
            except (KeyError, ValueError, sqlite3.Error) as error:
                st.error(f"Could not accept {item.id}: {error}")
            else:
                st.rerun()
        with action_columns[1].container(key=f"reject-action-{item.id}"):
            reject_clicked = st.button(
                "Reject",
                key=f"reject-{item.id}",
                type="secondary",
                disabled=item.status == Status.REJECTED,
                use_container_width=True,
            )
        if reject_clicked:
            try:
                reject_review_item(item.id)
            except (KeyError, ValueError, sqlite3.Error) as error:
                st.error(f"Could not reject {item.id}: {error}")
            else:
                st.rerun()

        if can_delete:
            with action_columns[2].container(key=f"delete-action-{item.id}"):
                delete_clicked = st.button(
                    "Delete",
                    icon=":material/delete:",
                    key=f"delete-{item.id}",
                    help="Permanently delete this pending or rejected case",
                )
            if delete_clicked:
                st.session_state["delete_confirmation_id"] = item.id
                st.rerun()

            if st.session_state.get("delete_confirmation_id") == item.id:
                st.warning(
                    f"Permanently delete {item.id}? This action cannot be undone."
                )
                confirmation_columns = st.columns(2)
                with confirmation_columns[0].container(
                    key=f"confirm-delete-action-{item.id}"
                ):
                    confirm_delete = st.button(
                        "Delete permanently",
                        icon=":material/delete_forever:",
                        key=f"confirm-delete-{item.id}",
                        use_container_width=True,
                    )
                cancel_delete = confirmation_columns[1].button(
                    "Cancel",
                    key=f"cancel-delete-{item.id}",
                    use_container_width=True,
                )
                if confirm_delete:
                    try:
                        delete_review_item(item.id)
                    except (KeyError, ValueError, sqlite3.Error) as error:
                        st.error(f"Could not delete {item.id}: {error}")
                    else:
                        del st.session_state["delete_confirmation_id"]
                        st.rerun()
                if cancel_delete:
                    del st.session_state["delete_confirmation_id"]
                    st.rerun()

        with st.expander(
            "Edit test case",
            expanded=False,
            icon=":material/edit:",
            key=f"edit-case-option-{item.id}",
        ):
            with st.form(f"edit-{item.id}"):
                first_row = st.columns(2)
                title = first_row[0].text_input(
                    "Title", value=case.title, key=f"title-{item.id}"
                )
                source_ref = first_row[1].text_input(
                    "Source reference", value=case.source_ref, key=f"source-{item.id}"
                )
                second_row = st.columns(2)
                category = second_row[0].selectbox(
                    "Category",
                    list(Category),
                    index=list(Category).index(case.category),
                    format_func=lambda value: value.value.title(),
                    key=f"category-{item.id}",
                )
                priority = second_row[1].selectbox(
                    "Priority",
                    list(Priority),
                    index=list(Priority).index(case.priority),
                    format_func=lambda value: value.value.title(),
                    key=f"priority-{item.id}",
                )
                preconditions = st.text_area(
                    "Preconditions (one per line)",
                    value=_lines(case.preconditions),
                    key=f"preconditions-{item.id}",
                )
                step_columns = st.columns(2)
                steps = step_columns[0].text_area(
                    "Steps (one per line)",
                    value=_lines(case.steps),
                    key=f"steps-{item.id}",
                    help="Each line is a separate step.",
                )
                expected_results = step_columns[1].text_area(
                    "Expected results (one per line, matching the steps)",
                    value=_lines(case.expected_results),
                    key=f"expected-results-{item.id}",
                    help="The result on each line corresponds to the step on the same line.",
                )
                test_data = st.text_area(
                    "Test data (JSON object)",
                    value=(
                        json.dumps(case.test_data, indent=2)
                        if case.test_data is not None
                        else ""
                    ),
                    key=f"test-data-{item.id}",
                )
                has_api = st.checkbox(
                    "Include API details",
                    value=case.api is not None,
                    key=f"has-api-{item.id}",
                )
                method = ""
                path = ""
                request_body = ""
                expected_status = 200
                if has_api:
                    api = case.api
                    api_columns = st.columns(2)
                    method = api_columns[0].text_input(
                        "HTTP method",
                        value=api.method if api else "",
                        key=f"method-{item.id}",
                    )
                    path = api_columns[1].text_input(
                        "API path",
                        value=api.path if api else "",
                        key=f"path-{item.id}",
                    )
                    expected_status = st.number_input(
                        "Expected HTTP status",
                        min_value=100,
                        max_value=599,
                        value=api.expected_status if api else 200,
                        key=f"status-code-{item.id}",
                    )
                    request_body = st.text_area(
                        "Request body (JSON object)",
                        value=(
                            json.dumps(api.request_body, indent=2)
                            if api and api.request_body is not None
                            else ""
                        ),
                        key=f"request-body-{item.id}",
                    )

                with st.container(key=f"save-edits-action-{item.id}"):
                    submitted = st.form_submit_button(
                        "Save edits",
                        type="secondary",
                        use_container_width=True,
                    )
                if submitted:
                    values: dict[str, object] = {
                        "title": title,
                        "category": category.value,
                        "priority": priority.value,
                        "source_ref": source_ref,
                        "preconditions": preconditions,
                        "steps": steps,
                        "expected_results": expected_results,
                        "test_data": test_data,
                        "has_api": has_api,
                        "method": method,
                        "path": path,
                        "request_body": request_body,
                        "expected_status": expected_status,
                    }
                    try:
                        updated_case = _build_edited_case(item, values)
                        edit_review_item(item.id, updated_case)
                    except (json.JSONDecodeError, ValidationError, ValueError) as error:
                        st.error(f"Could not save edits: {error}")
                    except (KeyError, sqlite3.Error) as error:
                        st.error(f"Could not update {item.id}: {error}")
                    else:
                        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Test Case Generator and Reviewer",
        page_icon="🔍",
        layout="wide",
    )
    st.title("Test Case Generator and Reviewer")
    st.caption("Generate and review tests, check validation flags, export approved tests.")
    st.markdown(
        """
        <style>
        [class*="st-key-accept-action-"] button {
            background-color: #198754;
            border-color: #198754;
            color: white;
        }
        [class*="st-key-accept-action-"] button:hover {
            background-color: #157347;
            border-color: #146c43;
            color: white;
        }
        [class*="st-key-reject-action-"] button {
            background-color: transparent;
            border: 1px solid #dc3545;
            color: #dc3545;
        }
        [class*="st-key-reject-action-"] button:hover {
            background-color: #dc3545;
            border-color: #dc3545;
            color: white;
        }
        [class*="st-key-delete-action-"] button,
        [class*="st-key-confirm-delete-action-"] button,
        [class*="st-key-confirm-delete-group-"] button,
        [class*="st-key-delete-story-group-"] button {
            background-color: #dc3545;
            border-color: #dc3545;
            color: white;
        }
        [class*="st-key-delete-action-"] button:hover,
        [class*="st-key-confirm-delete-action-"] button:hover,
        [class*="st-key-confirm-delete-group-"] button:hover,
        [class*="st-key-delete-story-group-"] button:hover {
            background-color: #b02a37;
            border-color: #b02a37;
            color: white;
        }
        [class*="st-key-save-group-name-"] button {
            background-color: #198754;
            border-color: #198754;
            color: white;
        }
        [class*="st-key-save-group-name-"] button:hover {
            background-color: #157347;
            border-color: #146c43;
            color: white;
        }
        [class*="st-key-cancel-group-name-"] button {
            background-color: #fd7e14;
            border-color: #fd7e14;
            color: white;
        }
        [class*="st-key-cancel-group-name-"] button:hover {
            background-color: #e96b0c;
            border-color: #e96b0c;
            color: white;
        }
        [class*="st-key-save-edits-action-"] button {
            background-color: transparent;
            border: 1px solid #fd7e14;
            color: #fd7e14;
        }
        [class*="st-key-save-edits-action-"] button:hover {
            background-color: #fd7e14;
            border-color: #fd7e14;
            color: white;
        }
        [class*="st-key-test-case-group-row-odd-"],
        [class*="st-key-test-case-group-row-even-"] {
            border: 1px solid #d1d5db;
            border-radius: 0.5rem;
            margin-bottom: 0.5rem;
            padding: 0.75rem 1rem;
        }
        [class*="st-key-test-case-group-row-odd-"] {
            background-color: #f3f4f6;
        }
        [class*="st-key-test-case-group-row-even-"] {
            background-color: #ffffff;
        }
        [class*="st-key-rename-story-group-"] button {
            min-height: 3rem;
            white-space: nowrap;
        }
        [class*="st-key-delete-story-group-"] button {
            box-sizing: border-box;
            width: 11rem;
            max-width: 100%;
            height: 3rem !important;
            min-height: 3rem !important;
            padding: 0 0.75rem;
            white-space: nowrap;
        }
        [class*="st-key-delete-action-"] button {
            box-sizing: border-box;
            width: 11rem;
            max-width: 100%;
            height: 2.5rem !important;
            min-height: 2.5rem !important;
            padding: 0 0.75rem;
            white-space: nowrap;
        }
        [class*="st-key-generate-markdown-stories"] button,
        [class*="st-key-generate-user-story"] button {
            background-color: #2563eb;
            border-color: #2563eb;
            color: white;
        }
        [class*="st-key-generate-markdown-stories"] button:hover,
        [class*="st-key-generate-user-story"] button:hover {
            background-color: #1d4ed8;
            border-color: #1d4ed8;
            color: white;
        }
        [class*="st-key-generate-markdown-stories"] button:disabled,
        [class*="st-key-generate-user-story"] button:disabled {
            background-color: #93c5fd;
            border-color: #93c5fd;
            color: white;
            opacity: 1;
        }
        [class*="st-key-story-title"] [data-testid="stTextInputRootElement"] {
            border: 1px solid #2563eb !important;
            box-shadow: none !important;
        }
        [class*="st-key-story-title"] [data-testid="stTextInputRootElement"]:focus-within {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 1px #2563eb !important;
        }
        [class*="st-key-story-title"] input {
            border: none !important;
            box-shadow: none !important;
            outline: none !important;
        }
        [class*="st-key-rename-story-group-title-"] [data-testid="stTextInputRootElement"] {
            border: 1px solid #2563eb !important;
            box-shadow: none !important;
        }
        [class*="st-key-rename-story-group-title-"] [data-testid="stTextInputRootElement"]:focus-within {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 1px #2563eb !important;
        }
        [class*="st-key-rename-story-group-title-"] input {
            border: none !important;
            box-shadow: none !important;
            outline: none !important;
        }
        [class*="st-key-story-text"] [data-testid="stTextAreaRootElement"]:focus-within {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 1px #2563eb !important;
        }
        [class*="st-key-story-group-filter"] [role="group"]:focus-within,
        [class*="st-key-story-group-filter"] [role="group"]:hover,
        [class*="st-key-status-filter"] [role="group"]:focus-within,
        [class*="st-key-status-filter"] [role="group"]:hover,
        [class*="st-key-category-filter"] [role="group"]:focus-within,
        [class*="st-key-category-filter"] [role="group"]:hover,
        [class*="st-key-export-group-scope"] [role="group"]:focus-within,
        [class*="st-key-export-group-scope"] [role="group"]:hover {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 1px #2563eb !important;
        }
        [class*="st-key-export-group-scope"] [data-testid="stWidgetLabel"] p {
            font-size: 1.05rem;
            font-weight: 600;
        }
        [role="listbox"] [role="option"]:hover,
        [role="listbox"] [role="option"][data-focused="true"],
        [role="listbox"] [role="option"][aria-selected="true"] {
            background-color: #eff6ff !important;
            color: #1d4ed8 !important;
        }
        [class*="st-key-edit-case-option-"] [data-testid="stExpander"] summary {
            border: 1px solid #93c5fd;
            border-radius: 0.5rem;
            background-color: #eff6ff;
            color: #1d4ed8;
            font-size: 1.05rem;
            font-weight: 600;
            padding: 0.65rem 0.85rem;
        }
        [class*="st-key-edit-case-option-"] [data-testid="stExpander"] summary:hover {
            border-color: #2563eb;
            background-color: #dbeafe;
        }
        [class*="st-key-metric-divider-after-total"] {
            border-right: 1px solid rgba(128, 128, 128, 0.45);
            min-height: 3.5rem;
            padding-right: 1rem;
        }
        [class*="st-key-metric-divider-after-rejected"] {
            border-right: 1px solid rgba(128, 128, 128, 0.45);
            min-height: 3.5rem;
            padding-right: 1rem;
        }
        [data-testid="stTabs"] [data-testid="stTab"]:hover,
        [data-testid="stTabs"] [data-testid="stTab"]:focus,
        [data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {
            color: #2563eb !important;
        }
        [data-testid="stTabs"] [data-testid="stTab"] .react-aria-SelectionIndicator {
            background-color: #2563eb !important;
            border-color: #2563eb !important;
        }
        [class*="st-key-warning-highlight-"] [data-testid="stAlert"] {
            background-color: #eff6ff;
            border: 1px solid #bfdbfe;
            color: #1e40af;
        }
        [class*="st-key-warning-highlight-"] [data-testid="stAlert"] svg {
            fill: #2563eb;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Test Generator")
    upload_tab, write_tab = st.tabs(
        ["Upload Markdown", "Write or paste"],
        key="test-generator-tabs",
    )
    with upload_tab:
        uploaded_stories = st.file_uploader(
            "Upload a Markdown file containing user stories",
            type=["md", "markdown"],
            key="user-stories-file",
        )
        st.button(
            "Generate from Markdown",
            disabled=(
                uploaded_stories is None
                or st.session_state.get("markdown_generation_pending", False)
            ),
            key="generate-markdown-stories",
            type="primary",
            on_click=_queue_markdown_generation,
        )
        if st.session_state.get("markdown_generation_pending", False):
            try:
                uploaded_stories = st.session_state.get("user-stories-file")
                if uploaded_stories is None:
                    raise ValueError("Upload a Markdown file before generating tests.")
                stories = _stories_from_markdown_upload(
                    uploaded_stories.getvalue()
                )
                with st.spinner("Generating test cases..."):
                    saved_items = generate_and_save_stories(
                        stories,
                        group_title_prefix=Path(uploaded_stories.name).stem,
                    )
            except (
                PartialGenerationError,
            ) as error:
                saved_count = len(error.saved_items)
                saved_noun = "test case" if saved_count == 1 else "test cases"
                status = (
                    f"Saved {saved_count} {saved_noun}; generated cases are available "
                    "in Test Review. "
                    if saved_count
                    else "No test cases could be saved. "
                )
                st.session_state["markdown_generation_feedback"] = (
                    "warning",
                    f"Generation incomplete. {status}{error}",
                )
            except (
                APIError,
                requests.RequestException,
                RuntimeError,
                ValueError,
                ValidationError,
                sqlite3.Error,
            ) as error:
                st.session_state["markdown_generation_feedback"] = (
                    "error",
                    f"Could not generate tests from Markdown: {error}",
                )
            else:
                st.session_state["markdown_generation_feedback"] = (
                    "success",
                    f"Saved {len(saved_items)} cases from {len(stories)} stories "
                    "to the review queue.",
                )
            finally:
                del st.session_state["markdown_generation_pending"]
            st.rerun()

        markdown_generation_feedback = st.session_state.pop(
            "markdown_generation_feedback", None
        )
        if markdown_generation_feedback:
            level, message = markdown_generation_feedback
            getattr(st, level)(message)

    with write_tab:
        with st.form("pasted-user-story"):
            st.text_input(
                "Story title",
                placeholder="e.g. Password reset",
                max_chars=MAX_STORY_TITLE_CHARS,
                key="story-title",
            )
            st.text_area(
                "User story",
                placeholder=(
                    "As a user, I want to reset my password, "
                    "so that I can regain access to my account."
                ),
                height=180,
                max_chars=MAX_STORY_TEXT_CHARS,
                help=f"Maximum {MAX_STORY_TEXT_CHARS} characters.",
                key="story-text",
            )
            st.form_submit_button(
                "Generate from user story",
                use_container_width=True,
                key="generate-user-story",
                type="primary",
                disabled=st.session_state.get("story_generation_pending", False),
                on_click=_queue_story_generation,
            )
        if st.session_state.get("story_generation_pending", False):
            try:
                story = story_from_text(
                    st.session_state["story-title"],
                    st.session_state.get("story-text", ""),
                )
                with st.spinner("Generating test cases..."):
                    saved_items = generate_and_save_stories([story])
            except (
                PartialGenerationError,
            ) as error:
                saved_count = len(error.saved_items)
                saved_noun = "test case" if saved_count == 1 else "test cases"
                status = (
                    f"Saved {saved_count} {saved_noun}; generated cases are available "
                    "in Test Review. "
                    if saved_count
                    else "No test cases could be saved. "
                )
                st.session_state["story_generation_feedback"] = (
                    "warning",
                    f"Generation incomplete. {status}{error}",
                )
            except (
                APIError,
                requests.RequestException,
                RuntimeError,
                ValueError,
                ValidationError,
                sqlite3.Error,
            ) as error:
                st.session_state["story_generation_feedback"] = (
                    "error",
                    f"Could not generate tests from user story: {error}",
                )
            else:
                st.session_state["story_generation_feedback"] = (
                    "success",
                    f"Saved {len(saved_items)} cases for {story.title} "
                    "to the review queue.",
                )
            finally:
                del st.session_state["story_generation_pending"]
            st.rerun()

        story_generation_feedback = st.session_state.pop(
            "story_generation_feedback", None
        )
        if story_generation_feedback:
            level, message = story_generation_feedback
            getattr(st, level)(message)

    with st.expander("Generate API tests from an OpenAPI spec"):
        uploaded_spec = st.file_uploader(
            "Upload an OpenAPI 3 YAML or JSON file",
            type=["yaml", "yml", "json"],
            key="openapi-spec",
        )
        if st.button(
            "Generate API tests",
            disabled=uploaded_spec is None,
            key="generate-openapi",
        ):
            try:
                spec_text = uploaded_spec.getvalue().decode("utf-8")
                operations = parse_openapi(spec_text)
                generated_items = generate_for_openapi(operations)
                saved_items = save_review_items(generated_items)
            except (
                UnicodeDecodeError,
                ValueError,
                ValidationError,
                RuntimeError,
                APIError,
                requests.RequestException,
                sqlite3.Error,
            ) as error:
                st.error(f"Could not generate API tests: {error}")
            else:
                st.success(f"Saved {len(saved_items)} API test cases to the review queue.")
                st.rerun()

    try:
        all_items = list_review_items()
        story_groups = list_story_groups()
    except (sqlite3.Error, ValueError) as error:
        st.error(f"Could not load the review queue or test case groups: {error}")
        st.stop()

    if story_groups:
        st.markdown("### Manage Test Case Groups")
        for group_index, group in enumerate(story_groups):
            row_style = "odd" if group_index % 2 == 0 else "even"
            with st.container(
                key=f"test-case-group-row-{row_style}-{group.id}"
            ):
                group_columns = st.columns([0.7, 0.3])
                with group_columns[0]:
                    st.markdown(f"**{group.title}**")
                    st.caption(
                        f"{group.case_count} tests · {group.accepted_count} accepted · "
                        f"{group.pending_count} pending · {group.rejected_count} rejected"
                    )
                with group_columns[1]:
                    action_buttons = st.columns(2, gap="small")
                    with action_buttons[0].container(
                        key=f"rename-story-group-{group.id}"
                    ):
                        rename_group_clicked = st.button(
                            "Rename",
                            icon=":material/edit:",
                            key=f"rename-group-{group.id}",
                            use_container_width=True,
                        )
                    with action_buttons[1].container(
                        key=f"delete-story-group-{group.id}"
                    ):
                        delete_group_clicked = st.button(
                            "Delete",
                            icon=":material/delete:",
                            key=f"delete-group-{group.id}",
                            use_container_width=True,
                        )

            if rename_group_clicked:
                title_key = f"rename-story-group-title-{group.id}"
                st.session_state[title_key] = group.title
                st.session_state["rename_story_group_id"] = group.id
                st.rerun()

            if st.session_state.get("rename_story_group_id") == group.id:
                title_key = f"rename-story-group-title-{group.id}"
                st.text_input(
                    "Test Case Group name",
                    key=title_key,
                    max_chars=200,
                )
                rename_columns = st.columns(2)
                if rename_columns[0].button(
                    "Save new name",
                    key=f"save-group-name-{group.id}",
                    use_container_width=True,
                ):
                    try:
                        rename_story_group(group.id, st.session_state[title_key])
                    except (KeyError, ValueError, sqlite3.Error) as error:
                        st.error(f"Could not rename test case group: {error}")
                    else:
                        del st.session_state["rename_story_group_id"]
                        del st.session_state[title_key]
                        st.rerun()
                if rename_columns[1].button(
                    "Cancel",
                    key=f"cancel-group-name-{group.id}",
                    use_container_width=True,
                ):
                    del st.session_state["rename_story_group_id"]
                    del st.session_state[title_key]
                    st.rerun()

            if delete_group_clicked:
                st.session_state["delete_story_group_id"] = group.id
                st.rerun()

            if st.session_state.get("delete_story_group_id") == group.id:
                accepted_noun = "test" if group.accepted_count == 1 else "tests"
                case_noun = "test" if group.case_count == 1 else "tests"
                st.error(
                    f"Permanently delete “{group.title}” and all "
                    f"{group.case_count} {case_noun}, including "
                    f"{group.accepted_count} accepted {accepted_noun}? "
                    "This cannot be undone."
                )
                group_confirmation = st.columns(2)
                confirm_group_delete = group_confirmation[0].button(
                    "Delete group permanently",
                    icon=":material/delete_forever:",
                    key=f"confirm-delete-group-{group.id}",
                    use_container_width=True,
                )
                cancel_group_delete = group_confirmation[1].button(
                    "Cancel",
                    key=f"cancel-delete-group-{group.id}",
                    use_container_width=True,
                )
                if confirm_group_delete:
                    try:
                        delete_story_group(group.id)
                    except (KeyError, sqlite3.Error) as error:
                        st.error(f"Could not delete test case group: {error}")
                    else:
                        del st.session_state["delete_story_group_id"]
                        st.rerun()
                if cancel_group_delete:
                    del st.session_state["delete_story_group_id"]
                    st.rerun()

    counts = {
        status: sum(item.status == status for item in all_items)
        for status in Status
    }
    reviewed = counts[Status.ACCEPTED] + counts[Status.REJECTED]
    st.subheader("Test Review")
    metrics = st.columns(5)
    with metrics[0].container(key="metric-divider-after-total"):
        st.metric("Total", len(all_items))
    metrics[1].metric("Accepted", counts[Status.ACCEPTED])
    metrics[2].metric("Pending", counts[Status.PENDING])
    with metrics[3].container(key="metric-divider-after-rejected"):
        st.metric("Rejected", counts[Status.REJECTED])
    with metrics[4].container(key="metric-divider-after-manually-edited"):
        st.metric("Manually edited", sum(item.manually_edited for item in all_items))
    st.progress(reviewed / len(all_items) if all_items else 0)
    st.caption(f"{reviewed}/{len(all_items)} cases reviewed")

    filter_columns = st.columns(3)
    story_group_names = {group.id: group.title for group in story_groups}
    with filter_columns[0].container(key="story-group-filter"):
        selected_group_id = st.selectbox(
            "Filter by Test Case Group",
            ["all", *story_group_names],
            format_func=lambda group_id: (
                "All Test Case Groups"
                if group_id == "all"
                else story_group_names[group_id]
            ),
            key="review-story-group-filter",
        )
    with filter_columns[1].container(key="status-filter"):
        status_selection = st.selectbox(
            "Filter by status",
            ["all", *(status.value for status in Status)],
            format_func=lambda value: (
                "All statuses" if value == "all" else value.title()
            ),
            key="review-status-filter",
        )
        status_filter = None if status_selection == "all" else Status(status_selection)
    with filter_columns[2].container(key="category-filter"):
        category_selection = st.selectbox(
            "Filter by category",
            ["all", *(category.value for category in Category)],
            format_func=lambda value: (
                "All categories" if value == "all" else value.title()
            ),
            key="review-category-filter",
        )
        category_filter = (
            None if category_selection == "all" else Category(category_selection)
        )

    visible_items = [
        item
        for item in all_items
        if (
            selected_group_id == "all"
            or item.group_id == selected_group_id
        )
        and _matches_review_filters(item, status_filter, category_filter)
    ]

    if visible_items:
        st.caption(f"Showing {len(visible_items)} of {len(all_items)} test cases")
        for item in visible_items:
            _render_item(item)
    elif not all_items:
        st.info("No test cases have been generated yet. Generate tests above to get started.")
    else:
        st.info(
            "No test cases match the selected test case group, status, and category. "
            "Try changing one or more filters."
        )

    st.markdown("#### Export approved tests")
    with st.container(key="export-scope"):
        export_scope = st.selectbox(
            "Export scope",
            [None, *story_groups],
            format_func=lambda group: (
                "All approved tests" if group is None else group.title
            ),
            key="export-group-scope",
        )
    export_items = (
        all_items
        if export_scope is None
        else [item for item in all_items if item.group_id == export_scope.id]
    )
    exportable_count = sum(item.status == Status.ACCEPTED for item in export_items)
    if exportable_count:
        st.caption(
            f"{exportable_count} accepted cases "
            f"{'in all groups' if export_scope is None else f'in {export_scope.title}'} "
            "will be included."
        )
    else:
        st.info("Accept cases before exporting.")
    export_columns = st.columns(3)
    export_columns[0].download_button(
        "Download JSON",
        data=export_json(export_items),
        file_name="approved_test_cases.json",
        mime="application/json",
        disabled=exportable_count == 0,
        use_container_width=True,
    )
    export_columns[1].download_button(
        "Download CSV",
        data=export_csv(export_items),
        file_name="approved_test_cases.csv",
        mime="text/csv",
        disabled=exportable_count == 0,
        use_container_width=True,
    )
    export_columns[2].download_button(
        "Download Markdown",
        data=export_markdown(export_items),
        file_name="approved_test_cases.md",
        mime="text/markdown",
        disabled=exportable_count == 0,
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
