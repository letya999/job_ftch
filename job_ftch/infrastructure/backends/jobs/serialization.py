"""Serialization helpers for persistent backends."""

from job_ftch.domain import JobGroup, JobRecord


def dump_job(job: JobRecord) -> str:
    return job.model_dump_json(by_alias=True)


def load_job(raw: str) -> JobRecord:
    return JobRecord.model_validate_json(raw)


def dump_group(group: JobGroup) -> str:
    return group.model_dump_json(by_alias=True)


def load_group(raw: str) -> JobGroup:
    return JobGroup.model_validate_json(raw)
