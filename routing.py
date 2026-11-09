"""Ottawa route planner: A* street routing with geofence-blocked edges."""
import math,heapq,json,time,re
from urllib.request import Request,urlopen
from urllib.parse import urlencode
R=6371000
CACHE={}
WAYS='cycleway|residential|living_street|tertiary|tertiary_link|secondary|secondary_link|unclassified|service|road|path|track'
def meters(a,b):
    x=math.radians(b[1]-a[1])*math.cos(math.radians((a[0]+b[0])/2))
    y=math.radians(b[0]-a[0])
    return R*math.hypot(x,y)
def inside_ring(point,ring):
    x,y=point[1],point[0];result=False
    for i in range(len(ring)):
        ax,ay=ring[i-1];bx,by=ring[i]
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:result=not result
    return result
def inside(p,rings):
    return bool(rings) and inside_ring(p,rings[0]) and not any(inside_ring(p,hole) for hole in rings[1:])
def segments_touch(a,b,c,d):
    p,q,r,s=[(x[1],x[0]) for x in (a,b,c,d)]
    def cross(x,y,z):return (y[0]-x[0])*(z[1]-x[1])-(y[1]-x[1])*(z[0]-x[0])
    v1,v2,v3,v4=cross(p,q,r),cross(p,q,s),cross(r,s,p),cross(r,s,q)
    if v1*v2<0 and v3*v4<0:return True
    eps=1e-12
    def on(x,y,z):return abs(cross(x,y,z))<eps and min(x[0],y[0])-eps<=z[0]<=max(x[0],y[0])+eps and min(x[1],y[1])-eps<=z[1]<=max(x[1],y[1])+eps
    return on(p,q,r) or on(p,q,s) or on(r,s,p) or on(r,s,q)
def blocked(a,b,polys):
    for rings,bounds in polys:
        if max(a[0],b[0])<bounds[0] or min(a[0],b[0])>bounds[2] or max(a[1],b[1])<bounds[1] or min(a[1],b[1])>bounds[3]:continue
        if inside(a,rings) or inside(b,rings):return True
        for ring in rings:
            for i in range(len(ring)):
                c=(ring[i-1][1],ring[i-1][0]);d=(ring[i][1],ring[i][0])
                if segments_touch(a,b,c,d):return True
    return False
def prepare(polys):
    out=[]
    for rings in polys:
        if not rings or not rings[0]:continue
        xs=[p[0] for p in rings[0]];ys=[p[1] for p in rings[0]]
        out.append((rings,(min(ys),min(xs),max(ys),max(xs))))
    return out
def permitted(tags):
    h=tags.get('highway','')
    if h not in WAYS.split('|'):return False
    if tags.get('access') in ('no','private') or tags.get('bicycle') in ('no','private','dismount'):return False
    if tags.get('foot')=='designated' and tags.get('bicycle') not in ('yes','designated'):return False
    if h in ('path','track') and tags.get('bicycle') not in ('yes','designated','official','permissive'):return False
    if h=='service' and tags.get('service') in ('parking_aisle','driveway'):return False
    m=re.search(r'\d+',str(tags.get('maxspeed','')))
    if m and float(m.group())>50 and h!='cycleway':return False
    if h.startswith('secondary') and not m:return False
    return True
