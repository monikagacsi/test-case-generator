import json
from pathlib import Path

import pytest

import testgen.generate as generator
from testgen.generate import generate_for_openapi, generate_for_openapi_operation
from testgen.openapi import load_openapi_file, parse_openapi
from testgen.schema import Category, ReviewItem, TestCase as Case
from testgen.storage import list_review_items


YAML_SPEC = """
openapi: 3.0.3
info:
  title: Pet API
  version: 1.0.0
paths:
  /pets/{petId}:
    parameters:
      - name: petId
        in: path
        required: true
        schema:
          type: integer
    get:
      summary: Get a pet
      operationId: getPet
      tags: [pets]
      responses:
        "200":
          description: Pet found
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pet"
        "404":
          description: Pet not found
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                on:
                  type: boolean
    delete:
      summary: Delete a pet
      responses:
        "204":
          description: Pet deleted
components:
  schemas:
    Pet:
      type: object
      properties:
        id:
          type: integer
        name:
          type: string
"""


def test_parse_openapi_yaml_extracts_operations_and_success_statuses():
    operations = parse_openapi(YAML_SPEC)

    assert [operation.source_ref for operation in operations] == [
        "API GET /pets/{petId}",
        "API DELETE /pets/{petId}",
    ]
    assert operations[0].expected_status == 200
    assert operations[0].parameters[0]["name"] == "petId"
    assert operations[0].summary == "Get a pet"
    assert "on" in operations[0].request_body["content"]["application/json"]["schema"]["properties"]
    assert "name" in operations[0].responses["200"]["content"]["application/json"]["schema"]["properties"]
    assert operations[1].expected_status == 204


def test_parse_openapi_accepts_json_document():
    spec = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Users", "version": "1"},
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "responses": {"201": {"description": "Created"}},
                    }
                }
            },
        }
    )

    operation = parse_openapi(spec)[0]

    assert operation.method == "POST"
    assert operation.path == "/users"
    assert operation.summary == "createUser"
    assert operation.expected_status == 201


def test_load_openapi_file_reads_utf8_yaml(tmp_path):
    spec_file = tmp_path / "pets.yaml"
    spec_file.write_text(YAML_SPEC, encoding="utf-8")

    assert load_openapi_file(spec_file)[0].path == "/pets/{petId}"


def test_petstore_example_spec_loads_all_operations():
    spec_file = (
        Path(__file__).resolve().parents[1]
        / "samples"
        / "petstore_openapi.yaml"
    )

    operations = load_openapi_file(spec_file)

    assert len(operations) == 4
    assert [operation.expected_status for operation in operations] == [
        200,
        201,
        200,
        204,
    ]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("not: [valid", "Invalid OpenAPI"),
        ("[]", "mapping/object"),
        ('{"swagger":"2.0","paths":{"/x":{}}}', "OpenAPI 3.x"),
        ('{"openapi":"3.0.0","paths":{}}', "at least one path"),
        (
            '{"openapi":"3.0.0","paths":{"/x":{"get":{"responses":{"400":{"description":"Bad"}}}}}}',
            "no 2xx response",
        ),
        (
            '{"openapi":"3.0.0","paths":{"/x":{"get":{"responses":{"200":{"$ref":"#/components/responses/Missing"}}}}}}',
            "unresolved local reference",
        ),
    ],
)
def test_parse_openapi_reports_invalid_spec(document, message):
    with pytest.raises(ValueError, match=message):
        parse_openapi(document)


def test_generation_uses_operation_contract_as_api_details():
    operation = parse_openapi(YAML_SPEC)[0]
    prompts = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "test_cases": [
                    {
                        "title": "Retrieve pet by ID",
                        "category": "functional",
                        "source_ref": "ignored",
                        "steps": ["Send a GET request", "Inspect the response"],
                        "expected_result": "Pet details are returned",
                        "api": {
                            "method": "POST",
                            "path": "/incorrect",
                            "expected_status": 500,
                        },
                    }
                ]
            }
        )

    cases = generate_for_openapi_operation(operation, llm=fake_llm)

    assert "GET /pets/{petId}" in prompts[0]
    assert "petId" in prompts[0]
    assert cases[0].category == Category.API
    assert cases[0].source_ref == operation.source_ref
    assert cases[0].api.method == "GET"
    assert cases[0].api.path == "/pets/{petId}"
    assert cases[0].api.request_body == operation.request_body
    assert cases[0].api.expected_status == 200


def test_generate_for_openapi_assigns_ids_and_flags_cases():
    operation = parse_openapi(YAML_SPEC)[0]
    response = json.dumps(
        {
            "test_cases": [
                {
                    "title": "Retrieve a pet",
                    "category": "functional",
                    "source_ref": "ignored",
                    "steps": ["Send a GET request", "Inspect the response"],
                    "expected_result": "Pet details are returned",
                },
                {
                    "title": "Retrieve a pet",
                    "category": "functional",
                    "source_ref": "ignored",
                    "steps": ["Send a GET request", "Inspect the response"],
                    "expected_result": "Pet details are returned",
                },
            ]
        }
    )

    items = generate_for_openapi([operation], llm=lambda _: response)

    assert [item.id for item in items] == ["TC-001", "TC-002"]
    assert items[0].test_case.api.path == operation.path
    assert items[1].flags[0].startswith("Possible duplicate of TC-001")


def test_openapi_cli_generates_and_persists_cases(tmp_path, monkeypatch, capsys):
    operation = parse_openapi(YAML_SPEC)[0]
    item = ReviewItem(
        id="TC-001",
        test_case=Case(
            title="Retrieve a pet by ID",
            category=Category.API,
            source_ref=operation.source_ref,
            steps=["Send a GET request", "Inspect the response"],
            expected_result="Pet details are returned",
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        generator.sys,
        "argv",
        ["testgen.generate", "--openapi", "pets.yaml"],
    )
    monkeypatch.setattr(generator, "load_openapi_file", lambda path: [operation])
    monkeypatch.setattr(generator, "generate_for_openapi", lambda operations: [item])

    generator.main()

    generated = json.loads((tmp_path / "output" / "generated.json").read_text())
    assert generated[0]["test_case"]["source_ref"] == operation.source_ref
    assert list_review_items()[0].test_case.category == Category.API
    assert "1 API test cases saved" in capsys.readouterr().out
