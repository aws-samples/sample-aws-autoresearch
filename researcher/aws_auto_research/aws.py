"""Thin AWS helpers used by the runners: EC2 instance lifecycle + SSH key retrieval.

boto3 with adaptive retries replaces the `aws ec2 …`/`aws ssm …` CLI calls. S3 artifact uploads are
NOT here — they run on the compute box itself via the backend (`aws s3 cp …`), where the artifacts live.
"""

from __future__ import annotations

import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

_CFG = Config(retries={"mode": "adaptive", "max_attempts": 5})


class CapacityError(RuntimeError):
    """EC2 had no capacity for the instance type (InsufficientInstanceCapacity)."""


class UnrecoverableInstanceError(RuntimeError):
    """Instance is terminated/shutting-down — cannot be started."""


def ec2(region: str):
    return boto3.client("ec2", region_name=region, config=_CFG)


def ssm(region: str):
    return boto3.client("ssm", region_name=region, config=_CFG)


def instance_state(region: str, instance_id: str) -> str:
    try:
        r = ec2(region).describe_instances(InstanceIds=[instance_id])
        return r["Reservations"][0]["Instances"][0]["State"]["Name"]
    except (ClientError, IndexError, KeyError):
        return "unknown"


def instance_private_ip(region: str, instance_id: str) -> str | None:
    r = ec2(region).describe_instances(InstanceIds=[instance_id])
    return r["Reservations"][0]["Instances"][0].get("PrivateIpAddress")


def start_instance(region: str, instance_id: str) -> None:
    """Start the instance. Raises CapacityError on InsufficientInstanceCapacity."""
    try:
        ec2(region).start_instances(InstanceIds=[instance_id])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "InsufficientInstanceCapacity":
            raise CapacityError(str(e)) from e
        raise


def stop_instance(region: str, instance_id: str) -> None:
    """Stop the instance (idempotent — errors swallowed)."""
    try:
        ec2(region).stop_instances(InstanceIds=[instance_id])
    except ClientError:
        pass


def wait_state(region: str, instance_id: str, target: str, timeout_s: int, poll_s: int = 10,
               sleep=time.sleep) -> bool:
    """Poll until state == target or timeout. Bounded — never the CLI's 10-min blocking waiter."""
    waited = 0
    while waited < timeout_s:
        if instance_state(region, instance_id) == target:
            return True
        sleep(poll_s)
        waited += poll_s
    return instance_state(region, instance_id) == target


def fetch_ssh_key(region: str, key_param: str, dest: str | None = None) -> str:
    """Fetch the worker SSH private key from an SSM SecureString to a 0600 file; return its path."""
    dest = dest or os.path.expanduser("~/.ssh/worker_key")
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        val = ssm(region).get_parameter(Name=key_param, WithDecryption=True)["Parameter"]["Value"]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write(val)
        os.chmod(dest, 0o600)
    return dest
