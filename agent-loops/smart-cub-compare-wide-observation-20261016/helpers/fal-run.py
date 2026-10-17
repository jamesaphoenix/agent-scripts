# usage: fal-run.py <endpoint> <image> <audio> <out.mp4> [extra-json]
import base64,json,os,sys,time,urllib.request
ep,img,aud,out=sys.argv[1:5]; extra=json.loads(sys.argv[5]) if len(sys.argv)>5 else {}
K=os.environ['FAL_KEY']; H={'Authorization':'Key '+K,'Content-Type':'application/json'}
def uri(p,mt): return f'data:{mt};base64,'+base64.b64encode(open(p,'rb').read()).decode()
def up(p,mt):
    # fal storage: initiate upload, then PUT
    r=urllib.request.Request('https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3',data=json.dumps({'content_type':mt,'file_name':os.path.basename(p)}).encode(),headers=H)
    d=json.load(urllib.request.urlopen(r))
    urllib.request.urlopen(urllib.request.Request(d['upload_url'],data=open(p,'rb').read(),method='PUT',headers={'Content-Type':mt}))
    return d['file_url']
def req(url,body=None,method=None):
    r=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=H,method=method)
    return json.load(urllib.request.urlopen(r,timeout=120))
body={'image_url':up(img,'image/jpeg'),'audio_url':up(aud,'audio/mpeg'),**extra}
t0=time.time(); sub=req('https://queue.fal.run/'+ep,body)
print(ep,'submitted',sub.get('request_id'),flush=True)
while True:
    time.sleep(10)
    st=req(sub['status_url'])
    if st['status']=='COMPLETED': break
    if st['status'] not in('IN_QUEUE','IN_PROGRESS'): print(ep,'FAILED',st); sys.exit(1)
    if time.time()-t0>10800: print(ep,'TIMEOUT'); sys.exit(1)
try: res=req(sub['response_url'])
except urllib.error.HTTPError as e: print(ep,'ERROR',e.read()[:500]); sys.exit(1)
v=res.get('video') or res.get('output',{}).get('video'); u=v['url'] if isinstance(v,dict) else v
urllib.request.urlretrieve(u,out); json.dump(res,open(out+'.json','w'))
print(ep,'DONE',out,f'{time.time()-t0:.0f}s',flush=True)
