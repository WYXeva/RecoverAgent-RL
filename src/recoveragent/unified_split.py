"""Public-ID split design, permission gates, and private exposure/coverage audit.

Never reads official answers, solution code, difficulty or required API labels.
The full-pool candidate is metadata only; only official train is operational.
"""

from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re

from . import APPWORLD_REVISION
from .io import digest, read_json

VERSION = "unified-scenario-split-v1"
SEED = 20261028
SPLITS = ("train", "dev", "test_normal", "test_challenge")
ROLES = ("base_sft", "recovery_grpo", "shared_validation")
DATASET_SHA = "5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c"
POLICY_URL = (
    "https://github.com/StonyBrookNLP/appworld/blob/"
    + APPWORLD_REVISION
    + "/README.md#no_entry_sign-agent-development-restrictions"
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_pool(root):
    from appworld import load_task_ids, update_root
    from appworld.task import task_id_to_generator_id

    direct = importlib.metadata.distribution("appworld").read_text("direct_url.json")
    if not direct or APPWORLD_REVISION not in direct:
        raise ValueError(
            "Task audit requires the pinned official AppWorld installation"
        )
    root = Path(root).resolve() / "data"
    update_root(str(root.parent))
    rows, anomalies, counts, seen = [], [], {}, set()
    source = {
        "appworld_revision": APPWORLD_REVISION,
        "data_version": (root / "version.txt").read_text().strip(),
        "split_files": {s: sha(root / "datasets" / (s + ".txt")) for s in SPLITS},
        "base_db_files": {
            p.name: sha(p) for p in sorted((root / "base_dbs").glob("*")) if p.is_file()
        },
        "license_sha256": sha(root / "LICENSE"),
    }
    apps = sorted(p.stem for p in (root / "api_docs/function_calling").glob("*.json"))
    source["tool_schema_files"] = {
        p.name: sha(p)
        for p in sorted((root / "api_docs/function_calling").glob("*.json"))
    }
    for split in SPLITS:
        ids = load_task_ids(split)  # No difficulty/ground_truth filter.
        counts[split] = {
            "tasks": len(ids),
            "scenarios": len({task_id_to_generator_id(t) for t in ids}),
        }
        for task in ids:
            if task in seen:
                anomalies.append({"task_id": task, "reason": "duplicate_membership"})
                continue
            seen.add(task)
            d = root / "tasks" / task
            expected = [
                "specs.json",
                "ground_truth/evaluation.py",
                "ground_truth/metadata.json",
                "ground_truth/test_data.json",
                "ground_truth/answer.json",
            ]
            expected += [f"dbs/{app}.jsonl" for app in [*apps, "admin"]]
            missing = sorted({n for n in expected if not (d / n).is_file()})
            # Presence only for protected evaluation artifacts and test task contents.
            row = {
                "task_id": task,
                "scenario_id": task_id_to_generator_id(task),
                "official_split": split,
                "missing_files": missing,
                "structural_status": "PASS" if not missing else "BLOCKED",
                "training_allowed": split == "train",
                "shared_development_validation_allowed": split in {"train", "dev"},
                "ground_truth_availability": "full_upstream_release"
                if split in {"train", "dev"}
                else "evaluation_only_not_full_solutions",
                "evaluation_artifacts_present": not any(
                    n.startswith("ground_truth/") for n in missing
                ),
                "runtime_verified": False,
            }
            if split == "train":
                row["initial_input_sha256"] = digest(
                    {
                        str(p.relative_to(d)): sha(p)
                        for p in [
                            d / "specs.json",
                            *sorted((d / "dbs").glob("*.jsonl")),
                        ]
                        if p.is_file()
                    }
                )
            rows.append(row)
            if missing:
                anomalies.append(
                    {"task_id": task, "reason": "missing_files", "files": missing}
                )
    groups = defaultdict(list)
    for row in rows:
        groups[row["scenario_id"]].append(row)
    for scenario, members in sorted(groups.items()):
        if len(members) != 3 or len({r["official_split"] for r in members}) != 1:
            anomalies.append(
                {
                    "scenario_id": scenario,
                    "reason": "non_triplet_or_cross_split",
                    "tasks": [r["task_id"] for r in members],
                }
            )
    extra = sorted(
        p.name for p in (root / "tasks").iterdir() if p.is_dir() and p.name not in seen
    )
    return {
        "format": "full-task-pool-audit-v1",
        "source": source,
        "policy_url": POLICY_URL,
        "tasks": len(rows),
        "scenarios": len(groups),
        "split_counts": counts,
        "scenario_size_histogram": dict(
            sorted(Counter(map(len, groups.values())).items())
        ),
        "anomalies": anomalies,
        "unlisted_task_directories": extra,
        "rows": sorted(rows, key=lambda r: r["task_id"]),
        "test_task_contents_read": False,
        "hidden_labels_used": False,
        "runtime_scope": "Separate train-only initial-state probes; file presence is not runtime success.",
    }


def make_split(audit, *, train_only, seed=SEED):
    if type(seed) is not int:
        raise ValueError("Integer seed required")
    groups = defaultdict(list)
    for row in audit["rows"]:
        if not train_only or row["official_split"] == "train":
            groups[row["scenario_id"]].append(row)
    rows, excluded = [], []
    for scenario, members in sorted(groups.items()):
        if (
            len(members) != 3
            or len({r["task_id"] for r in members}) != 3
            or any(r["structural_status"] != "PASS" for r in members)
            or len({r["official_split"] for r in members}) != 1
        ):
            excluded.append(
                {
                    "scenario_id": scenario,
                    "reason": "requires_three_distinct_structurally_valid_variants",
                }
            )
            continue
        ordered = sorted(
            members,
            key=lambda r: (
                digest([VERSION, seed, scenario, r["task_id"]]),
                r["task_id"],
            ),
        )
        for role, member in zip(ROLES, ordered, strict=True):
            rows.append(
                {
                    "task_id": member["task_id"],
                    "scenario_id": scenario,
                    "official_split": member["official_split"],
                    "role": role,
                    "initial_input_sha256": member.get("initial_input_sha256"),
                    "permission_status": "READY"
                    if member["official_split"] == "train"
                    else "BLOCKED",
                }
            )
    plan = {
        "format": VERSION,
        "seed": seed,
        "scope": "official_train" if train_only else "full_pool_candidate",
        "status": "FROZEN_DATA_PROTOCOL"
        if train_only
        else "BLOCKED_OFFICIAL_USAGE_RESTRICTIONS",
        "audit_sha256": digest(audit),
        "source": audit["source"],
        "assignment_rule": "sort SHA256(canonical([version,seed,scenario_id,task_id])); assign A,B,C",
        "uses_outcomes_or_exposure_for_assignment": False,
        "rows": rows,
        "excluded": excluded,
        "counts": {role: sum(r["role"] == role for r in rows) for role in ROLES},
        "grpo_reuses": "recovery_grpo",
        "training_authorized": False,
    }
    plan["manifest_sha256"] = digest(plan)
    validate_split(plan)
    return plan


def validate_split(plan):
    if (
        plan["format"] != VERSION
        or digest({k: v for k, v in plan.items() if k != "manifest_sha256"})
        != plan["manifest_sha256"]
    ):
        raise ValueError("Split manifest digest mismatch")
    groups, seen = defaultdict(list), set()
    for row in plan["rows"]:
        if (
            row["task_id"] in seen
            or row["task_id"].rsplit("_", 1)[0] != row["scenario_id"]
        ):
            raise ValueError("Duplicate task or wrong scenario")
        seen.add(row["task_id"])
        groups[row["scenario_id"]].append(row)
    for scenario, rows in groups.items():
        ordered = sorted(
            rows,
            key=lambda r: (
                digest([VERSION, plan["seed"], scenario, r["task_id"]]),
                r["task_id"],
            ),
        )
        if len(rows) != 3 or [r["role"] for r in ordered] != list(ROLES):
            raise ValueError("Scenario assignments are not reproducible triplets")
    if plan["scope"] == "official_train" and any(
        r["official_split"] != "train" for r in plan["rows"]
    ):
        raise ValueError("Non-train task in operational split")
    if plan["counts"] != {
        role: sum(r["role"] == role for r in plan["rows"]) for role in ROLES
    }:
        raise ValueError("Split counts differ")


def stage_tasks(plan, stage):
    validate_split(plan)
    if plan["scope"] != "official_train":
        raise ValueError(
            "Full-pool candidate is blocked; no executable training/evaluation plan"
        )
    role = {
        "base_sft": "base_sft",
        "recovery_sft": "recovery_grpo",
        "grpo": "recovery_grpo",
        "validation": "shared_validation",
    }[stage]
    selected = [r for r in plan["rows"] if r["role"] == role]
    if not selected:
        raise ValueError("No valid tasks for this stage")
    return selected


def validate_training_sources(plan, stage, records):
    """Dataset admission gate; not a substitute for trajectory/mask verification."""
    allowed = {r["task_id"] for r in stage_tasks(plan, stage)}
    if stage == "validation" or not records:
        raise ValueError("Training requires nonempty A/B source records")
    for record in records:
        if record["task_id"] not in allowed or record.get("official_split") != "train":
            raise ValueError("Training source violates A/B/C or official split")
        if record.get("split_manifest_sha256") != plan["manifest_sha256"]:
            raise ValueError("Unbound training source")
    return True


def verify_runtime_data(plan, root):
    """Bind future execution to the original public inputs, not just task IDs."""
    validate_split(plan)
    if plan["scope"] != "official_train":
        raise ValueError(
            "Full candidate task contents may not be inspected for execution"
        )
    data = Path(root) / "data"
    source = plan["source"]
    if (data / "version.txt").read_text().strip() != source["data_version"] or sha(
        data / "datasets/train.txt"
    ) != source["split_files"]["train"]:
        raise ValueError("Official data version/train membership changed")
    for folder, key in (
        ("base_dbs", "base_db_files"),
        ("api_docs/function_calling", "tool_schema_files"),
    ):
        actual = {
            p.name: sha(p) for p in sorted((data / folder).iterdir()) if p.is_file()
        }
        if actual != source[key]:
            raise ValueError("Base data or public tool schemas changed")
    for row in plan["rows"]:
        d = data / "tasks" / row["task_id"]
        actual = digest(
            {
                str(p.relative_to(d)): sha(p)
                for p in [d / "specs.json", *sorted((d / "dbs").glob("*.jsonl"))]
            }
        )
        if actual != row["initial_input_sha256"]:
            raise ValueError("Original task inputs changed: " + row["task_id"])
    return True


def task_ids(value):
    if isinstance(value, dict):
        if isinstance(value.get("task_id"), str):
            yield value["task_id"]
        for item in value.values():
            yield from task_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from task_ids(item)


def exposure_audit(plan, dataset, report_paths):
    dataset = Path(dataset)
    if sha(dataset / "manifest.json") != DATASET_SHA:
        raise ValueError("Historical dataset manifest changed")
    manifest = read_json(dataset / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha(dataset / name) != expected:
            raise ValueError("Historical dataset content changed: " + name)
    sources = []
    for partition, category in (
        ("train", "parameter_training"),
        ("validation", "training_validation_loss"),
    ):
        path = dataset / "model_input" / (partition + ".jsonl")
        records = [json.loads(line) for line in path.read_text().splitlines()]
        sources.append(
            {
                "path": str(path),
                "sha256": sha(path),
                "category": category,
                "task_ids": sorted({r["task_id"] for r in records}),
                "trajectories": len(records),
            }
        )
    paths = [
        (dataset / "control/attempts.json", "teacher_attempt_or_collection_selection")
    ]
    paths += [
        (Path(p), "student_execution_or_development_analysis") for p in report_paths
    ]
    for path, category in paths:
        sources.append(
            {
                "path": str(path),
                "sha256": sha(path),
                "category": category,
                "task_ids": sorted(set(task_ids(read_json(path)))),
            }
        )
    rows = []
    for row in plan["rows"]:
        categories = sorted(
            {s["category"] for s in sources if row["task_id"] in s["task_ids"]}
        )
        rows.append(
            {
                **row,
                "historical_exposure": categories,
                "unobserved_history_is_not_proof_of_no_exposure": True,
            }
        )
    validation = [r for r in rows if r["role"] == "shared_validation"]
    return {
        "format": "stage-exposure-audit-v1",
        "split_manifest_sha256": plan["manifest_sha256"],
        "historical_dataset_manifest_sha256": DATASET_SHA,
        "sources": sources,
        "rows": rows,
        "validation_overlap_counts": dict(
            Counter(c for r in validation for c in r["historical_exposure"])
        ),
        "old_checkpoint_eligible_as_new_M1": False,
        "new_training_must_start_from": "Pinned raw Qwen3-4B, not historical adapter",
        "interpretation": "Known developer exposure remains; new C is shared development validation, not unseen test.",
    }


def base_source_inventory(plan, dataset):
    """Map sealed normal demonstrations to new roles; never relabel as recovery."""
    if sha(Path(dataset) / "manifest.json") != DATASET_SHA:
        raise ValueError("Historical dataset manifest changed")
    manifest = read_json(Path(dataset) / "manifest.json")
    roles = {r["task_id"]: r["role"] for r in plan["rows"]}
    rows = []
    for part in ("train", "validation"):
        path = Path(dataset) / "model_input" / (part + ".jsonl")
        source_sha = sha(path)
        if source_sha != manifest["files"][str(path.relative_to(dataset))]:
            raise ValueError("Historical source content changed")
        for line in path.read_text().splitlines():
            record = json.loads(line)
            rows.append(
                {
                    "task_id": record["task_id"],
                    "scenario_id": record["scenario_id"],
                    "trajectory_id": record["trajectory_id"],
                    "historical_partition": part,
                    "new_role": roles[record["task_id"]],
                    "source_record_sha256": digest(record),
                    "source_jsonl_sha256": source_sha,
                    "prompt_version": record.get("actor_prompt_variant", "legacy_v1"),
                    "tool_mode": record["tool_mode"],
                }
            )
    return {
        "split_manifest_sha256": plan["manifest_sha256"],
        "historical_dataset_manifest_sha256": DATASET_SHA,
        "counts": {
            role: {
                "trajectories": sum(r["new_role"] == role for r in rows),
                "tasks": len({r["task_id"] for r in rows if r["new_role"] == role}),
            }
            for role in ROLES
        },
        "rows": rows,
        "new_dataset_built": False,
        "note": "Only A-side normal Teacher records are candidates for a new source audit. B normal trajectories are not recovery suffixes; C excluded from all parameter training. No old adapter is eligible.",
    }


# Conservative task-text annotations. Absence means unknown, not proof of absence.
APP_PATTERNS = {
    "spotify": r"\bspotify\b",
    "venmo": r"\bvenmo\b",
    "phone": r"\b(phone|text messages?|contacts?)\b",
    "file_system": r"\b(files?|folders?|director(?:y|ies)|file system)\b",
    "gmail": r"\b(gmail|emails?)\b",
    "amazon": r"\bamazon\b",
    "simple_note": r"\bsimple.?note\b",
    "splitwise": r"\bsplitwise\b",
    "todoist": r"\b(todoist|to.do)\b",
}
TYPE_PATTERNS = {
    "query": r"\b(find|show|list|which|what|search|tell)\b",
    "statistics": r"\b(total|sum|average|count|how many|most|least|highest|lowest|spent)\b",
    "create": r"\b(create|add|send|make|save|upload|schedule)\b",
    "modify": r"\b(update|change|rename|move|accept|mark|set)\b",
    "delete": r"\b(delete|remove|clear|cancel)\b",
    "multi_step_explicit": r"\b(then|after|before|first|finally)\b",
    "pagination_explicit": r"\b(page|pages|pagination|paginate)\b",
    "authentication_explicit": r"\b(log.?in|sign.?in|password|authenticate)\b",
}


def coverage(plan, root):
    rows = []
    for row in plan["rows"]:
        if row["official_split"] != "train":
            continue  # No test task inspection or annotation.
        path = Path(root) / "data/tasks" / row["task_id"] / "specs.json"
        text = read_json(path)["instruction"]
        apps = sorted(
            k for k, pattern in APP_PATTERNS.items() if re.search(pattern, text, re.I)
        )
        labels = sorted(
            k for k, pattern in TYPE_PATTERNS.items() if re.search(pattern, text, re.I)
        )
        rows.append(
            {
                "task_id": row["task_id"],
                "scenario_id": row["scenario_id"],
                "role": row["role"],
                "specs_sha256": sha(path),
                "app_mentions": apps,
                "text_labels": labels,
                "app_scope": "multiple_app_mentions"
                if len(apps) > 1
                else "one_app_mention"
                if apps
                else "unknown",
                "pagination_required": "unknown",
                "authentication_required": "unknown",
                "cross_app_dependency": "unknown",
            }
        )
    partitions = {}
    for role in [*ROLES, "all_train"]:
        selected = [r for r in rows if role == "all_train" or r["role"] == role]
        partitions[role] = {
            "tasks": len(selected),
            "scenarios": len({r["scenario_id"] for r in selected}),
            "app_mentions": dict(
                Counter(a for r in selected for a in r["app_mentions"])
            ),
            "text_labels": dict(Counter(t for r in selected for t in r["text_labels"])),
            "app_scope": dict(Counter(r["app_scope"] for r in selected)),
        }
    return {
        "method": "Deterministic public train instruction keyword annotations; heuristic mentions may under/overcount; not verified required tools or difficulty.",
        "partitions": partitions,
        "rows": rows,
        "rules_sha256": digest([APP_PATTERNS, TYPE_PATTERNS]),
    }
