"""Society-only HTTP composition; reuses existing auth, projections and pages."""
from datetime import datetime, timezone
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import MutableHeaders

from arena import auth, db, guest, society_public
from arena.community_pages import fixture


class ArenaApp(FastAPI):
    async def __call__(self, scope, receive, send):
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Content-Security-Policy'] = guest.content_security_policy()
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['Referrer-Policy'] = 'no-referrer'
            await send(message)
        await super().__call__(scope, receive, secure_send)


def create_app(*, connection_factory=None, now=None, oauth_provider=None,
               turnstile_verifier=None):
    connect = connection_factory or (lambda: db.connect(os.environ['ARENA_DATABASE_URL']))
    now = now or (lambda: datetime.now(timezone.utc))
    app = ArenaApp(docs_url=None, redoc_url=None, openapi_url=None)
    auth.install(app, connect, now, oauth_provider)
    guest.install(app, connect, now, turnstile_verifier)
    society_public.install(app, connect, now)
    static = Path(__file__).with_name('static')

    @app.get('/healthz', include_in_schema=False)
    def health():
        try:
            with connect() as conn:
                if conn.execute('SELECT 1 FROM society_clock').fetchone() is None:
                    return JSONResponse({'ready': False}, status_code=503)
        except Exception:
            return JSONResponse({'ready': False}, status_code=503)
        return {'ready': True}

    @app.get('/society/config.json', include_in_schema=False)
    def config():
        return JSONResponse({'live': society_public.enabled()}, headers={'Cache-Control': 'no-store'})

    @app.get('/')
    @app.get('/society')
    def feed():
        return FileResponse(static / 'society.html')

    @app.get('/society/square')
    def square():
        return FileResponse(static / 'society-square.html')

    @app.get('/society/stats')
    def stats():
        return FileResponse(static / 'society-stats.html')

    @app.get('/society/agents/{identifier}')
    def profile(identifier: str):
        return FileResponse(static / 'society-agent.html')

    @app.get('/about')
    def about():
        return {'name': 'Klados World', 'ai_mode': os.getenv('KLADOS_AI_MODE', 'live'),
                'description': 'Four AI founders, posts and likes. Mock mode uses scripted AI responses.'}

    @app.get('/static/fixtures/{resource:path}')
    def examples(resource: str):
        return JSONResponse(fixture(static / 'fixtures', resource), headers={'Cache-Control': 'no-store'})

    @app.get('/static/{resource}')
    def asset(resource: str):
        if resource not in {'society.js', 'society-session.js', 'society.css', 'public.css'}:
            raise HTTPException(404, 'Not found')
        return FileResponse(static / resource)

    @app.middleware('http')
    async def mock_label(request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Klados-AI-Mode'] = os.getenv('KLADOS_AI_MODE', 'live')
        return response

    return app


app = create_app()
