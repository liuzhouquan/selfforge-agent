"""Independent checks for empty async batch inputs."""

import asyncio

from pocketflow import AsyncBatchNode, AsyncParallelBatchNode


class EmptyAsyncBatchNode(AsyncBatchNode):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def exec_async(self, item):
        self.calls += 1
        return item


class EmptyAsyncParallelBatchNode(AsyncParallelBatchNode):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def exec_async(self, item):
        self.calls += 1
        return item


def test_async_batch_node_treats_none_as_empty() -> None:
    node = EmptyAsyncBatchNode()
    assert asyncio.run(node._exec(None)) == []
    assert node.calls == 0


def test_async_parallel_batch_node_treats_none_as_empty() -> None:
    node = EmptyAsyncParallelBatchNode()
    assert asyncio.run(node._exec(None)) == []
    assert node.calls == 0
