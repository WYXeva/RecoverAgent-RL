"""Real native template and execution/parser code; synthetic tokens/APIs/network."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from recoveragent import student, student_tokens as stream
from recoveragent.grpo_segments import coalesce
from recoveragent.harness import RULES, BUDGET_REVISION, ProtocolHarness
from recoveragent.io import read_json, digest
from recoveragent.student import StudentSession
from recoveragent.v2_grpo import segments
from recoveragent.v2_model import NativeTokenizer
from recoveragent.v2_vllm import request_payload, response_receipt
import test_schema_compatibility as schema_tests

public_contract = schema_tests.public_contract
prepared_adapter = schema_tests.prepared_adapter
TEMPLATE = Path(__file__).parent / "fixtures/qwen35_native_template.jinja"


class CharacterNative:
    chat_template = TEMPLATE.read_text()
    eos_token_id, pad_token_id = 1, 0

    def __init__(self):
        self.rendered = []

    def __call__(self, text, **kwargs):
        pieces = text.split("<|im_end|>")
        ids = []
        for i, piece in enumerate(pieces):
            if i:
                ids.append(1)
            ids.extend(ord(c) + 10 for c in piece)
        return {"input_ids": ids}

    def decode(self, ids, **kwargs):
        return "".join("<|im_end|>" if n == 1 else chr(n - 10) for n in ids)

    def apply_chat_template(self, messages, **kwargs):
        from transformers.utils.chat_template_utils import _compile_jinja_template

        self.rendered.append(copy.deepcopy(messages))
        return _compile_jinja_template(self.chat_template).render(
            messages=messages, **kwargs
        )


@pytest.fixture(params=[stream.REVISION, stream.FULL_DOCUMENT_REVISION])
def session(prepared_adapter, monkeypatch, tmp_path, request):
    import os

    native = CharacterNative()
    if os.environ.get("RECOVERAGENT_APPEND_TOKENIZER"):
        from transformers import AutoTokenizer

        native = AutoTokenizer.from_pretrained(
            os.environ["RECOVERAGENT_APPEND_TOKENIZER"], local_files_only=True
        )
        native.rendered = []
        original_render = native.apply_chat_template

        def record_render(messages, **kwargs):
            native.rendered.append(copy.deepcopy(messages))
            return original_render(messages, **kwargs)

        native.apply_chat_template = record_render
    tokenizer = NativeTokenizer(native)
    from recoveragent import tool_discovery

    tool_discovery.configure(
        prepared_adapter, tool_discovery.REVISION, tmp_path / "discovery"
    )
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
    )
    value = StudentSession(
        prepared_adapter,
        tokenizer,
        [
            {"role": "system", "content": RULES},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": "Inspect public category sizes and payment requests.",
                        "tool_discovery_notice": tool_discovery.NOTICE,
                    }
                ),
            },
        ],
        prepared_adapter.initial_tools(),
        tool_mode="dynamic_discovery",
        output=tmp_path / "episode/session",
        protocol_harness=True,
        input_encoding=request.param,
        top_p=1.0,
    )
    # Synthetic model identity only; exercise the actual native XML parser.
    value.model_contract = {"tool_format": "qwen_xml"}
    value.harness = ProtocolHarness(
        "dynamic_discovery", budget_revision=BUDGET_REVISION
    )
    value.max_input_tokens = 65536
    return value


def call(name, **kwargs):
    return (
        "<tool_call>\n<function="
        + name
        + ">\n"
        + "".join(
            f"<parameter={k}>\n{json.dumps(v) if v is None else v}\n</parameter>\n"
            for k, v in kwargs.items()
        )
        + "</function>\n</tool_call>"
    )


def reply(session, text, *, eos=True):
    def network(request):
        stream.validate_request(request, session.tokenizer)
        ids = stream.encode(session.tokenizer, text + ("<|im_end|>" if eos else ""))
        # Go through actual vLLM payload and response validation; only network is fake.
        payload = request_payload(
            request, request["policy"], session.tokenizer, "synthetic"
        )
        assert payload["prompt"] == request["input_token_ids"]
        return response_receipt(
            {
                "model": "synthetic",
                "id": "mock_only",
                "choices": [
                    {
                        "token_ids": ids,
                        "logprobs": {
                            "token_logprobs": [
                                -1.0 - i / 10000 for i in range(len(ids))
                            ]
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": len(payload["prompt"]),
                    "completion_tokens": len(ids),
                },
            },
            request,
            request["policy"],
            session.tokenizer,
            "synthetic",
            0.0,
        )

    return network


def requests(session):
    return [
        read_json(p)
        for p in sorted((session.output / "requests").glob("*.request.json"))
    ]


def test_multiturn_discovery_rejection_batch_and_raw_prefix(session):
    header = copy.deepcopy(session.build_request()["prompt_tools"])
    name = "gmail__show_category_sizes"
    denied = session.step(
        reply(session, call(name, access_token="observed", read=None))
    )
    assert (
        denied["protocol_rejection"] == "invisible_tool" and not session.adapter.calls
    )
    doc = call(
        "api_docs__show_api_doc", app_name="gmail", api_name="show_category_sizes"
    )
    # Even with discovery first, the original whole-batch visibility rule remains.
    same_batch = session.step(
        reply(session, doc + call(name, access_token="observed", read=None))
    )
    assert (
        same_batch["protocol_rejection"] == "invisible_tool"
        and not session.adapter.calls
    )
    session.step(reply(session, doc))
    observation = json.loads(session.messages[-1]["content"])
    expected = schema_tests.function(session.adapter.catalogue, name)
    assert observation["tool_definitions"] == [expected]
    assert observation["availability"] == "next_assistant_reply"
    assert session.build_request()["prompt_tools"] == header
    assert expected in session.build_request()["tools"]
    # Deliberately noncanonical spacing/order: these exact output IDs must survive.
    raw = (
        "  "
        + call(name, read=None, access_token="observed")
        + "\n"
        + call(name, access_token="observed", read=True)
    )
    assert session.step(reply(session, raw))["dispatched_count"] == 2
    session.step(
        reply(
            session,
            call(
                "api_docs__show_api_doc",
                app_name="venmo",
                api_name="show_sent_payment_requests",
            ),
        )
    )
    session.step(reply(session, "<tool_call><function=broken>", eos=False))
    session.step(reply(session, "There is no tool call in this reply."))
    records = requests(session)
    previous = None
    for q in records:
        r = read_json(
            session.output / "requests" / f"{records.index(q)+1:03d}.response.json"
        )
        stream.validate_request(q, session.tokenizer)
        stream.validate_transition(q, previous)
        assert q["prompt_tools"] == header
        if previous:
            prefix = previous[0]["input_token_ids"] + previous[1]["token_ids"]
            assert q["input_token_ids"][: len(prefix)] == prefix
        previous = q, r
    # No structured historical Assistant was ever passed to the template renderer.
    assert not any(
        m["role"] == "assistant" for batch in session.tokenizer.rendered for m in batch
    )
    rows = segments(session.output.parent, session.identity, max_length=65536)
    merged = coalesce(rows, max_target_tokens=16384)
    assert len(merged) == 1
    actual = [
        (t, p)
        for r in rows
        for t, p in zip(r["completion_ids"], r["logprobs"], strict=True)
    ]
    combined = [
        (t, p)
        for r in merged
        for t, p, m in zip(
            r["completion_ids"], r["logprobs"], r["tool_mask"], strict=True
        )
        if m
    ]
    assert combined == actual
    import os

    if os.environ.get("RECOVERAGENT_APPEND_AUDIT"):
        from recoveragent.io import write_json

        write_json(
            os.environ["RECOVERAGENT_APPEND_AUDIT"],
            {
                "real_model_execution": False,
                "real_tokenizer": bool(os.environ.get("RECOVERAGENT_APPEND_TOKENIZER")),
                "requests": len(rows),
                "prefix_checks": len(rows) - 1,
                "chains": len(merged),
                "original_sequence_tokens": sum(
                    len(r["prompt_ids"]) + len(r["completion_ids"]) for r in rows
                ),
                "merged_sequence_tokens": sum(
                    len(r["prompt_ids"]) + len(r["completion_ids"]) for r in merged
                ),
                "target_tokens": len(actual),
                "token_logprob_sha256": digest(actual),
                "complete_target_equality": True,
            },
        )
    assert len(coalesce(rows, max_target_tokens=100)) > 1
    assert (
        records[-1]["token_history"]["turns"][-1]["generated_ids"][-1]
        != session.tokenizer.eos_token_id
    )


@pytest.mark.parametrize(
    "damage", ["initial", "program", "response", "tools", "encoding"]
)
def test_input_receipt_tampering_fails_before_serving(session, damage):
    session.step(
        reply(
            session,
            call(
                "api_docs__show_api_doc",
                app_name="gmail",
                api_name="show_category_sizes",
            ),
        )
    )
    old = requests(session)[0]
    response = read_json(session.output / "requests/001.response.json")
    q = session.build_request()
    if damage == "initial":
        q["input_token_ids"][0] += 1
    if damage == "program":
        q["token_history"]["turns"][0]["program_ids"][-1] += 1
    if damage == "response":
        q["token_history"]["turns"][0]["response_sha256"] = "bad"
    if damage == "tools":
        q["tools"][-1]["function"]["parameters"]["required"] = []
    if damage == "encoding":
        q.pop(stream.KEY)
    with pytest.raises(ValueError):
        stream.validate_request(q, session.tokenizer)
        stream.validate_transition(q, (old, response))


def test_legacy_and_old_export_remain_distinct(session):
    from recoveragent.base_sft import assistant_examples

    q = session.build_request()
    with pytest.raises(ValueError, match="token-preserving"):
        assistant_examples(q, session.tokenizer)
    old = {"messages": q["messages"], "tools": q["prompt_tools"]}
    old["input_token_ids"] = stream.encode(
        session.tokenizer,
        stream.render(session.tokenizer, old["messages"], old["tools"]),
    )
    stream.validate_request(old, session.tokenizer)
    session.step(reply(session, "<broken>"))
    next_request = session.build_request()
    with pytest.raises(ValueError):
        stream.validate_transition(next_request, (old, {"token_ids": [1]}))


def test_plain_text_without_harness_preserves_real_tokens(session):
    session.harness = None
    session.step(reply(session, "Plain final text."))
    assert session.termination == "assistant_stop_without_tool"
    turn = read_json(session.output / "turns/001.json")
    assert turn[stream.KEY] == session.token_history.revision
    assert turn["token_history"]["turns"][0]["generated_ids"] == stream.encode(
        session.tokenizer, "Plain final text.<|im_end|>"
    )


def test_transformers_backend_uses_same_actual_tokens(session):
    """Only model.generate is fake; the request/response path is the real backend."""
    import time
    from types import SimpleNamespace
    import torch
    from recoveragent.v2_inference import generate

    session.step(
        reply(
            session,
            call(
                "api_docs__show_api_doc",
                app_name="gmail",
                api_name="show_category_sizes",
            ),
        )
    )
    q = session.build_request()
    generated = stream.encode(session.tokenizer, "No call.<|im_end|>")

    class Model:
        device = "cpu"

        def eval(self):
            return self

        def generate(self, input_ids, **kwargs):
            assert input_ids[0].tolist() == q["input_token_ids"]
            return SimpleNamespace(
                sequences=torch.tensor([q["input_token_ids"] + generated]),
                scores=[torch.zeros((1, max(generated) + 1)) for _ in generated],
            )

    response = generate(
        Model(),
        session.tokenizer,
        q,
        identity=q["policy"],
        deadline=time.monotonic() + 10,
        max_input_tokens=65536,
    )
    stream.validate_response(q, response)
    assert response["token_ids"] == generated
    assert len(response["logprobs"]) == len(generated)
    broken = copy.deepcopy(q)
    broken["input_token_ids"][-1] += 1
    with pytest.raises(ValueError):
        generate(
            Model(),
            session.tokenizer,
            broken,
            identity=q["policy"],
            deadline=time.monotonic() + 10,
            max_input_tokens=65536,
        )


def test_repeated_docs_always_complete_in_v2_and_legacy_preserved(session):
    name = "gmail__show_category_sizes"
    definition = schema_tests.function(session.adapter.catalogue, name)
    raw = json.dumps(
        {
            "description": definition["function"].get("description", ""),
            "parameters": [
                {"name": k, **v}
                for k, v in definition["function"]["parameters"]["properties"].items()
            ],
            "response_schemas": {"success": {"type": "object"}},
        }
    )
    session.adapter.world.execute = lambda _: raw
    initial = session.build_request()
    doc = call(
        "api_docs__show_api_doc", app_name="gmail", api_name="show_category_sizes"
    )
    for index in range(2):
        assert session.step(reply(session, doc))["dispatched_count"] == 1
        visible = json.loads(
            next(
                m["content"] for m in reversed(session.messages) if m["role"] == "tool"
            )
        )
        if session.token_history.revision == stream.FULL_DOCUMENT_REVISION:
            assert visible["tool_definitions"] == [definition]
            assert "parameter_schema_ref" not in json.dumps(visible)
            assert visible["observation"]["response_schemas"] == {
                "success": {"type": "object"}
            }
            assert "parameters" not in visible["observation"]
        elif index == 1:
            assert visible["parameter_schema_ref"] == name
            assert "tool_definitions" not in visible
        q = session.build_request()
        assert q["prompt_tools"] == initial["prompt_tools"]
        assert q["messages"][:2] == initial["messages"]
        stream.validate_request(q, session.tokenizer)
    assert (
        session.step(reply(session, call(name, access_token="observed", read=None)))[
            "dispatched_count"
        ]
        == 1
    )
    if session.token_history.revision == stream.FULL_DOCUMENT_REVISION:
        rendered = session.tokenizer.decode(initial["input_token_ids"])
        assert stream.FULL_DOCUMENT_RULES in initial["messages"][0]["content"]
        assert "only startup tools" in rendered
        assert "current tools schema" not in rendered
        assert "parameter_schema_ref" not in rendered
        assert "same Function Calling" in rendered
        assert "NEXT reply" in rendered
        assert name not in rendered
    # Failed repeat lookup must not claim availability or invent a definition.
    session.adapter.world.execute = lambda _: "Execution failed. public API error"
    session.step(reply(session, doc))
    assert session.messages[-1]["content"] == "Execution failed. public API error"


def test_v1_and_v2_cannot_mix_within_episode(session):
    session.step(reply(session, "No call."))
    old = requests(session)[0]
    response = read_json(session.output / "requests/001.response.json")
    q = session.build_request()
    q[stream.KEY] = next(v for v in stream.REVISIONS if v != old[stream.KEY])
    with pytest.raises(ValueError, match="encoding"):
        stream.validate_transition(q, (old, response))


def test_new_rules_leave_historical_system_and_public_input_untouched():
    from recoveragent.execution_v6 import SYSTEM_PROMPT
    from recoveragent.tool_discovery import NOTICE

    original = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"instruction": "Public task", "tool_discovery_notice": NOTICE}
            ),
        },
    ]
    before = copy.deepcopy(original)
    updated = stream.initial_messages(original, stream.FULL_DOCUMENT_REVISION)
    assert original == before
    assert updated[0]["content"] == SYSTEM_PROMPT + "\n\n" + stream.FULL_DOCUMENT_RULES
    assert (
        "current tools schema"
        not in json.loads(updated[1]["content"])["tool_discovery_notice"]
    )
