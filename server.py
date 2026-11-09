"""BirdMaps Ottawa — rebuilt minimal API. Standard-library Python only."""
import json
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path
import routing

ROOT = Path(__file__).resolve().parent
BIRD = 'https://mds.bird.co/gbfs/v2/public/ottawa/'
CITY = 'https://services.arcgis.com/G6F8XLCl5KtAlZ2G/arcgis/rest/services/E_Scooter/FeatureServer'
CACHE = {}
LOG = logging.getLogger('birdmaps')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

def fetch_json(url, timeout=12, size=8_000_000):
    req = Request(url, headers={'User-Agent':'BirdMapsOttawa/1.0 (+https://github.com/mznt19/BirdMaps)', 'Accept':'application/json, application/geo+json'})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(size+1)
        if len(raw) > size:
            raise ValueError('Upstream response too large')
        return json.loads(raw)

def cached(key, ttl, getter):
    hit=CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    data=getter()
    CACHE[key]=(time.monotonic(),data)
    return data

def api_error(exc):
    if isinstance(exc,HTTPError):return 'HTTP '+str(exc.code)
    if isinstance(exc,URLError):return 'Network: '+str(exc.reason)[:130]
    return type(exc).__name__+': '+str(exc)[:140]

def items(doc,key):
    d=doc.get('data',doc)
    if isinstance(d,dict) and isinstance(d.get(key),list):
        return d[key]
    if isinstance(d,dict):
        for v in d.values():
            if isinstance(v,dict) and isinstance(v.get(key),list):
                return v[key]
    return []

def feed_discovery():
    doc=fetch_json(BIRD+'gbfs.json',timeout=7)
    d=doc.get('data',doc)
    if isinstance(d,dict):
        if 'feeds' in d:return d['feeds']
        for entry in d.values():
            if isinstance(entry,dict) and isinstance(entry.get('feeds'),list):return entry['feeds']
    return []

def bird_data():
    result={'stations':[],'zones':[],'vehicles':[],'sources':[],'warnings':[],'bird_zones_loaded':False}
    try:
        feeds=cached('discovery',300,feed_discovery)
        feed_urls={i.get('name'):i.get('url') for i in feeds if isinstance(i,dict) and i.get('url')}
        result['sources'].append('Bird GBFS discovery')
        for key,target in [('station_information','stations'),('geofencing_zones','zones'),('free_bike_status','vehicles')]:
            url=feed_urls.get(key)
            if not url and key=='free_bike_status':url=feed_urls.get('vehicle_status')
            if not url:
                result['warnings'].append('Bird does not advertise '+key)
                continue
            if not url.startswith('https://mds.bird.co/'):
                result['warnings'].append('Unexpected Bird feed host for '+key)
                continue
            try:
                doc=cached('bird:'+key,75 if key=='free_bike_status' else 300,lambda:fetch_json(url,timeout=7))
                if target=='stations':
                    for s in items(doc,'stations'):
                        if isinstance(s,dict) and isinstance(s.get('lat'),(int,float)) and isinstance(s.get('lon'),(int,float)):
                            result['stations'].append({'id':str(s.get('station_id','')),'name':s.get('name') if isinstance(s.get('name'),str) else 'Bird parking station','lat':s['lat'],'lon':s['lon'],'source':'Bird GBFS'})
                elif target=='vehicles':
                    result['vehicles']=[{'lat':s['lat'],'lon':s['lon']} for s in (items(doc,'bikes') or items(doc,'vehicles')) if isinstance(s,dict) and isinstance(s.get('lat'),(int,float)) and isinstance(s.get('lon'),(int,float))][:1800]
                else:
                    result['zones']=normalize_bird_zones(doc)
                    result['bird_zones_loaded']=any(z.get('properties',{}).get('kind')=='no_ride' for z in result['zones'])
            except Exception as exc:
                result['warnings'].append('Bird '+key+': '+api_error(exc))
    except Exception as exc:
        result['warnings'].append('Bird discovery: '+api_error(exc))
    return result

