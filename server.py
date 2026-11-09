"""BirdMaps Ottawa server. Python standard library only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path
import json, os, socket, threading, webbrowser, time
import routing
ROOT=Path(__file__).resolve().parent
ALLOWED={'mds.bird.co':('/gbfs/',),'overpass.kumi.systems':('/api/interpreter',),'photon.komoot.io':('/api/',),'nominatim.openstreetmap.org':('/search',),'router.project-osrm.org':('/route/v1/',),'ws1.postescanada-canadapost.ca':('/addresscomplete/',)}
CACHE={}
class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,code=200):
        data=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        if urlparse(self.path).path!='/api/route':return self.send_json({'error':'Unknown endpoint'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>2_000_000:raise ValueError('Invalid route data size')
            payload=json.loads(self.rfile.read(length))
            a=payload.get('origin');b=payload.get('destination');polys=payload.get('no_ride_polygons',[])
            if not isinstance(a,list) or not isinstance(b,list) or len(a)!=2 or len(b)!=2 or any(not isinstance(v,(float,int)) for v in a+b):raise ValueError('Invalid coordinates')
            if not (45.28<a[0]<45.57 and -75.98<a[1]<-75.43 and 45.28<b[0]<45.57 and -75.98<b[1]<-75.43):raise ValueError('Only Ottawa is supported')
            if not isinstance(polys,list) or len(polys)>2500:raise ValueError('Invalid zone data')
            return self.send_json(routing.route(a,b,polys))
        except ValueError as e:return self.send_json({'error':str(e)},422)
        except Exception:return self.send_json({'error':'Routing service unavailable'},503)
    def do_GET(self):
        p=urlparse(self.path)
        if p.path in ('/','/index.html'):
            data=(ROOT/'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data);return
        if p.path=='/api/health':return self.send_json({'ok':True,'service':'BirdMaps Ottawa','build':'bird-feed-diagnostics-v1'})
        if p.path=='/api/bird-status':
            target='https://mds.bird.co/gbfs/v2/public/ottawa/gbfs.json'
            try:
                request=Request(target,headers={'User-Agent':'Mozilla/5.0 BirdMapsOttawa/0.4','Accept':'application/json'})
                with urlopen(request,timeout=12) as response:
                    data=json.loads(response.read(250000))
                    feeds=data.get('data',{}).get('feeds')
                    if feeds is None:
                        feeds=next((v['feeds'] for v in data.get('data',{}).values() if isinstance(v,dict) and isinstance(v.get('feeds'),list)),[])
                    return self.send_json({'server':'ok','bird':'reachable','discovery_feeds':[x.get('name') for x in feeds or []]})
            except HTTPError as e:return self.send_json({'server':'ok','bird':'error','http_status':e.code,'detail':'Bird denied the upstream request'},502)
            except (TimeoutError,URLError) as e:return self.send_json({'server':'ok','bird':'error','detail':str(e)[:300]},502)
            except Exception as e:return self.send_json({'server':'ok','bird':'error','detail':type(e).__name__+': '+str(e)[:240]},502)
        if p.path=='/api/bird':
            kind=parse_qs(p.query).get('kind',[''])[0]
            names={'discovery':'gbfs.json','geofences':'geofencing_zones.json','stations':'station_information.json','vehicles':'free_bike_status.json'}
            if kind not in names:return self.send_json({'error':'Unknown Ottawa Bird feed type'},400)
            target='https://mds.bird.co/gbfs/v2/public/ottawa/'+names[kind]
            hit=CACHE.get(target);now=time.time()
            if hit and now-hit[0]<(45 if kind=='vehicles' else 300):return self.send_json(hit[1])
            try:
                request=Request(target,headers={'User-Agent':'BirdMapsOttawa/0.5 (GBFS reader)','Accept':'application/json'})
                with urlopen(request,timeout=12) as response:
                    raw=response.read(8000001)
                    if len(raw)>8000000:raise ValueError('Upstream payload exceeds 8 MB')
                    obj=json.loads(raw)
                CACHE[target]=(time.time(),obj)
                return self.send_json(obj)
            except HTTPError as e:
                print('Bird '+kind+' returned '+str(e.code),flush=True)
                return self.send_json({'error':'Bird upstream returned HTTP '+str(e.code),'feed':kind,'source':'mds.bird.co'},502)
            except (URLError,TimeoutError) as e:
                print('Bird '+kind+' connection failed: '+str(e),flush=True)
                return self.send_json({'error':'Bird upstream connection failed: '+str(e)[:120],'feed':kind},502)
            except Exception as e:
                print('Bird '+kind+' error: '+str(e),flush=True)
                return self.send_json({'error':'Bird feed could not be decoded: '+type(e).__name__,'feed':kind},502)
        if p.path!='/api/request':return self.send_json({'error':'Unknown endpoint'},404)
        target=parse_qs(p.query).get('url',[''])[0];u=urlparse(target)
        if u.scheme!='https' or u.hostname not in ALLOWED or u.port not in (None,443) or not any(u.path.startswith(path) for path in ALLOWED.get(u.hostname,())) or u.username or u.password:return self.send_json({'error':'URL not approved'},400)
        now=time.time();hit=CACHE.get(target)
        ttl=30 if u.hostname=='mds.bird.co' else 15 if u.hostname=='photon.komoot.io' else 300
        if hit and now-hit[0]<ttl:return self.send_json(hit[1])
        try:
            req=Request(target,headers={'User-Agent':'BirdMapsOttawa/0.4','Accept':'application/json'})
            with urlopen(req,timeout=15) as response:
                data=response.read(8_000_001)
                if len(data)>8_000_000:raise ValueError('Feed too large')
                obj=json.loads(data)
            CACHE[target]=(time.time(),obj);return self.send_json(obj)
        except HTTPError as e:return self.send_json({'error':f'{u.hostname} returned HTTP {e.code}'},502)
        except (URLError,TimeoutError) as e:return self.send_json({'error':f'Unable to reach {u.hostname}: {str(e)[:180]}'},502)
        except Exception:return self.send_json({'error':f'Unable to load data from {u.hostname}'},502)
def main():
    if os.environ.get('PORT'): host='0.0.0.0';port=int(os.environ['PORT'])
    else:
        host='127.0.0.1';s=socket.socket();s.bind((host,0));port=s.getsockname()[1];s.close()
    server=ThreadingHTTPServer((host,port),Handler)
    print(f'BirdMaps listening on {host}:{port}',flush=True)
    if not os.environ.get('PORT'):threading.Timer(.8,lambda:webbrowser.open(f'http://localhost:{port}/')).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
if __name__=='__main__':main()
