"""The only module that talks to SnapTrade (Fidelity and other brokerages). Read-only calls only."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fin import secrets
from fin.providers.snaptrade_mapping import plain


class SnapTradeAdapter:
    def __init__(self, client: Any = None) -> None:
        if client is None:
            from snaptrade_client import SnapTrade, SnapTradeAuth

            from fin.logsetup import quiet_sdk_loggers

            quiet_sdk_loggers()

            client = SnapTrade(
                auth=SnapTradeAuth.personal_api_key(
                    client_id=secrets.require_secret("snaptrade_client_id"),
                    consumer_key=secrets.require_secret("snaptrade_consumer_key"),
                )
            )
        self._c = client

    @staticmethod
    def _body(resp: Any) -> Any:
        return plain(getattr(resp, "body", resp))

    def connection_url(self) -> str:
        """A one-time Connection Portal link for connecting a brokerage (read-only)."""
        return self._body(self._c.authentication.login_snap_trade_user())["redirectURI"]

    def list_accounts(self) -> list[dict[str, Any]]:
        return self._body(self._c.account_information.list_user_accounts()) or []

    def get_balances(self, account_id: str) -> list[dict[str, Any]]:
        return self._body(
            self._c.account_information.get_user_account_balance(account_id=account_id)
        ) or []

    def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        ai = self._c.account_information
        fn = getattr(ai, "get_user_account_positions", None) or getattr(
            ai, "get_all_account_positions"
        )
        body = self._body(fn(account_id=account_id))
        return (body.get("results") or body.get("data") or []) if isinstance(body, dict) else body or []

    def get_activities(self, account_id: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset, limit = 0, 1000
        while True:
            body = self._body(
                self._c.account_information.get_account_activities(
                    account_id=account_id, start_date=start.isoformat(),
                    end_date=end.isoformat(), offset=offset, limit=limit,
                )
            )
            page = body.get("data", []) if isinstance(body, dict) else (body or [])
            out.extend(page)
            if len(page) < limit:
                return out
            offset += limit
