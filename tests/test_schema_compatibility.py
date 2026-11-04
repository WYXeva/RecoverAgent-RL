"""P0.1 software contracts only: synthetic execution and frozen public API schemas."""

import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError, SchemaError

from recoveragent import APPWORLD_REVISION, schema_compatibility as compat, student
from recoveragent.adapter import AppWorldAdapter, call_code
from recoveragent.harness import ProtocolViolation, check_batch, RULES
from recoveragent.io import digest, read_json
from recoveragent.student import StudentSession
from test_student_batch import Tokenizer, block, receipt

FIXTURE = Path(__file__).parent / "fixtures/appworld_nullable_contract_v2.json"


@pytest.fixture
def public_contract():
    return read_json(FIXTURE)


def convert(fixture):
    return compat.convert_catalogue(
        fixture["upstream_catalogue"],
        fixture["openapi"],
        appworld_revision=fixture["appworld_revision"],
    )


def function(tools, name):
    return next(t for t in tools if t["function"]["name"] == name)


def message(name, args, call_id="test_call"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def synthetic(base, *, nullable=True, required=False):
    original = (
        {"anyOf": [copy.deepcopy(base), {"type": "null"}]}
        if nullable
        else copy.deepcopy(base)
    )
    raw = copy.deepcopy(base)
    if not required:
        raw["default"] = (
            None  # Also test misleading null defaults on NON-nullable types.
        )
    return {
        "appworld_revision": APPWORLD_REVISION,
        "upstream_catalogue": [
            {
                "type": "function",
                "function": {
                    "name": "demo__write",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": raw},
                        "required": ["value"] if required else [],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "openapi": {
            "demo": {
                "openapi": "3.1.0",
                "paths": {
                    "/write": {
                        "post": {
                            "summary": "Write",
                            "description": "Public synthetic contract",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"value": original},
                                            "required": ["value"] if required else [],
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }
        },
    }


def test_pinned_gmail_null_passes_both_strict_validation_layers(public_contract):
    name = "gmail__show_category_sizes"
    args = {"access_token": "synthetic_token", "read": None}
    with pytest.raises(ValidationError):
        call_code(name, args, public_contract["upstream_catalogue"])
    tools = convert(public_contract)["compatible_catalogue"]
    code, parsed = call_code(name, args, tools)
    assert parsed == args and "None" in code
    check_batch(message(name, args), [{"role": "user", "content": "synthetic"}], tools)
    shown = function(tools, name)["function"]["parameters"]
    assert shown["additionalProperties"] is False
    Draft202012Validator(shown).validate(args)


@pytest.mark.parametrize(
    "field,args",
    [
        ("access_token", {"access_token": None, "read": True}),
        ("access_token", {"read": None}),
        ("unknown", {"access_token": "synthetic", "extra": 1}),
    ],
)
def test_public_auth_required_and_extra_arguments_stay_strict(
    public_contract, field, args
):
    tools = convert(public_contract)["compatible_catalogue"]
    with pytest.raises(ValidationError):
        call_code("gmail__show_category_sizes", args, tools)
    with pytest.raises(ProtocolViolation, match="invalid_arguments"):
        check_batch(message("gmail__show_category_sizes", args), [], tools)


@pytest.mark.parametrize(
    "kind,good,bad",
    [
        ("boolean", True, "true"),
        ("boolean", False, 1),
        ("string", "value", 1),
        ("integer", 1, "1"),
        ("integer", 1, True),
        ("integer", 1, 1.5),
        ("array", [], {}),
        ("object", {}, []),
    ],
)
@pytest.mark.parametrize("nullable", [False, True])
def test_all_five_types_and_nullability_remain_distinct(kind, good, bad, nullable):
    tools = convert(synthetic({"type": kind}, nullable=nullable))[
        "compatible_catalogue"
    ]
    call_code("demo__write", {"value": good}, tools)
    with pytest.raises(ValidationError):
        call_code("demo__write", {"value": bad}, tools)
    if nullable:
        call_code("demo__write", {"value": None}, tools)
    else:
        with pytest.raises(ValidationError):
            call_code("demo__write", {"value": None}, tools)


def test_required_nullable_accepts_null_but_never_missing():
    tools = convert(synthetic({"type": "string"}, required=True))[
        "compatible_catalogue"
    ]
    assert tools[0]["function"]["parameters"]["required"] == ["value"]
    call_code("demo__write", {"value": None}, tools)
    with pytest.raises(ValidationError):
        call_code("demo__write", {}, tools)


@pytest.mark.parametrize(
    "base,good,bad",
    [
        ({"type": "string", "enum": ["a", "b"]}, "a", "c"),
        ({"type": "integer", "minimum": 1, "maximum": 3}, 2, 0),
        ({"type": "integer", "minimum": 1, "maximum": 3}, 2, 4),
        ({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 2}, 1, 0),
        ({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 2}, 1, 2),
        ({"type": "string", "pattern": "^ok$"}, "ok", "wrong"),
        ({"type": "array", "items": {"type": "integer"}, "maxItems": 1}, [1], ["x"]),
        ({"type": "array", "items": {"type": "integer"}, "maxItems": 1}, [1], [1, 2]),
        (
            {
                "type": "object",
                "required": ["n"],
                "properties": {"n": {"type": "integer"}},
                "additionalProperties": False,
            },
            {"n": 1},
            {"n": 1, "extra": 0},
        ),
        (
            {
                "type": "object",
                "required": ["n"],
                "properties": {"n": {"type": "integer"}},
                "additionalProperties": False,
            },
            {"n": 1},
            {},
        ),
    ],
)
def test_original_constraints_preserved_verbatim_for_non_null_values(base, good, bad):
    source = synthetic(base)
    result = convert(source)
    tools = result["compatible_catalogue"]
    converted = tools[0]["function"]["parameters"]["properties"]["value"]
    raw = source["upstream_catalogue"][0]["function"]["parameters"]["properties"][
        "value"
    ]
    assert converted["anyOf"][0] == {
        k: v for k, v in raw.items() if k not in {"description", "default"}
    }
    assert converted["default"] is None
    for value in (None, good):
        call_code("demo__write", {"value": value}, tools)
    with pytest.raises(ValidationError):
        call_code("demo__write", {"value": bad}, tools)


def test_real_nullable_enum_retains_allowed_values(public_contract):
    tools = convert(public_contract)["compatible_catalogue"]
    name = "venmo__show_sent_payment_requests"
    parameters = function(tools, name)["function"]["parameters"]
    values = parameters["properties"]["status"]["anyOf"][0]["enum"]
    for value in [None, *values]:
        call_code(name, {"access_token": "synthetic", "status": value}, tools)
    with pytest.raises(ValidationError):
        call_code(name, {"access_token": "synthetic", "status": "invented"}, tools)


@pytest.mark.parametrize(
    "declaration",
    [
        {},
        {"default": None},
        {"type": "Mystery"},
        {"nullable": True, "type": "string"},
        {"oneOf": [{"type": "string"}, {"type": "null"}]},
        {"anyOf": [{"type": "null"}, {}]},
        {"$ref": "https://invalid.example/type.json"},
        {"$ref": "#/components/schemas/Missing"},
    ],
)
def test_unproven_declarations_fail_closed(declaration):
    fixture = synthetic({"type": "string"})
    fixture["openapi"]["demo"]["paths"]["/write"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]["value"] = declaration
    with pytest.raises((compat.SchemaCompatibilityError, SchemaError)):
        convert(fixture)


def test_local_refs_resolve_but_cycles_and_ref_siblings_do_not():
    fixture = synthetic({"type": "boolean"})
    doc = fixture["openapi"]["demo"]
    props = doc["paths"]["/write"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]
    doc["components"] = {"schemas": {"Maybe": props["value"]}}
    props["value"] = {"$ref": "#/components/schemas/Maybe"}
    call_code("demo__write", {"value": None}, convert(fixture)["compatible_catalogue"])
    doc["components"]["schemas"]["Maybe"] = copy.deepcopy(props["value"])
    with pytest.raises(compat.SchemaCompatibilityError, match="cyclic"):
        convert(fixture)
    props["value"]["type"] = "string"
    with pytest.raises(compat.SchemaCompatibilityError):
        convert(fixture)


@pytest.mark.parametrize(
    "damage",
    [
        "revision",
        "default_only",
        "source_drift",
        "unknown_function",
        "duplicate",
        "private",
        "root_extras",
    ],
)
def test_contract_mismatch_never_silently_falls_back(public_contract, damage):
    fixture = public_contract
    if damage == "revision":
        fixture["appworld_revision"] = "wrong"
    elif damage == "default_only":
        raw = function(fixture["upstream_catalogue"], "gmail__show_category_sizes")
        raw["function"]["parameters"]["properties"]["read"].pop("type")
    elif damage == "source_drift":
        raw = function(fixture["upstream_catalogue"], "gmail__show_category_sizes")
        raw["function"]["parameters"]["properties"]["read"]["type"] = "string"
    elif damage == "unknown_function":
        fixture["upstream_catalogue"][0]["function"]["name"] = "gmail__missing"
    elif damage == "duplicate":
        fixture["upstream_catalogue"].append(fixture["upstream_catalogue"][0])
    elif damage == "private":
        fixture["openapi"]["gmail"]["paths"]["/category_sizes"]["get"][
            "description"
        ] = "Private-API"
    else:
        fixture["upstream_catalogue"][0]["function"]["parameters"][
            "additionalProperties"
        ] = True
    with pytest.raises(compat.SchemaCompatibilityError):
        convert(fixture)


def test_source_and_transformed_hashes_are_reproducible_without_input_mutation(
    public_contract,
):
    before = copy.deepcopy(public_contract)
    result = convert(public_contract)
    assert public_contract == before
    assert result["upstream_catalogue"] == before["upstream_catalogue"]
    assert (
        result["upstream_sha256"]
        == digest(result["upstream_catalogue"])
        == before["upstream_sha256"]
    )
    assert (
        result["compatible_sha256"]
        == digest(result["compatible_catalogue"])
        == before["expected_compatible_sha256"]
    )
    assert result["compatible_sha256"] != result["upstream_sha256"]
    assert digest(before["openapi"]) == before["openapi_sha256"]
    assert result["public_openapi_sha256"] == {
        k: digest(v) for k, v in before["openapi"].items()
    }
    for change in result["changes"]:
        assert change["public_parameter_sha256"] == digest(
            change["public_parameter_schema"]
        )
    result["compatible_catalogue"].clear()
    assert public_contract == before


def test_fixture_is_bound_to_installed_pinned_public_source_files(public_contract):
    # Files only: never import/instantiate an AppWorld task or call an API.
    distribution = importlib.metadata.distribution("appworld")
    direct = json.loads(distribution.read_text("direct_url.json"))
    assert direct["url"].endswith(APPWORLD_REVISION)
    for evidence in public_contract["sources"].values():
        path = distribution.locate_file(evidence["module"].replace(".", "/") + ".py")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]


class SyntheticAdapter(AppWorldAdapter):
    def __init__(self, tools):
        self.catalogue = copy.deepcopy(tools)
        self.versions = {"revision": APPWORLD_REVISION, "data_version": "synthetic"}
        self.calls = []
        self.world = SimpleNamespace(
            execute=self._execute, task_completed=lambda: False
        )

    def _execute(self, code):
        self.calls.append(code)
        return "{}"


@pytest.fixture
def prepared_adapter(public_contract, monkeypatch, tmp_path):
    monkeypatch.setattr(
        compat,
        "load_public_openapi",
        lambda catalogue: (
            copy.deepcopy(public_contract["openapi"]),
            {"synthetic_loader": True},
        ),
    )
    adapter = SyntheticAdapter(public_contract["upstream_catalogue"])
    adapter.configure_schema_compatibility(compat.SCHEMA_REVISION, tmp_path / "audit")
    return adapter


def make_student(adapter, monkeypatch, tmp_path):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    return StudentSession(
        adapter,
        Tokenizer(),
        [
            {"role": "system", "content": RULES},
            {"role": "user", "content": "synthetic"},
        ],
        adapter.initial_tools(),
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        output=tmp_path,
    )


def test_initial_and_dynamic_tools_match_requests_and_validation(
    prepared_adapter, monkeypatch, tmp_path
):
    adapter = prepared_adapter
    session = make_student(adapter, monkeypatch, tmp_path / "student")
    initial = session.build_request()["tools"]
    assert all(
        t["function"]["parameters"]["additionalProperties"] is False for t in initial
    )
    complete = function(initial, "supervisor__complete_task")
    assert Draft202012Validator(complete["function"]["parameters"]).is_valid(
        {"answer": None}
    )
    # A discovery cannot unlock another call in its own batch.
    rejected = session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
            + block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": None},
            )
        )
    )
    assert rejected["protocol_rejection"] == "invisible_tool" and adapter.calls == []
    session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    actual = session.build_request()["tools"]
    assert function(actual, "gmail__show_category_sizes") == function(
        adapter.catalogue, "gmail__show_category_sizes"
    )
    args = {"access_token": "synthetic", "read": None}
    event = session.step(receipt(block("gmail__show_category_sizes", args)))
    assert event["dispatched_count"] == 1 and len(adapter.calls) == 2
    shown = function(actual, "gmail__show_category_sizes")["function"]["parameters"]
    Draft202012Validator(shown).validate(args)
    assert session.tools == actual
    # An invalid suffix still prevents the legitimate prefix from executing.
    before = list(adapter.calls)
    event = session.step(
        receipt(
            block("gmail__show_category_sizes", args)
            + block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": "wrong"},
            )
        )
    )
    assert (
        event["protocol_rejection"] == "invalid_arguments" and adapter.calls == before
    )


def test_persistence_precedes_activation_and_legacy_revision_is_unchanged(
    public_contract, monkeypatch, tmp_path
):
    adapter = SyntheticAdapter(public_contract["upstream_catalogue"])
    monkeypatch.setattr(
        compat,
        "load_public_openapi",
        lambda catalogue: (public_contract["openapi"], {"fixture": True}),
    )
    original = copy.deepcopy(adapter.catalogue)
    assert adapter.configure_schema_compatibility(None, tmp_path) is None
    assert adapter.catalogue == original and not list(tmp_path.iterdir())
    with pytest.raises(compat.SchemaCompatibilityError, match="Unknown"):
        adapter.configure_schema_compatibility("unknown", tmp_path)
    adapter.configure_schema_compatibility(compat.SCHEMA_REVISION, tmp_path)
    audit = read_json(tmp_path / "schema_compatibility.json")
    assert audit["upstream_catalogue"] == original == adapter.upstream_catalogue
    assert audit["compatible_catalogue"] == adapter.catalogue
    assert adapter.versions["schema_compatibility"]["audit_sha256"] == digest(audit)
    other = SyntheticAdapter(original)
    with pytest.raises(FileExistsError):
        other.configure_schema_compatibility(compat.SCHEMA_REVISION, tmp_path)
    assert other.catalogue == original and not hasattr(other, "schema_compatibility")
    with pytest.raises(compat.SchemaCompatibilityError, match="already"):
        adapter.configure_schema_compatibility(
            compat.SCHEMA_REVISION, tmp_path / "again"
        )


def test_teacher_runner_and_student_share_identical_initial_and_dynamic_schemas(
    public_contract, monkeypatch, tmp_path
):
    import test_harness
    from test_runner import Adapter as TeacherWorld
    from test_teacher_data import response
    from recoveragent.teacher import TeacherPolicy

    monkeypatch.setattr(
        compat,
        "load_public_openapi",
        lambda catalogue: (
            copy.deepcopy(public_contract["openapi"]),
            {"fixture": True},
        ),
    )
    teacher_config = test_harness.config()
    teacher_config[compat.CONFIG_KEY] = compat.SCHEMA_REVISION
    monkeypatch.setattr(test_harness, "config", lambda: copy.deepcopy(teacher_config))
    original_init = TeacherWorld.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.catalogue = copy.deepcopy(public_contract["upstream_catalogue"])
        self.calls = []

        def execute(code):
            self.calls.append(code)
            self.completed = "supervisor__complete_task" in code
            return "{}"

        self.world.execute = execute

    monkeypatch.setattr(TeacherWorld, "__init__", init)
    monkeypatch.setattr(TeacherWorld, "initial_tools", AppWorldAdapter.initial_tools)
    monkeypatch.setattr(TeacherWorld, "execute", AppWorldAdapter.execute)
    monkeypatch.setattr(
        TeacherWorld,
        "configure_schema_compatibility",
        AppWorldAdapter.configure_schema_compatibility,
        raising=False,
    )
    captured = []

    def policy(config, credentials, *, transport):
        def capture(request):
            captured.append(copy.deepcopy(request))
            return transport(request)

        return TeacherPolicy(config, credentials, transport=capture)

    monkeypatch.setattr(test_harness, "TeacherPolicy", policy)
    replies = []
    batches = [
        [
            (
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        ],
        [
            ("gmail__show_category_sizes", {"access_token": "synthetic", "read": None}),
            ("supervisor__complete_task", {"answer": None}),
        ],
    ]
    for i, calls in enumerate(batches):
        reply = response()
        reply["choices"][0]["message"]["tool_calls"] = [
            message(n, a, f"c_{i}_{j}")["tool_calls"][0]
            for j, (n, a) in enumerate(calls)
        ]
        replies.append(reply)
    _, teacher, _ = test_harness.collect_synthetic(tmp_path, monkeypatch, replies)
    assert teacher["status"] == "valid", teacher.get("error_type")
    assert teacher["usage"]["tool_calls"] == 3
    assert teacher["harness"]["rejections"] == 0
    adapter = SyntheticAdapter(public_contract["upstream_catalogue"])
    adapter.configure_schema_compatibility(
        compat.SCHEMA_REVISION, tmp_path / "student_audit"
    )
    s = make_student(adapter, monkeypatch, tmp_path / "student")
    assert (
        s.build_request()["tools"] == captured[0]["tools"] == teacher["initial_tools"]
    )
    s.step(receipt(block(*batches[0][0])))
    assert (
        s.build_request()["tools"]
        == captured[1]["tools"]
        == teacher["turns"][1]["tools"]
    )
    event = s.step(receipt("".join(block(n, a) for n, a in batches[1])))
    assert event["dispatched_count"] == 2
    assert (
        teacher["appworld"]["schema_compatibility"]
        == adapter.versions["schema_compatibility"]
    )
    assert (
        read_json(tmp_path / "run/teacher_synthetic/schema_compatibility.json")
        == adapter.schema_compatibility
    )


def test_v6_prompt_and_other_guards_remain_unchanged(
    prepared_adapter, monkeypatch, tmp_path, current_v6_unit_lock
):
    from recoveragent.execution_v6 import SYSTEM_PROMPT

    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    s = StudentSession(
        prepared_adapter,
        Tokenizer(),
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"instruction": "Perform a synthetic action."}),
            },
        ],
        prepared_adapter.initial_tools(),
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        answer_guard=True,
        execution_protocol=student.V6_RUNTIME,
    )
    assert s.build_request()["messages"][0]["content"] == SYSTEM_PROMPT
    event = s.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": None, "api_name": "show_category_sizes"},
            )
        )
    )
    assert event["protocol_rejection"] == "invalid_arguments"
    assert not prepared_adapter.calls and s.harness.rejections == 1


