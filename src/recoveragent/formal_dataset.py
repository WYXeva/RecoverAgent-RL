"""Portable Base-SFT v1 package: immutable sources, family splits, validated targets."""

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from .base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from .dataset_design import design, example_weights, family_split
from .demonstrations import sha256, verify_dataset, validate_demo
from .io import canonical, digest, files_manifest, read_json, write_json
from .sft_quality import audit_samples

FORMAT = "recoveragent-base-sft-v1"


def training_record(sample):
    """Keep control-side state/evaluation references out of the model-input file."""
    return {
        k: v
        for k, v in sample.items()
        if k not in {"appworld", "provenance", "initial_state_hash"}
    }


def json_lines(path):
    with Path(path).open() as stream:
        for line in stream:
            yield json.loads(line)


def write_lines(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def visible_schema_replay(sample, catalogue):
    """Reconstruct tools from successful, already-returned public discovery calls."""
    dynamic = sample.get("tool_mode", "dynamic_discovery") == "dynamic_discovery"
    visible = (
        [
            s
            for n, s in catalogue.items()
            if n.split("__")[0] in {"api_docs", "supervisor"}
        ]
        if dynamic
        else sample["turns"][0]["tools"]
    )
    for turn in sample["turns"]:
        if turn["tools"] != visible:
            raise ValueError("Schema exposed future or undiscovered tools")
        index = turn["assistant_index"]
        if index == len(sample["messages"]):
            continue  # A rejected final response has no executed message.
        for offset, call in enumerate(sample["messages"][index].get("tool_calls", [])):
            f = call["function"]
            if not dynamic or f["name"] != "api_docs__show_api_doc":
                continue
            result = sample["messages"][index + 1 + offset]["content"]
            if "Execution failed" in result:
                continue
            args = json.loads(f["arguments"])
            name = (
                args.get("app_name", "").strip().lower()
                + "__"
                + args.get("api_name", "").strip().lower()
            )
            if name in catalogue and name not in {
                s["function"]["name"] for s in visible
            }:
                visible = [*visible, catalogue[name]]
    return visible


def public_audit(samples, root):
    """Controller audit uses only Task.load(load_ground_truth=False)."""
    from .sampling import official_pool
    from appworld.task import Task

    pool = official_pool(root)
    cache = {}
    schemas = set()
    for sample in samples:
        task = sample["task_id"]
        if task not in cache:
            public = Task.load(task, load_ground_truth=False)
            context = {
                "instruction": public.instruction,
                "supervisor": dict(public.supervisor),
                "datetime": str(public.datetime),
                "app_descriptions": public.app_descriptions,
            }
            catalogue = {
                t["function"]["name"]: t for t in public.api_docs.function_calling()
            }
            cache[task] = (context, catalogue)
        context, catalogue = cache[task]
        if (
            json.loads(sample["messages"][1]["content"]) != context
            or sample["scenario_id"] != pool[task]
        ):
            raise ValueError("Initial context/scenario differs from public task")
        visible_schema_replay(sample, catalogue)
        for turn in sample["turns"]:
            h = digest(turn["tools"])
            if (task, h) in schemas:
                continue
            if any(t != catalogue.get(t["function"]["name"]) for t in turn["tools"]):
                raise ValueError("Tool schema differs from official public catalogue")
            schemas.add((task, h))
    return {
        "public_tasks_reconstructed": len(cache),
        "task_schema_sets_checked": len(schemas),
        "ground_truth_loaded": False,
        "dynamic_visibility_replayed": True,
    }


def build(result_path, pool, output, protocol_paths):
    result = read_json(result_path)
    inventory_dir = Path(result["inventory"])
    inventory = read_json(inventory_dir / "inventory.json")
    if (
        inventory["status"] != "PASS"
        or sha256(inventory_dir / "samples.jsonl") != inventory["samples_sha256"]
        or digest(sorted(pool.items())) != inventory["official_train_pool_sha256"]
    ):
        raise ValueError("Source audit incomplete or changed")
    samples = list(json_lines(inventory_dir / "samples.jsonl"))
    quality = result["quality_rows"]
    campaign = result["campaign"]
    selection = design(samples, quality, max_per_task=campaign["max_per_task"])
    if selection != result["design"]:
        raise ValueError("Selection no longer reproducible")
    split = family_split(pool, campaign["split_seed"], campaign["validation_families"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    control = output / "control"
    control.mkdir()
    portable_sources = []
    seen = set()
    # Copy one package per manifest hash; preserve all alias paths in the inventory.
    for source in inventory["sources"]:
        h = source["manifest_sha256"]
        if h in seen:
            continue
        seen.add(h)
        directory = Path(source["path"])
        verify_dataset(directory, set(pool), h)
        target = control / "sources" / h
        shutil.copytree(directory, target)
        portable_sources.append(
            {
                "path": str(target.relative_to(output)),
                "manifest_sha256": h,
                "dataset_id": source["dataset_id"],
            }
        )
    shutil.copy2(inventory_dir / "samples.jsonl", control / "all_samples.jsonl")
    shutil.copy2(inventory_dir / "origins.json", control / "origins.json")
    shutil.copy2(inventory_dir / "attempts.json", control / "attempts.json")
    write_json(control / "source_inventory.json", inventory)
    write_json(control / "selection.json", selection)
    write_json(control / "source_quality.json", quality)
    write_json(control / "official_train_pool.json", pool)
    write_json(control / "split.json", split)
    write_json(control / "campaign.json", campaign)
    if result.get("interruption"):
        write_json(control / "collection_interruption.json", result["interruption"])
        shutil.copy2(
            "reports/base_sft_provider_diagnostic.json",
            control / "provider_diagnostic.json",
        )
    source_codes = {}
    for path in protocol_paths:
        source = Path(path)
        if source.is_absolute() or ".." in source.parts or source.is_symlink():
            raise ValueError(
                "Protocol sources must be regular repository-relative files"
            )
        target = control / "protocol_sources" / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_codes[path] = sha256(source)
    write_json(control / "protocol_lock.json", source_codes)
    selected_ids = set(selection["selected_ids"])
    selected = [s for s in samples if s["trajectory_id"] in selected_ids]
    tokenizer = load_tokenizer()
    partitions = {}
    weights = {}
    for name in ("train", "validation"):
        records = [s for s in selected if split[s["scenario_id"]] == name]
        if not records:
            raise ValueError("Empty formal partition")
        write_lines(
            output / "model_input" / f"{name}.jsonl",
            (training_record(s) for s in records),
        )
        report = audit_samples(records, tokenizer, output / "tokenized" / name, 32768)
        if report["ready_complete_demonstrations"] != len(records):
            raise ValueError("Final whole-trajectory token audit changed")
        w = example_weights(records, quality, selection["clusters"])
        weights.update(w)
        raw = output / "tokenized" / name / "ready_assistant_examples.jsonl"
        write_lines(
            output / "tokenized" / f"{name}.jsonl",
            (
                {**row, "example_weight": w[row["trajectory_id"]]}
                for row in json_lines(raw)
            ),
        )
        report["intermediate_unweighted_examples_sha256"] = report.pop(
            "ready_examples_sha256"
        )
        report["weighted_examples_sha256"] = sha256(
            output / "tokenized" / f"{name}.jsonl"
        )
        raw.unlink()  # Only this new intermediate, never a source artifact.
        write_json(output / "tokenized" / name / "quality.json", report)
        partitions[name] = {
            **report,
            "families": sorted({s["scenario_id"] for s in records}),
            "prompt_versions": dict(
                Counter(s.get("actor_prompt_variant", "legacy_v1") for s in records)
            ),
            "tool_modes": dict(
                Counter(s.get("tool_mode", "dynamic_discovery") for s in records)
            ),
        }
    for name in ("deferred", "reserve", "duplicates"):
        wanted = {s["trajectory_id"] for s in selection[name]}
        write_lines(
            output / "model_input" / f"{name}.jsonl",
            (training_record(s) for s in samples if s["trajectory_id"] in wanted),
        )
    write_json(control / "example_weights.json", weights)
    manifest = {
        "format": FORMAT,
        "version": 1,
        "target_student": {
            "model": MODEL,
            "revision": REVISION,
            "thinking": False,
            "chat_template_sha256": TEMPLATE_SHA256,
        },
        "primary_execution_protocol": "execution_v5 / dynamic_discovery / numeric_answer_self_correction_v1",
        "max_sequence_tokens": 32768,
        "max_target_tokens": 4096,
        "split_seed": campaign["split_seed"],
        "partition_metrics": partitions,
        "counts": {
            "source_qualified": len(samples),
            "selected": len(selected),
            "unique_tasks": len({s["task_id"] for s in selected}),
            "families": len({s["scenario_id"] for s in selected}),
            "deferred": len(selection["deferred"]),
            "reserved_by_task_cap": len(selection["reserve"]),
            "exact_duplicates": len(selection["duplicates"]),
            "high_similarity_pairs": len(selection["similar_pairs"]),
        },
        "source_packages": portable_sources,
        "source_inventory_sha256": sha256(control / "source_inventory.json"),
        "loss_contract": "weighted mean of per-example supervised-token CE means; weights mean one per partition; no packing or truncation",
        "training_input_paths": ["tokenized/train.jsonl", "tokenized/validation.jsonl"],
        "control_evidence_is_never_model_input": True,
        "training_started": False,
        "collection_stop": result.get("interruption", {}).get(
            "reason", "declared_campaign_boundary"
        ),
        "license_notice": "Private restricted AppWorld-derived data. Authorized secure transfer only; never publish raw package.",
        "files": files_manifest(output),
    }
    manifest["dataset_id"] = digest(manifest)
    write_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(
        sha256(output / "manifest.json") + "  manifest.json\n"
    )
    return manifest


def verify(path, expected_hash, *, official_root=None):
    path = Path(path)
    if sha256(path / "manifest.json") != expected_hash:
        raise ValueError("Trusted bundle manifest SHA-256 mismatch")
    if (path / "manifest.sha256").read_text() != expected_hash + "  manifest.json\n":
        raise ValueError("Manifest checksum sidecar mismatch")
    manifest = read_json(path / "manifest.json")
    if (
        manifest["format"] != FORMAT
        or digest({k: v for k, v in manifest.items() if k != "dataset_id"})
        != manifest["dataset_id"]
    ):
        raise ValueError("Dataset identity mismatch")
    actual = files_manifest(path)
    actual.pop("manifest.json")
    actual.pop("manifest.sha256")
    if actual != manifest["files"]:
        raise ValueError("Bundle file integrity mismatch")
    pool = read_json(path / "control/official_train_pool.json")
    if (path / "control/collection_interruption.json").exists():
        stop = read_json(path / "control/collection_interruption.json")
        if (
            sha256(path / "control/provider_diagnostic.json")
            != stop["provider_diagnostic_sha256"]
            or read_json(path / "control/provider_diagnostic.json")["http_status"]
            != 402
            or manifest["collection_stop"] != stop["reason"]
        ):
            raise ValueError("Collection interruption evidence mismatch")
    if official_root:
        from .sampling import official_pool

        if pool != official_pool(official_root):
            raise ValueError("Official train pool mismatch")
    sources = {}
    for source in manifest["source_packages"]:
        root = path / source["path"]
        verify_dataset(root, set(pool), source["manifest_sha256"])
        for sample in json_lines(root / "demonstrations.jsonl"):
            identity = sample["trajectory_id"]
            if identity in sources and sources[identity] != sample:
                raise ValueError("Conflicting original sample")
            sources[identity] = sample
    samples = list(json_lines(path / "control/all_samples.jsonl"))
    if (
        len(samples) != len(sources)
        or {s["trajectory_id"]: s for s in samples} != sources
    ):
        raise ValueError("Sources do not reproduce full candidate set")
    campaign = read_json(path / "control/campaign.json")
    split = family_split(pool, campaign["split_seed"], campaign["validation_families"])
    if split != read_json(path / "control/split.json"):
        raise ValueError("Family split changed")
    quality = read_json(path / "control/source_quality.json")
    selection = design(samples, quality, max_per_task=campaign["max_per_task"])
    if selection != read_json(path / "control/selection.json"):
        raise ValueError("Selection changed")
    selected = set(selection["selected_ids"])
    observed = set()
    expected_weights = {}
    for name in ("train", "validation"):
        records = list(json_lines(path / "model_input" / f"{name}.jsonl"))
        want = [
            s
            for s in samples
            if s["trajectory_id"] in selected and split[s["scenario_id"]] == name
        ]
        if records != [training_record(s) for s in want]:
            raise ValueError("Training records differ from source/split")
        w = example_weights(records, quality, selection["clusters"])
        expected_weights.update(w)
        expected_turns = [
            (s["trajectory_id"], t["assistant_index"])
            for s in records
            for t in s["turns"]
            if t.get("supervise") is not False
        ]
        actual_turns = []
        by_turn = {
            (s["trajectory_id"], t["assistant_index"]): (s, t)
            for s in records
            for t in s["turns"]
            if t.get("supervise") is not False
        }
        for e in json_lines(path / "tokenized" / f"{name}.jsonl"):
            identity = e["trajectory_id"]
            start = e["target_start"]
            ids = e["input_ids"]
            source, turn = by_turn[(identity, e["assistant_index"])]
            if (
                identity not in w
                or e["task_id"] != source["task_id"]
                or e["scenario_id"] != source["scenario_id"]
                or e["history_hash"] != turn["history_hash"]
                or e["tools_hash"] != digest(turn["tools"])
                or not 1 <= start < len(ids)
                or e["example_weight"] != w[identity]
                or len(ids) > 32768
                or len(ids) - start > 4096
                or e["labels"] != [-100] * start + ids[start:]
                or e["loss_mask"] != [0] * start + [1] * (len(ids) - start)
                or e["attention_mask"] != [1] * len(ids)
            ):
                raise ValueError("Token/mask/weight contract violation")
            actual_turns.append((identity, e["assistant_index"]))
        if actual_turns != expected_turns:
            raise ValueError("Incomplete or reordered Assistant targets")
        if observed & {s["trajectory_id"] for s in records}:
            raise ValueError("Split overlap")
        observed.update(s["trajectory_id"] for s in records)
    if observed != selected or expected_weights != read_json(
        path / "control/example_weights.json"
    ):
        raise ValueError("Selection/weights incomplete")
    for name in ("deferred", "reserve", "duplicates"):
        wanted = {r["trajectory_id"] for r in selection[name]}
        if list(json_lines(path / "model_input" / f"{name}.jsonl")) != [
            training_record(s) for s in samples if s["trajectory_id"] in wanted
        ]:
            raise ValueError("Deferred/reserve/duplicate partition mismatch")
    for s in samples:
        validate_demo(s)
    result = {
        "status": "PASS",
        "manifest_sha256": expected_hash,
        "dataset_id": manifest["dataset_id"],
        "counts": manifest["counts"],
        "source_packages_reverified": len(manifest["source_packages"]),
        "training_started": False,
        "official_train_rechecked": bool(official_root),
    }
    if official_root:
        result.update(public_audit(samples, official_root))
    return result


def retokenize(path, expected_hash, output):
    verify(path, expected_hash)
    verify_runtime_protocol(path)
    from .base_sft import assistant_examples

    path, output = Path(path), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    tokenizer = load_tokenizer()
    weights = read_json(path / "control/example_weights.json")
    counts = {}
    for name in ("train", "validation"):

        def rows():
            for sample in json_lines(path / "model_input" / f"{name}.jsonl"):
                for row in assistant_examples(sample, tokenizer, 32768):
                    yield {**row, "example_weight": weights[row["trajectory_id"]]}

        target = output / f"{name}.jsonl"
        write_lines(target, rows())
        if sha256(target) != sha256(path / "tokenized" / f"{name}.jsonl"):
            raise ValueError("Retokenization is not byte-identical")
        counts[name] = sum(1 for _ in json_lines(target))
    result = {
        "status": "PASS",
        "source_manifest_sha256": expected_hash,
        "byte_identical": True,
        "counts": counts,
        "weights_loaded": False,
        "training_started": False,
    }
    write_json(output / "verification.json", result)
    return result


def verify_runtime_protocol(dataset, repository="."):
    """Require the checked-out code/configs to match the private package source lock."""
    locked = read_json(Path(dataset) / "control/protocol_lock.json")
    for name, expected in locked.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid runtime protocol path")
        actual = Path(repository) / path
        if not actual.is_file() or sha256(actual) != expected:
            raise ValueError("Runtime protocol source mismatch: " + name)
    return {"runtime_source_files_checked": len(locked), "runtime_source_match": True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["build", "verify", "retokenize", "public-audit"])
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    p.add_argument("--pool", default="configs/base_sft_official_train_pool.json")
    p.add_argument("--protocol-list", default="configs/base_sft_package_sources.json")
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--official-appworld-root")
    p.add_argument("--check-runtime", action="store_true")
    p.add_argument("--report", required=True)
    a = p.parse_args()
    if a.command == "build":
        m = build(a.input, read_json(a.pool), a.output, read_json(a.protocol_list))
        result = {
            "status": "PASS",
            "manifest_sha256": sha256(Path(a.output) / "manifest.json"),
            "dataset_id": m["dataset_id"],
            "counts": m["counts"],
            "partition_metrics": m["partition_metrics"],
            "training_started": False,
        }
    elif a.command == "verify":
        result = verify(
            a.input, a.expected_manifest_sha256, official_root=a.official_appworld_root
        )
        if a.check_runtime:
            result.update(verify_runtime_protocol(a.input))
    elif a.command == "retokenize":
        result = retokenize(a.input, a.expected_manifest_sha256, a.output)
    else:
        result = public_audit(list(json_lines(a.input)), a.official_appworld_root)
    write_json(a.report, result)
    print({k: v for k, v in result.items() if k != "partition_metrics"})


if __name__ == "__main__":
    main()
