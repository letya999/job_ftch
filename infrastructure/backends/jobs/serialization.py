"""Serialization helpers for persistent backends."""

from domain import Job, JobGroup


def dump_job(job: Job) -> str:
    return job.model_dump_json(by_alias=True)


def load_job(raw: str) -> Job:
    return Job.model_validate_json(raw)


def dump_group(group: JobGroup) -> str:
    return group.model_dump_json(by_alias=True)


def load_group(raw: str) -> JobGroup:
    return JobGroup.model_validate_json(raw)