def network(a,b):
    south=min(a[0],b[0])-.009;north=max(a[0],b[0])+.009
    west=min(a[1],b[1])-.012;east=max(a[1],b[1])+.012
    if north-south>.095 or east-west>.12:raise ValueError('Trip exceeds the prototype routing area')
    key=tuple(round(x,3) for x in (south,west,north,east))
    hit=CACHE.get(key)
    if hit and time.time()-hit[0]<900:return hit[1]
    query=f'[out:json][timeout:25];way["highway"~"^({WAYS})$"]({south},{west},{north},{east});out body;>;out skel qt;'
    errors=[]
    for host in ('https://overpass.kumi.systems/api/interpreter','https://overpass-api.de/api/interpreter'):
        try:
            req=Request(host,data=urlencode({'data':query}).encode(),headers={'User-Agent':'BirdMapsOttawa/0.4','Content-Type':'application/x-www-form-urlencoded'})
            with urlopen(req,timeout=32) as response:raw=response.read(20_000_001)
            if len(raw)>20_000_000:raise ValueError('Network data too large')
            obj=json.loads(raw)
            if 'elements' not in obj:raise ValueError('No ways returned')
            CACHE[key]=(time.time(),obj)
            if len(CACHE)>12:CACHE.pop(next(iter(CACHE)))
            return obj
        except Exception as e:errors.append(str(e))
    raise ValueError('Street network unavailable')
def astar(nodes,edges,start,goal):
    heap=[(meters(nodes[start],nodes[goal]),0,start)]
    distance={start:0};previous={}
    while heap:
        _,cost,u=heapq.heappop(heap)
        if cost>distance.get(u,float('inf'))+1e-5:continue
        if u==goal:
            path=[u]
            while u in previous:u=previous[u];path.append(u)
            return list(reversed(path)),cost
        for v,w in edges.get(u,()):
            nc=cost+w
            if nc<distance.get(v,float('inf')):
                distance[v]=nc;previous[v]=u
                heapq.heappush(heap,(nc+meters(nodes[v],nodes[goal]),nc,v))
    return None,None
def route(a,b,polygons,network_data=None):
    if not polygons:raise ValueError('No published Bird no-ride geofences. Cannot verify scooter route.')
    polys=prepare(polygons)
    if blocked(a,a,polys) or blocked(b,b,polys):raise ValueError('Starting point or parking point is in no-ride zone')
    obj=network_data if network_data is not None else network(a,b)
    nodes={e['id']:(e['lat'],e['lon']) for e in obj.get('elements',[]) if e.get('type')=='node'}
    edges={};checked=0;kept=0
    for way in obj.get('elements',[]):
        if way.get('type')!='way' or not permitted(way.get('tags',{})):continue
        ids=way.get('nodes',[])
        one=way.get('tags',{}).get('oneway') in ('yes','1','true')
        if way.get('tags',{}).get('oneway:bicycle')=='no':one=False
        for u,v in zip(ids,ids[1:]):
            if u not in nodes or v not in nodes:continue
            checked+=1
            if blocked(nodes[u],nodes[v],polys):continue
            d=meters(nodes[u],nodes[v])
            if d<.1:continue
            edges.setdefault(u,[]).append((v,d));kept+=1
            if not one:edges.setdefault(v,[]).append((u,d))
    valid=set(edges)
    for arr in edges.values():valid.update(v for v,_ in arr)
    if not valid:raise ValueError('No permitted street network remains')
    def nearest(p):
        best=sorted(((meters(p,nodes[i]),i) for i in valid),key=lambda x:x[0])[:8]
        return [(d,i) for d,i in best if d<230 and not blocked(p,nodes[i],polys)]
    starts=nearest(a);ends=nearest(b)
    if not starts or not ends:raise ValueError('No scooter-eligible road within 230 m of start or end')
    options=[]
    for ds,sid in starts[:5]:
        for de,eid in ends[:5]:
            path,d=astar(nodes,edges,sid,eid)
            if path:options.append((ds+d+de,path,ds,de))
    if not options:raise ValueError('No route avoids all loaded no-ride polygons')
    total,path,ds,de=min(options,key=lambda v:v[0])
    coords=[list(a)]+[list(nodes[n]) for n in path]+[list(b)]
    return {'coords':coords,'meters':round(total),'seconds':round(total/3.88889),'snap_start_m':round(ds),'snap_end_m':round(de),'edges_total':checked,'edges_blocked_or_excluded':checked-kept,'geofence_avoiding':True,'caveat':'Not Bird-certified. Map accuracy, sidewalk rules, and NCC path restrictions require independent verification.'}