def test_validator_receives_exactly_the_schema_sent_to_the_actor(
    prepared_adapter, monkeypatch, tmp_path
):
    from recoveragent import adapter as adapter_module

    s = make_student(prepared_adapter, monkeypatch, tmp_path / "student")
    actual = s.build_request()["tools"]
    shown = function(actual, "supervisor__complete_task")["function"]["parameters"]
    observed = []
    validator = adapter_module.Draft202012Validator

    def capture(schema):
        observed.append(copy.deepcopy(schema))
        return validator(schema)

    monkeypatch.setattr(adapter_module, "Draft202012Validator", capture)
    check_batch(
        message("supervisor__complete_task", {"answer": None}), s.messages, actual
    )
    assert observed == [shown]


def test_wrong_adapter_revision_rejected_before_loading_public_contracts(
    public_contract, monkeypatch, tmp_path
):
    adapter = SyntheticAdapter(public_contract["upstream_catalogue"])
    adapter.versions["revision"] = "other"
    monkeypatch.setattr(
        compat,
        "load_public_openapi",
        lambda _: pytest.fail("Must reject before loading"),
    )
    with pytest.raises(compat.SchemaCompatibilityError, match="pinned AppWorld"):
        adapter.configure_schema_compatibility(compat.SCHEMA_REVISION, tmp_path)
    assert not list(tmp_path.iterdir())


