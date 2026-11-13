"""Versioned AppWorld dependency and execution-error contract, controller only."""

import importlib.metadata
import re
from datetime import datetime

from .io import digest, write_json

REVISION = "appworld_naive_datetime_20261113"
VERSIONS = {
    "sqlmodel": "0.0.24",
    "sqlalchemy": "2.0.52",
    "pydantic": "2.12.3",
    "freezegun": "1.5.1",
}


class ExecutionEnvironmentError(RuntimeError):
    pass


def verify_environment(revision):
    if revision != REVISION:
        raise ValueError("Unknown AppWorld environment contract")
    actual = {name: importlib.metadata.version(name) for name in VERSIONS}
    if actual != VERSIONS:
        raise ValueError("AppWorld dependency versions differ: " + repr(actual))
    from sqlalchemy import create_engine, insert, select
    from sqlmodel import Field, SQLModel

    # A separate registry avoids modifying AppWorld's model metadata.
    from sqlalchemy import MetaData

    class Base(SQLModel):
        metadata = MetaData()

    class Probe(Base, table=True):
        id: int | None = Field(default=None, primary_key=True)
        timestamp: datetime

    engine = create_engine("sqlite://")
    value = datetime(2023, 5, 18, 12)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(insert(Probe).values(timestamp=value))
            if connection.execute(select(Probe.timestamp)).scalar_one() != value:
                raise ValueError("AppWorld naive datetime storage changed")
    finally:
        engine.dispose()
    return {"revision": revision, "versions": actual, "naive_datetime_roundtrip": True}


def execution_environment_error(result):
    """Inspect real error envelopes, never words in ordinary task data."""
    if not isinstance(result, str) or not result.lstrip().startswith(
        "Execution failed."
    ):
        return None
    # API 4xx responses (expired card, missing object, bad credentials) remain
    # ordinary task observations. Internal database/runtime failures do not.
    match = re.search(
        r"^(sqlalchemy\.exc\.[A-Za-z]+|sqlite3\.[A-Za-z]+|"
        r"MemoryError|OSError|ImportError|ModuleNotFoundError|"
        r"pydantic_core\._pydantic_core\.ValidationError):",
        result,
        re.MULTILINE,
    )
    if match:
        return {"category": "worker_environment", "exception_type": match[1]}
    match = re.search(r"^Exception: Response status code is (5\d\d)\b", result, re.M)
    if match:
        return {"category": "worker_environment", "http_status": int(match[1])}
    return None


def require_usable_execution(result, name, directory):
    fault = execution_environment_error(result)
    if fault is None:
        return
    # Keep the returned observation before aborting, alongside the existing
    # call intent and interrupted-batch state. It must not become a SFT target.
    write_json(
        directory / "execution_environment_error.json",
        {
            "revision": REVISION,
            "tool_name": name,
            "result": result,
            "result_sha256": digest(result),
            "fault": fault,
            "automatic_retry": False,
        },
    )
    raise ExecutionEnvironmentError(
        "Tool execution infrastructure failure: " + str(fault)
    )