def normalize_bird_zones(doc):
    zones=[]
    d=doc.get('data',{})
    collection=d.get('geofencing_zones') if isinstance(d,dict) else None
    if isinstance(collection,dict):
        collection=collection.get('features',[])
    if collection is None:collection=items(doc,'geofencing_zones')
    if isinstance(collection,dict):collection=collection.get('features',[])
    for item in collection if isinstance(collection,list) else []:
        if not isinstance(item,dict):continue
        geom=item.get('geometry') or item.get('geojson',{}).get('geometry')
        if not geom and item.get('type')=='Feature':geom=item.get('geometry')
        if not isinstance(geom,dict) or geom.get('type') not in ('Polygon','MultiPolygon'):continue
        props={**item.get('properties',{})} if isinstance(item.get('properties'),dict) else {}
        rules=props.get('rules',item.get('rules',[]))
        if not isinstance(rules,list):rules=[]
        no_ride=props.get('ride_allowed') is False or props.get('ride_through_allowed') is False or any(r.get('ride_allowed') is False or r.get('ride_through_allowed') is False for r in rules if isinstance(r,dict))
        no_park=props.get('parking_allowed') is False or any(r.get('parking_allowed') is False for r in rules if isinstance(r,dict))
        slow=any(r.get('maximum_speed_kph') is not None for r in rules if isinstance(r,dict))
        kind='no_ride' if no_ride else 'no_park' if no_park else 'slow' if slow else 'other'
        zones.append({'type':'Feature','geometry':geom,'properties':{'kind':kind,'source':'Bird GBFS'}})
    return zones

def city_data():
    data={'stations':[],'zones':[],'sources':[],'warnings':[]}
    try:
        meta=cached('citylayers',3600,lambda:fetch_json(CITY+'?f=json',timeout=7))
        layers=meta.get('layers',[])
        if not isinstance(layers,list):raise ValueError('City API has no layer list')
        data['sources'].append('City of Ottawa E-Scooter GIS')
        for layer in layers[:35]:
            name=str(layer.get('name','')); name_l=name.lower()
            kind=None
            if any(v in name_l for v in ('no ride','no-ride','no riding','no riding zone')):kind='no_ride'
            elif any(v in name_l for v in ('slow ride','slow zone','reduced speed')):kind='slow'
            elif 'no park' in name_l:kind='no_park'
            elif any(v in name_l for v in ('scooter parking','designated parking','parking location','parking area','parking zone')):kind='parking'
            if kind is None:continue
            try:
                url=CITY+'/'+str(layer['id'])+'/query?'+urlencode({'where':'1=1','outFields':'*','returnGeometry':'true','outSR':'4326','f':'geojson','resultRecordCount':2000})
                j=cached('city:'+str(layer['id']),3600,lambda:fetch_json(url,timeout=7,size=12_000_000))
                for feature in j.get('features',[]):
                    g=feature.get('geometry') or {}
                    if kind=='parking' and g.get('type')=='Point':
                        lon,lat=g['coordinates'][:2]
                        if 45.2<lat<45.6 and -76.1<lon<-75.3:
                            data['stations'].append({'id':'ott-'+str(feature.get('id','')),'name':feature.get('properties',{}).get('NAME_EN',name),'lat':lat,'lon':lon,'source':'City of Ottawa GIS'})
                    elif kind=='parking' and g.get('type')=='Polygon':
                        rings=g.get('coordinates') or []
                        if rings and rings[0]:
                            lon=sum(p[0] for p in rings[0])/len(rings[0]);lat=sum(p[1] for p in rings[0])/len(rings[0])
                            data['stations'].append({'id':'ott-'+str(feature.get('id','')),'name':name,'lat':lat,'lon':lon,'source':'City of Ottawa GIS'})
                    elif kind!='parking' and g.get('type') in ('Polygon','MultiPolygon'):
                        data['zones'].append({'type':'Feature','geometry':g,'properties':{'kind':kind,'source':'City of Ottawa GIS','layer':name}})
            except Exception as exc:
                data['warnings'].append('Ottawa layer '+name+': '+api_error(exc))
    except Exception as exc:
        data['warnings'].append('Ottawa GIS: '+api_error(exc))
    return data

def full_data():
    # Fetch unrelated providers in parallel to keep a cold Render request responsive.
    with ThreadPoolExecutor(max_workers=2) as pool:
        bird_future=pool.submit(bird_data)
        city_future=pool.submit(city_data)
        bird=bird_future.result()
        city=city_future.result()
    if not bird['stations']:bird['stations']=city['stations']
    # Never replace Bird no-ride zones with a partial invented polygon set; keep both official sources.
    seen=set()
    for z in city['zones']:
        key=(z['properties']['kind'],str(z['geometry'])[:180])
        if key not in seen:bird['zones'].append(z);seen.add(key)
    bird['sources']+=city['sources']
    bird['warnings']+=city['warnings']
    # The city-only map never certifies a Bird route if Bird's own exclusions are missing.
    bird['status']='ready' if bird['stations'] and bird['bird_zones_loaded'] else 'partial'
    bird['checked_at']=int(time.time())
    return bird

