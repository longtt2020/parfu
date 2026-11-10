import os, re, csv, io, json, time, hashlib, zipfile, threading
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from collections import deque
from flask import Flask, request, jsonify, send_file, render_template, abort
import requests
from bs4 import BeautifulSoup

BASE='https://parfumerie.vn/'
DOMAIN='parfumerie.vn'
DATA=Path(os.environ.get('DATA_DIR','data'))
DATA.mkdir(parents=True,exist_ok=True)
IMAGES=DATA/'images'; IMAGES.mkdir(exist_ok=True)
app=Flask(__name__)
state={'running':False,'stop':False,'pages':0,'products':0,'images':0,'errors':0,'message':'Sẵn sàng'}
lock=threading.Lock()
UA='ParfumerieCatalogResearchBot/1.0 (public catalog archival; respects robots.txt)'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'vi-VN,vi;q=0.9'})
robot=RobotFileParser(); robot.set_url(urljoin(BASE,'robots.txt'))
try: robot.read(); robots_ready=True
except Exception: robots_ready=False

def allowed(url):
    p=urlparse(url)
    return p.scheme in ('http','https') and p.hostname in (DOMAIN,'www.'+DOMAIN) and robots_ready and robot.can_fetch(UA,url)

def fetch(url):
    if not allowed(url): return None
    try:
        r=s.get(url,timeout=25)
        r.raise_for_status()
        if 'text/html' not in r.headers.get('content-type',''): return None
        return BeautifulSoup(r.text,'html.parser')
    except requests.RequestException: return None

def normalize(url):
    url=urldefrag(urljoin(BASE,url))[0]
    p=urlparse(url)
    if p.hostname not in (DOMAIN,'www.'+DOMAIN): return None
    if p.query: return None
    return url.rstrip('/')+'/' if p.path in ('','/') else url.rstrip('/')

def text(el): return el.get_text(' ',strip=True) if el else ''
def meta(soup,key,attr='name'):
    e=soup.find('meta',attrs={attr:key}); return (e.get('content') or '').strip() if e else ''

def product_jsonld(soup):
    for el in soup.select('script[type="application/ld+json"]'):
        try: obj=json.loads(el.string or el.get_text())
        except (ValueError,TypeError): continue
        todo=obj if isinstance(obj,list) else [obj]
        while todo:
            item=todo.pop()
            if not isinstance(item,dict): continue
            typ=item.get('@type',[])
            if typ=='Product' or (isinstance(typ,list) and 'Product' in typ): return item
            for key in ('@graph','mainEntity'):
                sub=item.get(key,[])
                todo.extend(sub if isinstance(sub,list) else [sub])
    return {}

def image_urls(soup,ld):
    urls=[]
    for u in (ld.get('image') or [] if isinstance(ld.get('image'),list) else [ld.get('image')] if ld.get('image') else []):
        if isinstance(u,dict): u=u.get('url')
        if isinstance(u,str): urls.append(urljoin(BASE,u))
    for e in soup.select('meta[property="og:image"], .product img, .product-detail img, .product-image img, .product-images img, .product-gallery img'):
        u=e.get('content') or e.get('data-src') or e.get('data-original') or e.get('src')
        if u and not u.startswith('data:'): urls.append(urljoin(BASE,u))
    return list(dict.fromkeys(u for u in urls if urlparse(u).scheme in ('http','https')))

def parse_product(url,soup):
    ld=product_jsonld(soup)
    if not ld and not re.search(r'/products?/',urlparse(url).path,re.I): return None
    name=ld.get('name') or text(soup.select_one('h1'))
    if not name: return None
    brand=ld.get('brand','')
    if isinstance(brand,dict): brand=brand.get('name','')
    offers=ld.get('offers',{})
    if isinstance(offers,list): offers=offers[0] if offers else {}
    if not isinstance(offers,dict): offers={}
    desc=ld.get('description') or meta(soup,'description')
    imgs=image_urls(soup,ld)
    return {'url':url,'name':name,'brand':brand,'sku':ld.get('sku',''),'price':offers.get('price',''),'currency':offers.get('priceCurrency','VND'),'availability':offers.get('availability',''),'description':BeautifulSoup(str(desc),'html.parser').get_text(' ',strip=True),'meta_title':text(soup.title),'meta_description':meta(soup,'description'),'canonical':(soup.select_one('link[rel="canonical"]') or {}).get('href',''),'images':' | '.join(imgs),'image_count':len(imgs)},imgs

