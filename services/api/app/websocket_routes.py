"""WebSocket endpoint for live course snapshots."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket
from sqlalchemy.exc import SQLAlchemyError

from .in_memory_registry import InMemoryRegistry
from .postgres_registry import PostgresRegistry

router = APIRouter(prefix="/api")


async def wait_for_course_update(
    notification_queue: asyncio.Queue[str | None], registry: PostgresRegistry, course_id: str
) -> None:
    # Wait for a committed update to this course or for a full resync signal.
    while True:
        resource_id = await notification_queue.get()
        notification_queue.task_done()
        if resource_id is None:
            return
        try:
            resource_uuid = UUID(resource_id)
        except ValueError:
            continue
        if await registry.course_for_resource(resource_uuid) == course_id:
            return


@router.websocket("/courses/{course_id}/ws")
async def course_updates(websocket: WebSocket, course_id: str) -> None:
    # Send the current course snapshot and push updates while the client is connected.
    registry: InMemoryRegistry | PostgresRegistry | None = websocket.app.state.registry
    if registry is None:
        await websocket.close(code=1013)
        return
    preview_changed = asyncio.Event()
    socket_tasks: set[asyncio.Task] = set()
    accepted = False
    async with AsyncExitStack() as subscriptions:
        wait_for_update = preview_changed.wait
        if isinstance(registry, InMemoryRegistry) and course_id in registry.listeners:
            listeners = registry.listeners[course_id]
            listeners.add(preview_changed)
            subscriptions.callback(listeners.discard, preview_changed)
        elif isinstance(registry, PostgresRegistry) and websocket.app.state.resource_notifications:
            notification_queue = await subscriptions.enter_async_context(websocket.app.state.resource_notifications.subscribe())

            async def wait_for_update():
                # Wait for this socket's course to change in PostgreSQL.
                await wait_for_course_update(notification_queue, registry, course_id)

        try:
            snapshot = await registry.get_snapshot(course_id)
            await websocket.accept()
            accepted = True
            await websocket.send_json(snapshot.model_dump(mode="json"))
            receive_task = asyncio.create_task(websocket.receive())
            refresh_task = asyncio.create_task(wait_for_update())
            socket_tasks = {receive_task, refresh_task}
            while True:
                completed_tasks, _ = await asyncio.wait(socket_tasks, return_when=asyncio.FIRST_COMPLETED)
                if receive_task in completed_tasks:
                    if receive_task.result()["type"] == "websocket.disconnect":
                        return
                    socket_tasks.remove(receive_task)
                    receive_task = asyncio.create_task(websocket.receive())
                    socket_tasks.add(receive_task)
                if refresh_task in completed_tasks:
                    refresh_task.result()
                    preview_changed.clear()
                    snapshot = await registry.get_snapshot(course_id)
                    await websocket.send_json(snapshot.model_dump(mode="json"))
                    socket_tasks.remove(refresh_task)
                    refresh_task = asyncio.create_task(wait_for_update())
                    socket_tasks.add(refresh_task)
        except HTTPException:
            await websocket.close(code=1008)
        except (SQLAlchemyError, TimeoutError):
            await websocket.close(code=1013)
        except OSError:
            if not accepted:
                await websocket.close(code=1013)
        finally:
            for task in socket_tasks:
                task.cancel()
            await asyncio.gather(*socket_tasks, return_exceptions=True)