def search(q):
    q=q.strip()
    if len(q)<3 or len(q)>180:raise ValueError('Enter at least three characters')
    if not any(k in q.lower() for k in ['ottawa','ontario','canada']):query=q+', Ottawa, Ontario'
    else:query=q
    errors=[]
    for source,url in [
        ('Photon','https://photon.komoot.io/api/?'+urlencode({'q':query,'limit':7,'lat':45.42,'lon':-75.7})),
        ('Nominatim','https://nominatim.openstreetmap.org/search?'+urlencode({'q':query,'countrycodes':'ca','format':'json','limit':7,'addressdetails':1}))]:
        try:
            j=fetch_json(url,timeout=9,size=800000)
            results=[]
            if source=='Photon':
                for f in j.get('features',[]):
                    p=f.get('properties',{});c=f.get('geometry',{}).get('coordinates',[])
                    if len(c)<2:continue
                    label=', '.join(str(x) for x in [p.get('name'),p.get('housenumber'),p.get('street'),p.get('city'),p.get('state')] if x)
                    lat,lon=c[1],c[0]
                    if 45.2<lat<45.6 and -76.1<lon<-75.3:results.append({'label':label,'lat':lat,'lon':lon})
            else:
                for f in j:
                    lat,lon=float(f['lat']),float(f['lon'])
                    if 45.2<lat<45.6 and -76.1<lon<-75.3:results.append({'label':f['display_name'],'lat':lat,'lon':lon})
            if results:return {'items':results,'source':source}
        except Exception as exc:errors.append(source+': '+api_error(exc))
    return {'items':[],'error':'; '.join(errors) if errors else 'No Ottawa matches'}

class Handler(BaseHTTPRequestHandler):
    def json(self,obj,status=200):
        body=json.dumps(obj,ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        p=urlparse(self.path)
        if p.path in ('/','/index.html'):
            body=(ROOT/'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body);return
        if p.path=='/api/health':return self.json({'ok':True,'version':'1.0-rebuild'})
        if p.path=='/api/data':
            try:return self.json(cached('full',150,full_data))
            except Exception as exc:
                LOG.exception('data failed')
                return self.json({'stations':[],'zones':[],'vehicles':[],'status':'partial','warnings':[api_error(exc)],'sources':[]},200)
        if p.path=='/api/search':
            try:return self.json(search(parse_qs(p.query).get('q',[''])[0]))
            except ValueError as exc:return self.json({'items':[],'error':str(exc)},400)
            except Exception as exc:return self.json({'items':[],'error':api_error(exc)},502)
        return self.json({'error':'Not found'},404)
    def do_POST(self):
        if urlparse(self.path).path!='/api/route':return self.json({'error':'Not found'},404)
        try:
            length=int(self.headers.get('Content-Length',0))
            if length<1 or length>1800000:raise ValueError('Invalid request length')
            data=json.loads(self.rfile.read(length))
            a,b=data.get('origin'),data.get('destination')
            if not all(isinstance(x,list) and len(x)==2 and all(isinstance(v,(int,float)) for v in x) for x in [a,b]):
                raise ValueError('Invalid coordinate pair')
            if not all(45.2<pt[0]<45.6 and -76.1<pt[1]<-75.3 for pt in [a,b]):
                raise ValueError('Ottawa-only route')
            snap=cached('full',150,full_data)
            polygons=[]
            for f in snap['zones']:
                if f.get('properties',{}).get('kind')!='no_ride':continue
                g=f.get('geometry',{})
                if g.get('type')=='Polygon':polygons.append(g.get('coordinates',[]))
                elif g.get('type')=='MultiPolygon':polygons+=g.get('coordinates',[])
            if snap['status']!='ready' or not polygons:raise ValueError('No verified no-ride zones and parking points; route cannot be certified')
            res=routing.route(a,b,polygons)
            res['verified_operator_route']=False
            return self.json(res)
        except ValueError as exc:return self.json({'error':str(exc)},422)
        except Exception as exc:
            LOG.exception('route failed')
            return self.json({'error':'Routing server unavailable: '+api_error(exc)},503)

def main():
    port=int(os.environ.get('PORT','10000'))
    server=ThreadingHTTPServer(('0.0.0.0',port),Handler)
    LOG.info('BirdMaps 1.0 listening on %s',port)
    server.serve_forever()
if __name__=='__main__':main()
