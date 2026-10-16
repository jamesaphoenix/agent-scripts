# Recover a fal queue result by request id (when a local poller timed out but fal kept working).
# Usage: python3 scripts/fal-fetch.py <endpoint-app e.g. fal-ai/kling-video> <request_id> <out.mp4>
import json, os, sys, time, urllib.request
app, rid, out = sys.argv[1:4]
H = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
base = f'https://queue.fal.run/{app}/requests/{rid}'
while True:
    st = json.load(urllib.request.urlopen(urllib.request.Request(base + '/status', headers=H)))
    if st['status'] == 'COMPLETED':
        break
    if st['status'] not in ('IN_QUEUE', 'IN_PROGRESS'):
        sys.exit(f'{rid} {st}')
    time.sleep(15)
res = json.load(urllib.request.urlopen(urllib.request.Request(base, headers=H)))
urllib.request.urlretrieve(res['video']['url'], out)
json.dump(res, open(out + '.json', 'w'))
print(rid, 'DONE', out)
