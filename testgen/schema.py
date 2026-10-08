from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class Category(str, Enum):
    FUNCTIONAL = "functional"
    NEGATIVE = "negative"
    EDGE = "edge"
    BOUNDARY = "boundary"
    API = "api"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ApiDetails(BaseModel):
    method: str = Field(description="HTTP method, e.g. GET, POST")
    path: str = Field(description="Endpoint path, e.g. /users/{id}")
    request_body: Optional[dict] = None
    expected_status: int


class TestCase(BaseModel):
    title: str = Field(min_length=5)
    category: Category
    priority: Priority = Priority.MEDIUM
    source_ref: str = Field(description="ID of the user story / endpoint this came from")
    preconditions: list[str] = []
    steps: list[str] = Field(min_length=1)
    expected_results: list[str] = Field(min_length=1)
    test_data: Optional[dict] = None
    api: Optional[ApiDetails] = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_expected_result(cls, values):
        if not isinstance(values, dict) or "expected_results" in values:
            return values
        legacy_result = values.pop("expected_result", None)
        if legacy_result is None:
            return values

        steps = values.get("steps", [])
        values["expected_results"] = [
            *(["No separate result specified."] * max(len(steps) - 1, 0)),
            legacy_result,
        ]
        return values

    @model_validator(mode="after")
    def validate_expected_results_match_steps(self):
        if len(self.expected_results) != len(self.steps):
            raise ValueError(
                "Each test step must have exactly one expected result "
                f"({len(self.steps)} steps, {len(self.expected_results)} results)."
            )
        if any(len(result.strip()) < 5 for result in self.expected_results):
            raise ValueError("Each expected result must be at least 5 characters.")
        return self


class StoryGroup(BaseModel):
    id: str
    title: str
    source_ref: str
    story_text: str = ""
    domain: str = ""
    created_at: str
    case_count: int = 0
    accepted_count: int = 0
    pending_count: int = 0
    rejected_count: int = 0
    manually_edited_count: int = 0


class TestCaseSet(BaseModel):
    """What we ask the LLM to return: a list of test cases."""
    test_cases: list[TestCase]


class ReviewItem(BaseModel):
    """A test case plus the review/validation state around it."""
    id: str
    test_case: TestCase
    status: Status = Status.PENDING
    manually_edited: bool = False
    group_id: Optional[str] = None
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_edited_status(cls, values):
        if isinstance(values, dict) and values.get("status") == "edited":
            values["status"] = Status.PENDING
            values["manually_edited"] = True
        return values