"""Region-aware EC2 on-demand hourly rates (Lambda layer).

Resolved from the AWS Price List API for the DEPLOY region (Linux, shared tenancy, no pre-installed
software) and cached on the project record (`researcher_hourly_usd` / `worker_hourly_usd`), so every
cost computation for a project uses one consistent, region-correct rate. If the Pricing API is
unavailable, a static us-east-1 table is used for that computation only (`rate_source=
static-us-east-1`, never cached on the record, so it is retried) — never silently zero.
"""

import json

import boto3
from botocore.config import Config

# us-east-1 on-demand $/h — FALLBACK ONLY (other regions are typically 10-30% higher).
STATIC_US_EAST_1 = {
    "m8g.large": 0.08976, "m9g.large": 0.1000, "m7i.large": 0.1008, "m7i.xlarge": 0.2016,
    "t3.large": 0.0832, "g4dn.xlarge": 0.526, "g5.xlarge": 1.006, "g6.xlarge": 0.8048,
    "p5.4xlarge": 6.881,
}
UNKNOWN_HOURLY_USD = 10.0  # unknown type AND no Pricing API → deliberately conservative

# The Price List API is only served from a few regions; us-east-1 answers for every region.
_PRICING_ENDPOINT_REGION = "us-east-1"
_cache: dict = {}


def _price_list_lookup(instance_type, region):
    # Tight timeouts: create_project calls this on the API-Gateway-bound (29 s) request path.
    client = boto3.client("pricing", region_name=_PRICING_ENDPOINT_REGION,
                          config=Config(connect_timeout=2, read_timeout=4, retries={"max_attempts": 2}))
    filters = [
        ("instanceType", instance_type), ("regionCode", region), ("operatingSystem", "Linux"),
        ("tenancy", "Shared"), ("preInstalledSw", "NA"), ("capacitystatus", "Used"),
        ("licenseModel", "No License required"),
    ]
    resp = client.get_products(
        ServiceCode="AmazonEC2",
        Filters=[{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters],
        MaxResults=10,
    )
    for raw in resp.get("PriceList", []):
        product = json.loads(raw) if isinstance(raw, str) else raw
        for term in product.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                usd = float(dim.get("pricePerUnit", {}).get("USD", "0") or 0)
                if usd > 0:
                    return usd
    return None


def hourly_rate(instance_type, region):
    """(usd_per_hour, source) for an instance type in a region. Never returns 0."""
    key = (instance_type, region)
    if key in _cache:
        return _cache[key]
    try:
        usd = _price_list_lookup(instance_type, region)
        if usd:
            _cache[key] = (usd, f"pricing-api:{region}")
            return _cache[key]
    except Exception:  # noqa: BLE001 — fall back below
        pass
    if instance_type in STATIC_US_EAST_1:
        return STATIC_US_EAST_1[instance_type], "static-us-east-1"
    return UNKNOWN_HOURLY_USD, "unknown-type-fallback"
