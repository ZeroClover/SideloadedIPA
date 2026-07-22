"""Shared environment and publication-runtime helpers during package migration."""

from __future__ import annotations

import base64
import binascii
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sideloadedipa.adapters.publication import R2PublicationGateway
from sideloadedipa.adapters.publication.r2_store import R2Store
from sideloadedipa.domain import Task, TaskConfiguration
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.inspection import InspectDependencies
from sideloadedipa.pipeline.publication import VerifiedPublicationService

_DEFAULT_REVALIDATE_URL = "https://itms.zeroclover.io/api/revalidate"


@dataclass(frozen=True, slots=True)
class PipelineEnvironmentDependencies:
    inspect: InspectDependencies = InspectDependencies()
    profile_root: Path = Path("work/profiles")
    output_root: Path = Path("work/signed")
    cache_root: Path = Path("work/cache")
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)


def required_environment(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if not value:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            f"package signing requires {key}",
            remediation=f"provide {key} through the local or CI secret environment",
        )
    return value


def selected_tasks(
    configuration: TaskConfiguration,
    names: tuple[str, ...],
    *,
    scope: str,
) -> tuple[Task, ...]:
    available = {task.task_name: task for task in configuration.tasks}
    selected_names = names or tuple(available)
    if len(set(selected_names)) != len(selected_names) or any(
        name not in available for name in selected_names
    ):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            f"{scope} task selection is invalid",
            remediation="select each configured task name at most once",
            safe_details=(("task_names", selected_names),),
        )
    return tuple(available[name] for name in selected_names)


def decode_p12(environment: Mapping[str, str], destination: Path) -> str:
    encoded = required_environment(environment, "APPLE_DEV_CERT_P12_ENCODED")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "APPLE_DEV_CERT_P12_ENCODED is not valid base64",
            remediation="replace the CI secret with the complete base64-encoded P12",
        ) from error
    destination.write_bytes(content)
    destination.chmod(0o600)
    return required_environment(environment, "APPLE_DEV_CERT_PASSWORD")


def safe_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return re.sub(r"_+", "_", filename).strip("._-") or "app"


def trigger_revalidation(environment: Mapping[str, str]) -> bool:
    secret = required_environment(environment, "VERCEL_REVALIDATE_SECRET")
    endpoint = environment.get("VERCEL_REVALIDATE_URL", _DEFAULT_REVALIDATE_URL)
    request = urllib.request.Request(
        endpoint,
        headers={"X-Revalidate-Secret": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = int(response.status)
            return 200 <= status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def publication_runtime(
    configuration: TaskConfiguration,
    environment: Mapping[str, str],
) -> tuple[R2Store, VerifiedPublicationService]:
    required_environment(environment, "VERCEL_REVALIDATE_SECRET")
    store = R2Store.from_env(
        configuration.r2.ipa_prefix,
        configuration.r2.registry_key,
        environment,
    )
    gateway = R2PublicationGateway(store, lambda: trigger_revalidation(environment))
    return store, VerifiedPublicationService(gateway, configuration.publication.batch_policy)
