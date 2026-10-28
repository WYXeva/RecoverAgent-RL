"""Pinned AppWorld Unified adapter. Only public function schemas reach the Actor."""

import importlib.metadata
import json
import random
import re
import shutil
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from . import APPWORLD_REVISION
from .io import canonical, digest, files_manifest


def tuple_tree(value):
    return tuple(tuple_tree(x) for x in value) if isinstance(value, list) else value


def call_code(name, arguments, tools):
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    if name not in schemas or not re.fullmatch(
        r"[a-z][a-z0-9_]*__[a-z][a-z0-9_]*", name
    ):
        raise ValueError("Tool is not in the visible public schema")
    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    canonical(arguments)  # Reject NaN/Infinity and non-JSON objects.
    schema = dict(schemas[name], additionalProperties=False)
    Draft202012Validator(schema).validate(arguments)
    # Name is whitelisted and arguments are a parsed JSON tree, then Python literals.
    return f"print({name}(**{arguments!r}))", arguments


class AppWorldAdapter:
    def __init__(
        self,
        root,
        task_id,
        experiment_name,
        seed=42,
        max_interactions=100,
        timeout=60,
        *,
        research=None,
    ):
        from appworld import AppWorld, load_task_ids
        from appworld.common.path_store import path_store
        from appworld import update_root

        direct = importlib.metadata.distribution("appworld").read_text(
            "direct_url.json"
        )
        if not direct or APPWORLD_REVISION not in direct:
            raise ValueError(
                "AppWorld must be installed from the pinned source revision"
            )
        update_root(str(Path(root).resolve()))
        if research is not None:
            from .research_access import authorize_task

            self.official_split = authorize_task(root, task_id, research)
        elif task_id not in load_task_ids("train"):
            raise ValueError("P0–P2 accepts official train tasks only")
        output = (
            Path(path_store.experiment_outputs) / experiment_name / "tasks" / task_id
        )
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite AppWorld output: {output}")
        self.task_id = task_id
        self.experiment_name = experiment_name
        self.world = AppWorld(
            task_id=task_id,
            experiment_name=experiment_name,
            load_ground_truth=False,
            include_direct_functions=True,
            direct_function_separator="__",
            random_seed=seed,
            max_interactions=max_interactions,
            max_api_calls_per_interaction=1,
            timeout_seconds=timeout,
            allow_datetime_change=False,
            add_login_shortcut=False,
        )
        self.catalogue = self.world.task.api_docs.function_calling()
        if any(t["function"]["name"].startswith("admin__") for t in self.catalogue):
            raise ValueError("Admin APIs must never be exposed")
        self.versions = {
            "appworld": importlib.metadata.version("appworld"),
            "revision": APPWORLD_REVISION,
            "installation_source": json.loads(direct),
            "data_version": self.world.task.db_version,
            "task_input_files": files_manifest(
                Path(root) / "data" / "tasks" / task_id, exclude_python_cache=True
            ),
            "base_db_files": files_manifest(Path(root) / "data" / "base_dbs"),
        }

    def public_task(self):
        task = self.world.task
        return {
            "instruction": task.instruction,
            "supervisor": dict(task.supervisor),
            "datetime": str(task.datetime),
            "app_descriptions": task.app_descriptions,
        }

    def initial_tools(self):
        # Public discovery and supervisor tools. Actor chooses additional APIs by requesting docs.
        return [
            t
            for t in self.catalogue
            if t["function"]["name"].split("__")[0] in {"api_docs", "supervisor"}
        ]

    def execute(self, name, arguments, tools):
        code, parsed = call_code(name, arguments, tools)
        result = self.world.execute(code)
        updated = list(tools)
        if name == "api_docs__show_api_doc" and "Execution failed" not in result:
            selected = f"{parsed.get('app_name', '').strip().lower()}__{parsed.get('api_name', '').strip().lower()}"
            existing = {t["function"]["name"] for t in tools}
            updated.extend(
                t
                for t in self.catalogue
                if t["function"]["name"] == selected and selected not in existing
            )
        return result, updated

    def fingerprint(self):
        """Hash normalized logical records, not SQLite file bytes or cached hashes."""
        tables = {}
        for app, models in self.world.models.items():
            for name, model in models.items():
                if name == "SQLModel":
                    continue
                records = [
                    record.model_dump(mode="json") for record in model.find_all()
                ]
                tables[f"{app}.{name}"] = sorted(records, key=canonical)
        return digest(tables)

    def runtime(self):
        if datetime.now().replace(tzinfo=None) != self.world.task.datetime.replace(
            tzinfo=None
        ):
            raise ValueError("AppWorld clock is not frozen at the task time")
        return {
            "python_random": json.loads(json.dumps(random.getstate())),
            "num_interactions": self.world.num_interactions,
            "task_datetime": str(self.world.task.datetime),
            "allow_datetime_change": False,
            "shell_variables": "none; direct calls only",
        }

    def restore_runtime(self, runtime):
        if (
            runtime["task_datetime"] != str(self.world.task.datetime)
            or runtime["allow_datetime_change"]
        ):
            raise ValueError("Unsupported task clock")
        self.world.num_interactions = runtime["num_interactions"]
        random.setstate(tuple_tree(runtime["python_random"]))

    def export_state(self, state_id, destination):
        self.world.save_state(state_id)  # Do not depend on the return value.
        shutil.copytree(
            Path(self.world.output_checkpoints_directory) / state_id, destination
        )

    def import_state(self, source, state_id):
        target = Path(self.world.output_checkpoints_directory) / state_id
        if target.exists():
            if files_manifest(source) != files_manifest(target):
                raise ValueError("Worker import was modified")
        else:
            shutil.copytree(source, target)
        self.world.load_state(state_id)
        # Pinned upstream load_state closes every clock, but only recreates the requester
        # clock. Recreate the world's own clock too; otherwise close() double-stops
        # the stale freezer (real probe: AttributeError fake_names / empty freezer stack).
        self.world._set_datetime()

    def save_terminal(self):
        self.world.save()

    def close(self):
        self.world.close()
