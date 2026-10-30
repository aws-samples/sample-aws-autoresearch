"""
users: per-user budget + the budget-admin surface.

- GET /me                       any authed    -> the caller's user row (lazy-created at budget 0)
- GET /users                    budget-admin   -> every pool user + their budget (Cognito ListUsers ⋈ table)
- PUT /users/{sub}/budget       budget-admin   -> set a user's budget (>= 0)

`budget` is the spendable USD balance: creating a project reserves its max budget (create_project),
deleting refunds the unspent part (delete_project). Everyone starts at 0; the first budget-admin is
seeded manually (see README). Identity comes from the Cognito JWT (see ar_authz).
"""

import json
import os
from decimal import Decimal

import boto3

from ar_authz import caller, forbidden

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
USERS_TABLE = os.environ["USERS_TABLE"]
USER_POOL_ID = os.environ["USER_POOL_ID"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _get_row(sub):
    return dynamodb.Table(USERS_TABLE).get_item(Key={"user_sub": sub}).get("Item")


def _ensure_row(who):
    """Load the caller's row, creating it at budget 0 (non-admin) on first sight."""
    row = _get_row(who["sub"])
    if row:
        return row
    row = {"user_sub": who["sub"], "username": who["username"], "budget": Decimal(0), "is_budget_admin": False}
    dynamodb.Table(USERS_TABLE).put_item(Item=row, ConditionExpression="attribute_not_exists(user_sub)")
    return row


def _is_admin(who):
    row = _get_row(who["sub"])
    return bool(row and row.get("is_budget_admin"))


def _public(row):
    return {
        "user_sub": row["user_sub"],
        "username": row.get("username", ""),
        "budget": _num(row.get("budget", 0)),
        "is_budget_admin": bool(row.get("is_budget_admin", False)),
    }


def handler(event, context):
    route = event.get("routeKey", "")
    who = caller(event)

    # --- the caller's own budget (any authenticated user) ---
    if route.endswith("/me") and route.startswith("GET"):
        return _resp(200, _public(_ensure_row(who)))

    # --- everything else is budget-admin only ---
    if not _is_admin(who):
        return forbidden("Budget administration is restricted to budget administrators.")

    if route.endswith("/users") and route.startswith("GET"):
        # All pool users ⋈ their budgets, so an admin can fund a user who hasn't signed in yet.
        budgets = {r["user_sub"]: r for r in dynamodb.Table(USERS_TABLE).scan().get("Items", [])}
        out, token = [], None
        while True:
            kw = {"UserPoolId": USER_POOL_ID, "Limit": 60}
            if token:
                kw["PaginationToken"] = token
            resp = cognito.list_users(**kw)
            for u in resp.get("Users", []):
                attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
                sub = attrs.get("sub", "")
                row = budgets.get(sub, {})
                out.append({
                    "user_sub": sub,
                    "username": u.get("Username", ""),
                    "budget": _num(row.get("budget", 0)),
                    "is_budget_admin": bool(row.get("is_budget_admin", False)),
                })
            token = resp.get("PaginationToken")
            if not token:
                break
        out.sort(key=lambda x: x["username"].lower())
        return _resp(200, {"users": out})

    if route.endswith("/users/{sub}/budget") and route.startswith("PUT"):
        target = (event.get("pathParameters") or {}).get("sub", "")
        body = json.loads(event.get("body") or "{}")
        try:
            budget = Decimal(str(body["budget"]))
        except Exception:  # noqa: BLE001
            return _resp(400, {"error": "Provide a numeric 'budget'."})
        if budget < 0:
            return _resp(400, {"error": "Budget must be >= 0."})
        username = (body.get("username") or "").strip()
        expr, names, vals = "SET budget = :b", {}, {":b": budget}
        if username:
            expr += ", username = :u"
            vals[":u"] = username
        dynamodb.Table(USERS_TABLE).update_item(
            Key={"user_sub": target}, UpdateExpression=expr, ExpressionAttributeValues=vals)
        return _resp(200, {"user_sub": target, "budget": _num(budget)})

    return _resp(404, {"error": f"Unknown route {route}"})
