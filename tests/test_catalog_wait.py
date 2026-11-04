from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tennis_overview.browser_runtime import BrowserReadError, ReadNetwork, wait_for_catalog


@pytest.mark.benchmark
def test_known_membership_denial_does_not_start_card_wait():
    network = ReadNetwork(True)
    network.membership_required = True
    page = Mock()
    with pytest.raises(BrowserReadError, match="^membership_required$"):
        asyncio.run(wait_for_catalog(page, network))
    page.locator.assert_not_called()


@pytest.mark.benchmark
@pytest.mark.parametrize("outcome", ["denied", "visible", "timeout", "cancelled", "both"])
def test_catalog_wait_reaps_both_tasks(outcome):
    async def run():
        network = ReadNetwork(True)
        started = asyncio.Event()
        release = asyncio.Event()
        card_tasks = []
        denial_tasks = []
        original_wait = network.membership_denied.wait

        async def denial_wait():
            denial_tasks.append(asyncio.current_task())
            return await original_wait()

        network.membership_denied.wait = denial_wait

        async def card_wait(*, timeout):
            assert timeout == 15000
            card_tasks.append(asyncio.current_task())
            started.set()
            await release.wait()
            if outcome == "timeout":
                raise PlaywrightTimeoutError("synthetic card timeout")

        page = Mock()
        page.locator.return_value.first.wait_for = AsyncMock(side_effect=card_wait)
        task = asyncio.create_task(wait_for_catalog(page, network))
        await asyncio.wait_for(started.wait(), timeout=1)
        if outcome in {"denied", "both"}:
            network.membership_required = True
            network.membership_denied.set()
        if outcome in {"visible", "timeout", "both"}:
            release.set()
        if outcome == "cancelled":
            task.cancel()

        if outcome in {"denied", "both"}:
            with pytest.raises(BrowserReadError, match="^membership_required$"):
                await asyncio.wait_for(task, timeout=1)
        elif outcome == "timeout":
            with pytest.raises(PlaywrightTimeoutError, match="synthetic card timeout"):
                await asyncio.wait_for(task, timeout=1)
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, timeout=1)
        assert len(card_tasks) == len(denial_tasks) == 1
        assert all(waiter.done() for waiter in card_tasks + denial_tasks)
        if outcome in {"denied", "cancelled"}:
            assert card_tasks[0].cancelled()
        if outcome in {"visible", "timeout", "cancelled"}:
            assert denial_tasks[0].cancelled()
        page.locator.assert_called_once_with('[data-testid^="events-list-card-name-"]')

    asyncio.run(run())
