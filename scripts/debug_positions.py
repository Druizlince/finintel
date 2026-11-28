"""Diagnose why Fidelity positions come back empty. Read-only; prints structure, not secrets."""
import json

from fin.providers.snaptrade_adapter import SnapTradeAdapter
from fin.providers.snaptrade_mapping import plain


def shape(x, depth=0):
    if isinstance(x, dict):
        return {k: shape(v, depth + 1) if depth < 3 else type(v).__name__ for k, v in x.items()}
    if isinstance(x, list):
        return [f"list[{len(x)}]"] + ([shape(x[0], depth + 1)] if x else [])
    return type(x).__name__


ad = SnapTradeAdapter()
ai = ad._c.account_information
for a in ad.list_accounts():
    aid = a["id"]
    print("=== account", str(a.get("number", ""))[-4:])
    for name in ("get_user_account_positions", "get_all_account_positions", "get_user_holdings"):
        fn = getattr(ai, name, None)
        if not fn:
            print(name, "-> not in SDK"); continue
        try:
            body = plain(getattr(fn(account_id=aid), "body", None))
            print(name, "->", json.dumps(shape(body), indent=1)[:1500])
        except Exception as e:  # noqa: BLE001
            print(name, "-> ERROR", type(e).__name__, str(e)[:200])
