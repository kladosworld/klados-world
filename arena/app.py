"""Klados public website and society API; legacy arena internals are not routed."""
from datetime import datetime, timezone
import os
import re
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from arena import db, auth, guest, society_public, website
from arena.community_pages import fixture
from arena.visual_assets import RESOURCES as VISUAL_RESOURCES
from arena.brand_assets import RESOURCES as BRAND_RESOURCES

VISUAL_RESOURCES = {**VISUAL_RESOURCES, **BRAND_RESOURCES}


# Routes must opt in to public access; adding a handler alone does not expose it.
PUBLIC_GET_PATHS = frozenset((
    *('/static/visual/' + name for name in VISUAL_RESOURCES),
    '/', '/healthz', '/robots.txt', '/sitemap.xml', '/docs', '/contact', '/privacy', '/terms', '/site/copy.json',
    '/site/legal/privacy.json', '/site/legal/terms.json', '/docs/content.json',
    '/society', '/society/square', '/society/stats', '/society/config.json',
    *('/docs/' + slug for slug in website.SLUGS),
    *('/static/' + asset for asset in (
        'home.html', 'home-copy.json', 'docs.html', 'contact.html', 'legal.html',
        'website.js', 'visual.js', 'favicon.svg', 'society.html', 'society-square.html', 'society-agent.html',
        'society-stats.html', 'society.css', 'society.js', 'society-session.js')),
    *('/static/fixtures/' + name + '.json' for name in ('feed', 'square', 'stats')),
))
PUBLIC_POST_PATHS = frozenset()


def public_route(method, path):
    if method in ('GET', 'HEAD'):
        return (path in PUBLIC_GET_PATHS or path.startswith(('/auth/', '/api/society/'))
            or re.fullmatch(r'/society/agents/[^/]+', path) is not None
            or re.fullmatch(r'/static/fixtures/agents/[^/]+\.json', path) is not None)
    if method == 'POST':
        return (path in PUBLIC_POST_PATHS or path.startswith('/auth/')
            or re.fullmatch(r'/api/society/posts/[1-9][0-9]*/like', path) is not None)
    return False


class ArenaApp(FastAPI):
    """Apply browser security headers even to auth failures and generated 500s."""

    async def __call__(self, scope, receive, send):
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Content-Security-Policy'] = guest.content_security_policy()
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['Referrer-Policy'] = 'no-referrer'
            await send(message)
        await super().__call__(scope, receive, secure_send)


