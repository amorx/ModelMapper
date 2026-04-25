from fastapi import Request

from src.services.storage import Storage


def get_storage(request: Request) -> Storage:
    storage = getattr(request.app.state, "storage", None)
    if not isinstance(storage, Storage):
        storage = Storage()
        storage.init_db()
        request.app.state.storage = storage
    return storage
