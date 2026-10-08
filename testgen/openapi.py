import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class OpenAPIOperation(BaseModel):
    source_ref: str
    method: str
    path: str
    summary: str
    description: str = ""
    parameters: list[dict[str, Any]]
    request_body: dict[str, Any] | None = None
    expected_status: int
    responses: dict[str, Any]
    tags: list[str] = Field(default_factory=list)

    def prompt_details(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "description": self.description,
            "tags": self.tags,
            "parameters": self.parameters,
            "request_body": self.request_body,
            "responses": self.responses,
        }


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class _OpenAPILoader(yaml.SafeLoader):
    pass


_OpenAPILoader.yaml_implicit_resolvers = {
    initial: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
    for initial, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_OpenAPILoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def _success_status(responses: object, source_ref: str) -> int:
    if not isinstance(responses, dict):
        raise ValueError(f"{source_ref}: OpenAPI operation must define responses.")
    success_codes = sorted(
        int(code)
        for code in responses
        if str(code).isdigit() and 200 <= int(code) < 300
    )
    if not success_codes:
        raise ValueError(
            f"{source_ref}: no 2xx response is defined; cannot infer an expected "
            "success status."
        )
    return success_codes[0]


def _resolve_local_refs(
    value: Any,
    document: dict[str, Any],
    resolving: frozenset[str] = frozenset(),
) -> Any:
    if isinstance(value, list):
        return [_resolve_local_refs(item, document, resolving) for item in value]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in resolving:
            return {
                "$ref": reference,
                **{
                    key: _resolve_local_refs(item, document, resolving)
                    for key, item in value.items()
                    if key != "$ref"
                },
            }

        target: Any = document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(target, list) and token.isdecimal():
                target = target[int(token)]
            elif isinstance(target, dict) and token in target:
                target = target[token]
            else:
                raise ValueError(f"OpenAPI document has unresolved local reference {reference!r}.")

        resolved_target = _resolve_local_refs(
            target,
            document,
            resolving | {reference},
        )
        if not isinstance(resolved_target, dict):
            return resolved_target
        resolved_siblings = {
            key: _resolve_local_refs(item, document, resolving)
            for key, item in value.items()
            if key != "$ref"
        }
        return {**resolved_target, **resolved_siblings}

    return {
        key: _resolve_local_refs(item, document, resolving)
        for key, item in value.items()
    }


def parse_openapi(text: str) -> list[OpenAPIOperation]:
    """Parse OpenAPI 3 YAML or JSON into one operation per supported HTTP endpoint."""
    try:
        document = yaml.load(text, Loader=_OpenAPILoader)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid OpenAPI YAML/JSON: {error}") from error

    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must be a mapping/object.")
    version = document.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise ValueError("Only OpenAPI 3.x documents are supported.")

    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("OpenAPI document must define at least one path.")

    operations: list[OpenAPIOperation] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters", [])
        if not isinstance(path_parameters, list):
            path_parameters = []
        path_parameters = _resolve_local_refs(path_parameters, document)
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation = _resolve_local_refs(operation, document)

            normalized_method = method.upper()
            source_ref = f"API {normalized_method} {path}"
            operation_parameters = operation.get("parameters", [])
            if not isinstance(operation_parameters, list):
                operation_parameters = []
            request_body = operation.get("requestBody")
            if not isinstance(request_body, dict):
                request_body = None
            responses = operation.get("responses", {})
            success_status = _success_status(responses, source_ref)
            summary = operation.get("summary") or operation.get("operationId") or (
                f"{normalized_method} {path}"
            )
            operations.append(
                OpenAPIOperation(
                    source_ref=source_ref,
                    method=normalized_method,
                    path=str(path),
                    summary=str(summary),
                    description=str(operation.get("description", "")),
                    parameters=[*path_parameters, *operation_parameters],
                    request_body=request_body,
                    expected_status=success_status,
                    responses=responses,
                    tags=operation.get("tags", []),
                )
            )

    if not operations:
        raise ValueError("OpenAPI document does not contain any supported operations.")
    return operations


def load_openapi_file(path: str | Path) -> list[OpenAPIOperation]:
    return parse_openapi(Path(path).read_text(encoding="utf-8"))
