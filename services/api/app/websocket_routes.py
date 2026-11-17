"""WebSocket endpoint for live course snapshots."""

import asyncio

from fastapi import APIRouter, WebSocket

from .in_memory_registry import InMemoryRegistry

router = APIRouter(prefix="/api")


@router.websocket("/courses/{course_id}/ws")
async def course_updates(websocket: WebSocket, course_id: str) -> None:
    # Send the current course snapshot and push updates while the client is connected.
    registry: InMemoryRegistry | None = websocket.app.state.registry
    if registry is None:
        await websocket.close(code=1013)
        return
    if course_id not in registry.courses:
        await websocket.close(code=1008)
        return

    changed = asyncio.Event()
    changed.set()
    registry.listeners[course_id].add(changed)
    tasks: set[asyncio.Task] = set()
    try:
        await websocket.accept()
        incoming = asyncio.create_task(websocket.receive())
        update = asyncio.create_task(changed.wait())
        tasks = {incoming, update}
        while True:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if incoming in done:
                if incoming.result()["type"] == "websocket.disconnect":
                    return
                tasks.remove(incoming)
                incoming = asyncio.create_task(websocket.receive())
                tasks.add(incoming)
            if update in done:
                changed.clear()
                await websocket.send_json(registry.snapshot(course_id).model_dump(mode="json"))
                tasks.remove(update)
                update = asyncio.create_task(changed.wait())
                tasks.add(update)
    except OSError:
        return
    finally:
        registry.listeners[course_id].discard(changed)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
