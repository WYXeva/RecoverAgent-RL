"""Public synthetic APIs only; no model service or real AppWorld task."""

import copy
import json
from types import SimpleNamespace

import pytest
import test_schema_compatibility as schema_tests
from jsonschema import ValidationError
from test_schema_compatibility import (
    SyntheticAdapter,
    function,
    make_student,
)
from test_student_batch import block, receipt

from recoveragent import tool_discovery as discovery
from recoveragent.adapter import AppWorldAdapter, call_code
from recoveragent.io import digest, read_json
from recoveragent.schema_snapshot import prepare_snapshot_adapter

prepared_adapter = schema_tests.prepared_adapter
public_contract = schema_tests.public_contract


def test_search_then_discover_model_schema_is_dispatch_schema(
    prepared_adapter, monkeypatch, tmp_path
):
    adapter = prepared_adapter
    original = copy.deepcopy(adapter.catalogue)
    discovery.configure(adapter, discovery.REVISION, tmp_path / "audit")
    s = make_student(adapter, monkeypatch, tmp_path / "student")
    assert function(s.tools, discovery.SEARCH) == discovery.extra_tools(adapter)[0]
    event = s.step(receipt(block(discovery.SEARCH, {"app_name": "gmail"})))
    assert event["dispatched_count"] == 1 and not adapter.calls
    assert not any(
        t["function"]["name"] == "gmail__show_category_sizes" for t in s.tools
    )
    s.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    visible = function(s.tools, "gmail__show_category_sizes")
    assert visible == function(adapter.catalogue, "gmail__show_category_sizes")
    s.step(
        receipt(
            block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": None},
            )
        )
    )
    assert len(adapter.calls) == 2 and adapter.catalogue == original


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"app_name": None},
        {"app_name": "gmail", "limit": 11},
        {"app_name": "gmail", "offset": -1},
    ],
)
def test_search_strict_arguments(prepared_adapter, tmp_path, arguments):
    discovery.configure(prepared_adapter, discovery.REVISION, tmp_path)
    with pytest.raises(ValidationError):
        prepared_adapter.execute(
            discovery.SEARCH, arguments, prepared_adapter.initial_tools()
        )
    assert not prepared_adapter.calls


def test_search_pages_and_no_match(prepared_adapter):
    catalogue = prepared_adapter.catalogue
    full = json.loads(discovery.search(catalogue, {"app_name": "gmail"}))
    one = json.loads(discovery.search(catalogue, {"app_name": "gmail", "limit": 1}))
    assert one["apis"] == full["apis"][:1]
    result = json.loads(
        discovery.search(
            catalogue, {"app_name": "gmail", "query": "no_matching_api_xyz"}
        )
    )
    assert result["apis"] == []


def test_document_projection_preserves_extra_notes_and_return_schema(
    prepared_adapter, tmp_path
):
    adapter = prepared_adapter
    discovery.configure(adapter, discovery.REVISION, tmp_path)
    f = function(adapter.catalogue, "gmail__show_category_sizes")["function"]
    raw = json.dumps(
        {
            "description": f.get("description"),
            "parameters": [
                {
                    "name": name,
                    "type": "public type",
                    "required": False,
                    "default": None,
                    "description": prop.get("description"),
                    "constraints": ["Extra public condition"],
                }
                for name, prop in f["parameters"]["properties"].items()
            ],
            "response_schemas": {"200": {"category": "integer"}},
            "notes": "Pagination guidance remains available",
        }
    )
    adapter.world.execute = lambda code: raw
    visible, tools = adapter.execute(
        "api_docs__show_api_doc",
        {"app_name": "gmail", "api_name": "show_category_sizes"},
        adapter.initial_tools(),
    )
    doc = json.loads(visible)
    assert "parameters" not in doc
    assert doc["parameter_schema_ref"] == f["name"]
    assert doc["response_schemas"] == json.loads(raw)["response_schemas"]
    assert all(p["constraints"] for p in doc["parameter_notes"])
    audit = read_json(tmp_path / "tool_discovery_calls/000001.json")
    assert audit["raw"] == raw and audit["raw_sha256"] == digest(raw)
    assert audit["visible"] == visible and audit["visible_sha256"] == digest(visible)
    assert (
        discovery.document_result(adapter, "gmail__show_category_sizes", {}, raw, tools)
        == raw
    )
    for malformed in ("Execution failed", '{"parameters": [{"name":"unknown"}]}'):
        assert discovery.project_document(malformed, f) == malformed