def save_csv(path,rows,fields):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader();w.writerows(rows)

def load_csv(path):
    if not path.exists(): return []
    with open(path,encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def run(max_pages=5000,download=True):
    global state
    products=load_csv(DATA/'products.csv'); seen_products={p['url'] for p in products}
    images=load_csv(DATA/'images.csv'); seen_images={i['url'] for i in images}
    queue=deque([BASE,urljoin(BASE,'/collections/all'),urljoin(BASE,'/products')]); seen=set()
    with lock: state.update(running=True,stop=False,pages=0,products=len(products),images=len(images),errors=0,message='Đang quét')
    try:
        if not robots_ready:
            with lock: state['message']='Không đọc được robots.txt: dừng an toàn'
            return
        while queue and len(seen)<max_pages and not state['stop']:
            url=queue.popleft()
            if url in seen or not allowed(url): continue
            seen.add(url)
            soup=fetch(url)
            if soup is None:
                with lock: state['errors']+=1
                time.sleep(1.5);continue
            result=parse_product(url,soup)
            if result and url not in seen_products:
                p,img_urls=result;products.append(p);seen_products.add(url)
                for img_url in img_urls:
                    if img_url in seen_images: continue
                    seen_images.add(img_url)
                    filename='';status='url_only'
                    if download:
                        try:
                            host=urlparse(img_url).hostname or ''
                            if host.endswith(('dktcdn.net','bizweb.dktcdn.net',DOMAIN)):
                                r=s.get(img_url,timeout=25,stream=True)
                                r.raise_for_status()
                                mime=r.headers.get('content-type','')
                                if mime.startswith('image/'):
                                    ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}.get(mime.split(';')[0],'.img')
                                    filename=hashlib.sha256(img_url.encode()).hexdigest()[:20]+ext
                                    size=0
                                    with open(IMAGES/filename,'wb') as f:
                                        for chunk in r.iter_content(65536):
                                            size+=len(chunk)
                                            if size>12_000_000: raise ValueError('Image too large')
                                            f.write(chunk)
                                    status='downloaded'
                        except (requests.RequestException,ValueError,OSError):
                            status='failed';filename=''
                    images.append({'product_url':url,'url':img_url,'file':filename,'status':status})
                save_csv(DATA/'products.csv',products,list(p.keys()))
                save_csv(DATA/'images.csv',images,['product_url','url','file','status'])
            for a in soup.select('a[href]'):
                u=normalize(a['href'])
                if u and u not in seen and len(queue)<20000 and not re.search(r'/(cart|account|checkout|search|blogs|pages)/',urlparse(u).path): queue.append(u)
            with lock: state.update(pages=len(seen),products=len(products),images=len(images),message='Đang thu thập')
            time.sleep(1.5)
    finally:
        with lock: state.update(running=False,message=('Đã dừng' if state['stop'] else state['message'] if not robots_ready else 'Hoàn tất'))

def auth():
    token=os.environ.get('ADMIN_TOKEN')
    if not token: abort(503,'Set ADMIN_TOKEN in Render Environment')
    if request.headers.get('X-Admin-Token')!=token: abort(401)

@app.get('/')
def home():return render_template('index.html')
@app.get('/api/status')
def status():auth();return jsonify(state)
@app.post('/api/start')
def start():
    auth()
    if state['running']:return jsonify({'error':'Already running'}),409
    body=request.get_json(silent=True) or {}
    max_pages=max(1,min(int(body.get('max_pages',5000)),20000))
    threading.Thread(target=run,args=(max_pages,bool(body.get('download',True))),daemon=True).start()
    return jsonify({'ok':True})
@app.post('/api/stop')
def stop():auth();state['stop']=True;return jsonify({'ok':True})
@app.get('/api/export')
def export():
    auth()
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in (DATA/'products.csv',DATA/'images.csv'):
            if f.exists():z.write(f,f.name)
        for f in IMAGES.iterdir():
            if f.is_file():z.write(f,'images/'+f.name)
    memory.seek(0)
    return send_file(memory,mimetype='application/zip',as_attachment=True,download_name='parfumerie-products.zip')

if __name__=='__main__':app.run(port=5000)
