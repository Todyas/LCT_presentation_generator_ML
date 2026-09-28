from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

app = FastAPI(title="Digital Presentation Designer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
# Keep direct/internal endpoints backward compatible while also accepting the
# prefix preserved by the external Nginx `location /api/` proxy.
app.include_router(router, prefix="/api")
