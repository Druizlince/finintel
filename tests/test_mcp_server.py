def test_server_module_imports_and_registers_tools():
    """Catches SDK API changes (e.g. the mcp 2.x FastMCP -> MCPServer rename) before Claude does."""
    import inspect

    from fin_mcp import server

    names = {n for n, f in inspect.getmembers(server, inspect.isfunction) if n.startswith(("get_", "search_", "sync_"))}
    assert {"get_overview", "get_cashflow", "get_investing_plan", "get_upcoming",
            "search_transactions", "get_holdings", "sync_now", "get_data_status"} <= names
    assert server.mcp is not None
