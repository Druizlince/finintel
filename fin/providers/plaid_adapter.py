"""The only module that talks to Plaid. Read-only products only (no Auth, no Transfer)."""

from __future__ import annotations

import json
import logging
from typing import Any

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from fin import secrets
from fin.config import get_settings

log = logging.getLogger(__name__)

SANDBOX_INSTITUTION = "ins_109508"  # "First Platypus Bank" - Plaid's fake test bank


class PlaidError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"Plaid error {code}: {message}")
        self.code = code


def token_secret_name(item_id: str) -> str:
    return f"plaid_token:{item_id}"


def _wrap(exc: plaid.ApiException) -> PlaidError:
    try:
        body = json.loads(exc.body or "{}")
    except ValueError:
        body = {}
    return PlaidError(
        str(body.get("error_code") or exc.status), body.get("error_message") or "request failed"
    )


class PlaidAdapter:
    def __init__(self, client: Any = None, env: str | None = None) -> None:
        self.env = env or get_settings().plaid_env
        self._client = client or self._build_client(self.env)

    @staticmethod
    def _build_client(env: str) -> plaid_api.PlaidApi:
        if env == "sandbox":
            host, secret_name = plaid.Environment.Sandbox, "plaid_secret"
        else:
            host, secret_name = plaid.Environment.Production, "plaid_secret_production"
        cfg = plaid.Configuration(
            host=host,
            api_key={
                "clientId": secrets.require_secret("plaid_client_id"),
                "secret": secrets.require_secret(secret_name),
            },
        )
        return plaid_api.PlaidApi(plaid.ApiClient(cfg))

    def _call(self, fn: Any, request: Any) -> dict[str, Any]:
        try:
            return fn(request).to_dict()
        except plaid.ApiException as exc:
            raise _wrap(exc) from None

    # --- linking -------------------------------------------------------------
    def sandbox_public_token(self, institution_id: str = SANDBOX_INSTITUTION) -> str:
        if self.env != "sandbox":
            raise RuntimeError("sandbox_public_token is only allowed when FIN_PLAID_ENV=sandbox")
        req = SandboxPublicTokenCreateRequest(
            institution_id=institution_id, initial_products=[Products("transactions")]
        )
        return self._call(self._client.sandbox_public_token_create, req)["public_token"]

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        r = self._call(
            self._client.item_public_token_exchange,
            ItemPublicTokenExchangeRequest(public_token=public_token),
        )
        return r["access_token"], r["item_id"]

    def create_link_token(self) -> str:
        req = LinkTokenCreateRequest(
            client_name="finintel (personal)",
            language="en",
            country_codes=[CountryCode("US")],
            user=LinkTokenCreateRequestUser(client_user_id="finintel-owner"),
            products=[Products("transactions")],
            # Card due dates / minimum payments where the bank supports it; never blocks linking.
            optional_products=[Products("liabilities")],
            transactions=LinkTokenTransactions(days_requested=730),  # 2 years of history
        )
        return self._call(self._client.link_token_create, req)["link_token"]

    def institution_info(self, access_token: str) -> tuple[str | None, str]:
        """Returns (plaid institution id, display name)."""
        item = self._call(self._client.item_get, ItemGetRequest(access_token=access_token))["item"]
        inst_id = item.get("institution_id")
        if not inst_id:
            return None, "Unknown institution"
        r = self._call(
            self._client.institutions_get_by_id,
            InstitutionsGetByIdRequest(institution_id=inst_id, country_codes=[CountryCode("US")]),
        )
        return inst_id, r["institution"]["name"]

    def remove_item(self, access_token: str) -> None:
        self._call(self._client.item_remove, ItemRemoveRequest(access_token=access_token))

    # --- data ----------------------------------------------------------------
    def get_accounts(self, access_token: str) -> list[dict[str, Any]]:
        return self._call(self._client.accounts_get, AccountsGetRequest(access_token=access_token))[
            "accounts"
        ]

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        req = TransactionsSyncRequest(access_token=access_token, cursor=cursor or "")
        return self._call(self._client.transactions_sync, req)
