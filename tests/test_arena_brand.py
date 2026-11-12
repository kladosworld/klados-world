"""Brand source, packaged derivatives and product-copy contracts; no society writes."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
from arena.app import create_app
from arena.brand_assets import FAVICON, RESOURCES
from arena import website

ROOT = Path(__file__).resolve().parents[1]
NS = '{http://www.w3.org/2000/svg}'


class BrandRefinementTests(unittest.TestCase):
    def test_packaged_glyph_and_favicon_keep_the_existing_k_geometry(self):
        expected = 'M18 10v60l14-9V20ZM35 37 62 10h17L43 46l32 24H54L35 53Z'
        for markup in (RESOURCES['mark.svg'][1], FAVICON):
            tree = ET.fromstring(markup)
            paths = tree.findall(NS+'path')
            self.assertEqual(len(paths),1)
            self.assertEqual(paths[0].attrib['d'],expected)
            self.assertEqual(paths[0].attrib['stroke'],'#00CC96')
            self.assertEqual(tree.findall('.//'+NS+'script'),[])
            self.assertEqual(tree.attrib['viewBox'],'0 0 80 80')
        for path in (ROOT/'arena/static').glob('*.html'):
            markup=path.read_text(encoding='utf-8')
            self.assertIn('src="/static/visual/mark.svg"',markup,path.name)
            self.assertNotIn(expected,markup,path.name)
            self.assertNotIn('DIGITAL CIVILIZATION',markup,path.name)
            self.assertNotIn('living experiment',markup,path.name)

    def test_metadata_uses_real_packaged_png_and_assets_remain_read_only(self):
        def forbidden():
            raise AssertionError('Brand assets must never open the database')
        with TestClient(create_app(connection_factory=forbidden)) as client:
            for name,(mime,data) in RESOURCES.items():
                response=client.get('/static/visual/'+name)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.content,data)
                self.assertIn(mime,response.headers['content-type'])
                self.assertEqual(response.headers['content-security-policy'],"default-src 'self'")
            self.assertEqual(client.get('/static/visual/private-brand.key').status_code,404)
            self.assertEqual(client.get('/static/favicon.svg').text,FAVICON)
            html=client.get('/').text
            self.assertIn('<title>A world for agents. · Klados World</title>',html)
            self.assertIn('property="og:site_name" content="Klados World"',html)
            self.assertIn('name="twitter:card" content="summary_large_image"',html)
            self.assertIn('https://kladosworld.com/static/visual/social-card.png',html)
        png=RESOURCES['social-card.png'][1]
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',png[16:24]),(1200,630))

    @unittest.skipUnless((ROOT/'brand/klados-mark.svg').is_file(),'canonical authoring files are repository-only')
    def test_canonical_assets_and_copy_are_reproducibly_derived(self):
        self.assertEqual((ROOT/'brand/klados-mark.svg').read_bytes().replace(b'\r\n',b'\n'),RESOURCES['mark.svg'][1])
        for script in ('build_brand_assets.py','build_website_assets.py'):
            result=subprocess.run([sys.executable,str(ROOT/'scripts'/script),'--check'],cwd=ROOT,
                capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for name,size in [('avatar',(1024,1024)),('social-card',(1200,630)),('x-header',(1500,500))]:
            data=(ROOT/f'brand/klados-{name}.png').read_bytes()
            self.assertEqual(struct.unpack('>II',data[16:24]),size)
            tree=ET.parse(ROOT/f'brand/klados-{name}.svg')
            self.assertEqual(tree.findall('.//'+NS+'script'),[])

    def test_copy_describes_possibilities_without_granting_capabilities(self):
        copy=website.home_copy()
        self.assertEqual(copy['headline'],'A world for agents.')
        self.assertEqual(copy['closing'],'What happens next is up to them.')
        self.assertEqual(len(copy['how']),3)
        self.assertNotIn('infrastructure',json.dumps(copy['faq']))
        content=website.docs_content()
        text=json.dumps(content)
        self.assertIn('not new platform permissions',text)
        self.assertIn('Agreement is optional',text)
        self.assertIn('Access is invitation-only',text)
        self.assertIn('/connect',text)
        self.assertEqual(content['pages'][-1]['status'],'Available')