def test_public_notice_and_restore_keep_same_discovery(
    prepared_adapter, monkeypatch, tmp_path
):
    adapter = prepared_adapter
    adapter.world.task = SimpleNamespace(
        instruction="Synthetic", supervisor={}, datetime="now", app_descriptions={}
    )
    legacy = AppWorldAdapter.public_task(adapter)
    discovery.configure(adapter, discovery.REVISION, tmp_path / "source")
    public = AppWorldAdapter.public_task(adapter)
    assert public == {**legacy, "tool_discovery_notice": discovery.NOTICE}
    versions, tools = copy.deepcopy(adapter.versions), adapter.initial_tools()
    restored = SyntheticAdapter(adapter.upstream_catalogue)
    prepare_snapshot_adapter(
        restored, {"versions": versions}, {"tools": tools}, tmp_path / "restore"
    )
    assert restored.initial_tools() == tools
    assert discovery.search(
        restored.catalogue, {"app_name": "gmail"}
    ) == discovery.search(adapter.catalogue, {"app_name": "gmail"})
    tampered = copy.deepcopy(versions)
    tampered["tool_discovery"]["search_tool_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        prepare_snapshot_adapter(
            restored, {"versions": tampered}, {"tools": tools}, tmp_path / "bad"
        )
    with pytest.raises(ValueError, match="differs"):
        discovery.extra_tools(restored, {"revision": "old"})
    modified = copy.deepcopy(tools)
    function(modified, discovery.SEARCH)["function"]["parameters"]["required"] = []
    with pytest.raises(ValueError, match="visible tools"):
        prepare_snapshot_adapter(
            restored,
            {"versions": versions},
            {"tools": modified},
            tmp_path / "bad_tools",
        )


def test_legacy_adapter_unchanged(public_contract):
    adapter = SyntheticAdapter(public_contract["upstream_catalogue"])
    assert not discovery.extra_tools(adapter)
    assert all(
        t["function"]["name"] != discovery.SEARCH for t in adapter.initial_tools()
    )
    with pytest.raises(ValueError):
        call_code(discovery.SEARCH, {"app_name": "gmail"}, adapter.initial_tools())


def test_new_protocol_binding_and_teacher_configuration(monkeypatch, tmp_path):
    from recoveragent import v2_runtime, v2_teacher
    from recoveragent.s2_experiment import configuration
    from recoveragent.s2_state import protocol_identity

    monkeypatch.setenv(
        "RECOVERAGENT_S2_EXECUTION", "v2_s2_append_teacher_text_final_20261118"
    )
    assert protocol_identity()["tool_discovery"] == discovery.REVISION
    args = (
        configuration()["feedback_task_ids"][0],
        configuration()["models"][configuration()["selected_model"]],
        tmp_path,
        1.0,
    )
    teacher = v2_teacher.configuration(
        *args, recovery=False, runtime=v2_runtime.S2_RUNTIME, purpose="s2_solver"
    )
    assert teacher[discovery.KEY] == discovery.REVISION
    assert configuration()["grpo"]["coalesce_exact_prefixes"] is True
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", v2_runtime.S2_SEGMENT_EXECUTION)
    assert discovery.KEY not in v2_teacher.configuration(
        *args, recovery=False, runtime=v2_runtime.S2_RUNTIME, purpose="s2_solver"
    )
    assert "tool_discovery" not in protocol_identity()