def test_all_pinned_public_api_definitions_convert_without_creating_a_world(
    monkeypatch,
):
    import dotenv

    # Upstream imports auto-load .env. This software-only metadata test must
    # neither read real credentials nor leak environment changes into other tests.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    import appworld
    from appworld.api_docs import _prepare_api_docs_function_calling_mcp_or_smolagents

    def forbidden(*args, **kwargs):
        pytest.fail("Schema tests must never instantiate an AppWorld task")

    monkeypatch.setattr(appworld, "AppWorld", forbidden)
    apps = [
        "amazon",
        "api_docs",
        "file_system",
        "gmail",
        "phone",
        "simple_note",
        "splitwise",
        "spotify",
        "supervisor",
        "todoist",
        "venmo",
    ]
    raw = [
        tool
        for app in apps
        for tool in _prepare_api_docs_function_calling_mcp_or_smolagents(
            "function_calling", app
        )
    ]
    before = copy.deepcopy(raw)
    definitions, source = compat.load_public_openapi(raw)
    result = compat.convert_catalogue(
        raw, definitions, appworld_revision=APPWORLD_REVISION
    )
    assert len(raw) == 473 and len(result["changes"]) == 248
    assert raw == before and len(source["public_api_sources"]) == 11
    for change in result["changes"]:
        Draft202012Validator(change["public_parameter_schema"]).validate(None)
    assert result["upstream_sha256"] == digest(before)
    assert result["compatible_sha256"] == digest(result["compatible_catalogue"])
