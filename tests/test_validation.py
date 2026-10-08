from testgen.schema import Category, ReviewItem, TestCase as Case
from testgen.validation import flag_test_cases


def _item(
    item_id: str,
    title: str,
    *,
    category: Category = Category.FUNCTIONAL,
    source_ref: str = "US-01",
    steps: list[str] | None = None,
    expected_result: str = "The user sees the account dashboard",
    test_data: dict | None = None,
) -> ReviewItem:
    return ReviewItem(
        id=item_id,
        test_case=Case(
            title=title,
            category=category,
            source_ref=source_ref,
            steps=steps or ["Open the login page", "Submit valid credentials"],
            expected_result=expected_result,
            test_data=test_data,
        ),
    )


def test_flags_exact_and_near_duplicates_against_first_case():
    items = [
        _item("TC-001", "Login with valid credentials"),
        _item("TC-002", "Login with valid credentials"),
        _item(
            "TC-003",
            "Login using valid credentials",
            steps=["Open the login page", "Submit valid credentials"],
        ),
    ]

    flagged = flag_test_cases(items)

    assert flagged[0].flags == []
    assert flagged[1].flags == [
        "Possible duplicate of TC-001 (1.00 similarity)"
    ]
    assert flagged[2].flags[0].startswith("Possible duplicate of TC-001")


def test_does_not_flag_similar_tests_from_different_stories():
    items = [
        _item("TC-001", "Login with valid credentials", source_ref="US-01"),
        _item("TC-002", "Login with valid credentials", source_ref="US-02"),
    ]

    assert all(not item.flags for item in flag_test_cases(items))


def test_flags_low_value_expected_result_steps_and_generic_title():
    item = _item(
        "TC-001",
        "Verify functionality",
        steps=["Verify"],
        expected_result="The login works correctly",
    )

    assert flag_test_cases([item])[0].flags == [
        "Low value: title is too generic",
        "Low value: expected result is vague",
        "Low value: fewer than 2 meaningful steps",
    ]


def test_flags_boundary_case_without_concrete_data():
    item = _item(
        "TC-001",
        "Submit maximum allowed username",
        category=Category.BOUNDARY,
        test_data={"username": "maximum"},
    )

    assert flag_test_cases([item])[0].flags == [
        "Low value: boundary test has no concrete test data"
    ]


def test_preserves_existing_flags_and_does_not_mutate_input():
    item = _item("TC-001", "Login with valid credentials").model_copy(
        update={"flags": ["Existing validation note"]}
    )

    flagged = flag_test_cases([item])

    assert flagged[0].flags == ["Existing validation note"]
    assert item.flags == ["Existing validation note"]
