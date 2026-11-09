"""Use the original locked Teacher/exporter for an existing snapshot version."""

import json
import os
from pathlib import Path
import subprocess


def environment(source):
    return {**os.environ, "PYTHONPATH": str(Path(source) / "src")}


def call(python, source, expected_lock, action, payload):
    if action not in {"configuration", "suffix"}:
        raise ValueError("Unsupported original-source operation")
    script = """
import json,sys
from recoveragent.v2_runtime import verify_lock
p=json.loads(sys.argv[1])
if verify_lock()['lock_sha256'] != p['expected_lock']:
    raise ValueError('Original Teacher source changed')
if p['action']=='configuration':
    from recoveragent.v2_teacher import configuration
    result=configuration(**p['payload'])
else:
    from recoveragent.v2_data import prepare_recovery
    from recoveragent.v2_model import load_tokenizer
    result=prepare_recovery(p['payload']['directory'],load_tokenizer(p['payload']['identity']))
print(json.dumps(result))
"""
    return json.loads(
        subprocess.check_output(
            [
                python,
                "-c",
                script,
                json.dumps(
                    {
                        "expected_lock": expected_lock,
                        "action": action,
                        "payload": payload,
                    }
                ),
            ],
            cwd=source,
            env=environment(source),
            text=True,
            timeout=120,
        )
    )
