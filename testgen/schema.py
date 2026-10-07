from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


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
    EDITED = "edited"
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
    test_data: Optional[dict] = None
    expected_result: str = Field(min_length=5)
    api: Optional[ApiDetails] = None


class TestCaseSet(BaseModel):
    """What we ask the LLM to return: a list of test cases."""
    test_cases: list[TestCase]


class ReviewItem(BaseModel):
    """A test case plus the review/validation state around it."""
    id: str
    test_case: TestCase
    status: Status = Status.PENDING
    flags: list[str] = []  # e.g. "Possible duplicate of TC-014 (0.91)"