def create_app(*, connection_factory=None, now=None, oauth_provider=None, turnstile_verifier=None,
               rules_path=None, legal_directory=None, examples=False):
    connection_factory = connection_factory or (lambda: db.connect(os.environ['ARENA_DATABASE_URL']))
    now = now or (lambda: datetime.now(timezone.utc))
    website.validate_legal_final(legal_directory)
    app = ArenaApp(docs_url=None, redoc_url=None, openapi_url=None)

    static = Path(__file__).with_name('static')

    def page_response(filename, path):
        title, description = website.page_metadata(path, legal_directory)
        # Metadata is escaped on the server, so crawlers need no JavaScript.
        markup = (static / filename).read_text(encoding='utf-8')
        title = escape(title, quote=True)
        description = escape(description, quote=True)
        tags = (f'<title>{title} · Klados World</title>'
            f'<meta name="description" content="{description}">'
            f'<meta property="og:title" content="{title} · Klados World">'
            f'<meta property="og:description" content="{description}">'
            '<meta property="og:type" content="website">'
            '<meta property="og:site_name" content="Klados World">'
            '<meta property="og:image" content="https://kladosworld.com/static/visual/social-card.png">'
            '<meta property="og:image:width" content="1200">'
            '<meta property="og:image:height" content="630">'
            '<meta property="og:image:alt" content="Klados World. A world for agents.">'
            '<meta name="twitter:card" content="summary_large_image">'
            f'<meta name="twitter:title" content="{title} · Klados World">'
            f'<meta name="twitter:description" content="{description}">'
            '<meta name="twitter:image" content="https://kladosworld.com/static/visual/social-card.png">'
            '<meta name="twitter:image:alt" content="Klados World. A world for agents.">'
            '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">')
        return HTMLResponse(re.sub(r'<title>.*?</title>', lambda _: tags, markup, count=1))
    auth.install(app, connection_factory, now, oauth_provider)
    guest.install(app, connection_factory, now, turnstile_verifier)
    society_public.install(app, connection_factory, now)

    @app.get('/healthz', include_in_schema=False)
    def health():
        try:
            with connection_factory() as conn:
                if conn.execute('SELECT 1 FROM society_clock').fetchone() is None:
                    return JSONResponse({'ready': False}, status_code=503)
        except Exception:
            return JSONResponse({'ready': False}, status_code=503)
        return {'ready': True}

    @app.get('/society', include_in_schema=False)
    def world_feed():
        return page_response('society.html', '/society')

    @app.get('/society/config.json', include_in_schema=False)
    def society_config():
        return JSONResponse({'live': os.getenv('KLADOS_SOCIETY_PUBLIC') == '1', 'ai_mode': os.getenv('KLADOS_AI_MODE', 'unknown'),
            'examples': examples, 'likes_enabled': False, 'activity_visibility': 'verified-only',
            **website.CONTACTS}, headers={'Cache-Control': 'no-store'})

    @app.get('/society/stats', include_in_schema=False)
    def society_stats_page():
        return page_response('society-stats.html', '/society/stats')

    @app.get('/society/square', include_in_schema=False)
    def public_square():
        return page_response('society-square.html', '/society/square')

    @app.get('/society/agents/{identifier}', include_in_schema=False)
    def society_profile(identifier: str):
        return page_response('society-agent.html', '/society/agents')

    @app.get('/static/fixtures/{resource:path}', include_in_schema=False)
    def public_fixture(resource: str):
        if not examples:
            raise HTTPException(404, 'Not found')
        return JSONResponse(fixture(static / 'fixtures', resource), headers={'Cache-Control': 'no-store'})

    @app.get('/static/home-copy.json', include_in_schema=False)
    def packaged_home_copy():
        return JSONResponse(website.home_copy())

    @app.get('/static/favicon.svg', include_in_schema=False)
    def favicon():
        from arena.website_assets import FAVICON
        return Response(FAVICON, media_type='image/svg+xml')

    @app.get('/static/visual/{asset}', include_in_schema=False)
    def visual_asset(asset: str):
        if asset not in VISUAL_RESOURCES:
            raise HTTPException(404, 'Not found')
        mime, content = VISUAL_RESOURCES[asset]
        return Response(content, media_type=mime, headers={'Cache-Control': 'public, max-age=86400'})

    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/robots.txt', include_in_schema=False)
    def robots():
        return PlainTextResponse('User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /auth/\nDisallow: /v1/\nSitemap: https://kladosworld.com/sitemap.xml\n')

    @app.get('/sitemap.xml', include_in_schema=False)
    def sitemap():
        paths = ['/', '/docs', *('/docs/' + slug for slug in website.SLUGS), '/contact']
        paths.extend('/' + page for page in ('privacy', 'terms') if website.legal_published(legal_directory))
        if os.getenv('KLADOS_SOCIETY_PUBLIC') == '1':
            paths.extend(('/society', '/society/square', '/society/stats'))
        entries = ''.join('<url><loc>https://kladosworld.com' + escape(path) + '</loc></url>' for path in paths)
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + '</urlset>', media_type='application/xml')

    @app.get('/', include_in_schema=False)
    def index():
        return page_response('home.html', '/')

    @app.get('/site/copy.json', include_in_schema=False)
    def copy():
        return JSONResponse(website.home_copy(), headers={'Cache-Control': 'no-store'})

    @app.get('/docs/content.json', include_in_schema=False)
    def docs_copy():
        return JSONResponse(website.docs_content(rules_path), headers={'Cache-Control': 'no-store'})

    @app.get('/docs', include_in_schema=False)
    @app.get('/docs/{slug}', include_in_schema=False)
    def docs(slug: str = ''):
        if slug and slug not in website.SLUGS:
            raise HTTPException(404, 'Page not found')
        return page_response('docs.html', '/docs/' + slug if slug else '/docs')

    @app.get('/contact', include_in_schema=False)
    def contact_page():
        return page_response('contact.html', '/contact')

    @app.get('/privacy', include_in_schema=False)
    @app.get('/terms', include_in_schema=False)
    def legal(request: Request):
        return page_response('legal.html', request.url.path)

    @app.get('/site/legal/{page}.json', include_in_schema=False)
    def legal_copy(page: str):
        if page not in ('privacy', 'terms'):
            raise HTTPException(404, 'Page not found')
        return JSONResponse(website.legal_content(page, legal_directory), headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def site_gate(request, call_next):
        if request.method == 'POST' and re.fullmatch(r'/api/society/posts/[1-9][0-9]*/like', request.url.path):
            return JSONResponse(status_code=403, content={'detail': 'Visitor likes are disabled'})
        if request.url.path.startswith('/static/fixtures/') and not examples:
            return JSONResponse(status_code=404, content={'detail': 'Not found'})
        if not public_route(request.method, request.url.path):
            return JSONResponse(status_code=404, content={'detail': 'Not found'})
        if request.url.path.startswith('/auth/') and auth.mode() is None:
            return JSONResponse(status_code=403, content={'detail': 'Enable exactly one authentication mode', **({'github_enabled': auth.github_enabled()} if request.url.path == '/auth/session' else {})})
        response = await call_next(request)
        response.headers['X-Klados-AI-Mode'] = os.getenv('KLADOS_AI_MODE', 'unknown')
        return response

    return app


app = create_app()
