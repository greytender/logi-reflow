"""Record real Tabbit tab footage using the already user-authorized stream.

No screenshots, desktop capture, foreground activation or model calls.
Needs the Tabbit task's captureDemoPage/captureControlPage bindings and local sink.
"""
from pathlib import Path
from collections import defaultdict
import argparse,json,os,subprocess,time,wave
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'outputs/video-work'
START="""
const control=globalThis.captureControlPage;
assert(control&&globalThis.captureDemoPage);
await control.evaluate(async()=>{
 const c=captureControl,old=c.recorder;
 if(old.state!=='inactive'){old.onstop=null;const done=new Promise(r=>old.addEventListener('stop',r,{once:true}));old.stop();await done;}
 if(c.stream.getVideoTracks()[0].readyState!=='live')throw Error('Shared tab stream ended');
 c.chunks=[];const r=new MediaRecorder(c.stream,{mimeType:'video/webm;codecs=vp9',videoBitsPerSecond:4000000});
 r.ondataavailable=e=>{if(e.data.size)c.chunks.push(e.data);};
 r.onstop=()=>{c.recordedBlob=new Blob(c.chunks,{type:r.mimeType});c.state='paused';};
 c.recorder=r;c.state='recording';c.startedAt=Date.now();r.start(1000);
 document.getElementById('status').textContent='正在后台录制完整演示，请保持两个标签页打开。可以使用其他窗口。';
});
"""
END="""
await control.evaluate(()=>captureControl.recorder.stop());await control.waitForFunction(()=>captureControl.state==='paused');
const saved=await control.evaluate(async token=>{const c=captureControl,r=await fetch('http://127.0.0.1:18780/full',{method:'POST',headers:{'Content-Type':'video/webm','X-Recording-Token':token},body:c.recordedBlob});if(!r.ok)throw Error('Local video save failed');return {file:await r.json(),recordingStartedAt:c.startedAt,settings:c.stream.getVideoTracks()[0].getSettings()};},globalThis.localRecordingToken);
result.video=saved.file.path;result.recordingStartedAt=saved.recordingStartedAt;result.captureSettings=saved.settings;return result;
"""
def main():
    parser=argparse.ArgumentParser();parser.add_argument('stages',nargs='*');args=parser.parse_args()
    plan=json.loads((ROOT/'docs/video-narration.json').read_text(encoding='utf-8'))
    selected=args.stages or [s['id'] for s in plan['scenes']]
    assert all(s in {q['id'] for q in plan['scenes']} for s in selected)
    phrases=json.loads((WORK/'audio/phrases.json').read_text(encoding='utf-8'));seconds=defaultdict(float);counts=defaultdict(int)
    for p in phrases:
        with wave.open(str(WORK/'audio'/p['filename'])) as f:seconds[p['scene']]+=f.getnframes()/f.getframerate()
        counts[p['scene']]+=1
    token=json.loads((WORK/'sink-config.json').read_text(encoding='utf-8'))['token']
    cli=Path(os.environ['LOCALAPPDATA'])/'Tabbit/LocalAgent/bin/tabbit-cli.exe'
    captures=WORK/'background-captures.json'
    records=json.loads(captures.read_text(encoding='utf-8')) if captures.exists() else {'recording_kind':'continuous browser tab MediaRecorder video','screenshots_used':False,'foreground_activated':False,'scenes':[]}
    helpers=(ROOT/'scripts/capture_walkthrough.js').read_text(encoding='utf-8')
    for id in selected:
        scene=next(s for s in plan['scenes'] if s['id']==id)
        minimum=max(seconds[id]+.3*max(0,counts[id]-1)+6,scene.get('minimum_seconds',0))
        attempt=str(time.time_ns());program=WORK/f'background-stage-{id}.js'
        program.write_text('globalThis.localRecordingToken='+json.dumps(token)+';'+helpers+START+f'\nw.minimumSeconds={minimum:.3f};const result=await w.stage({int(id)});\n'+END,encoding='utf-8')
        invocation=f'"{cli}" nodejs --task logireflow-walkthrough --request-id bg-{id}-{attempt} --timeout-ms 180000 < "{program}"'
        result=subprocess.run('cmd /d /s /c "'+invocation+'"',cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
        receipt=next((json.loads(s) for s in result.stdout.splitlines() if s.startswith('{')),None)
        (WORK/f'background-stage-{id}-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        if result.returncode or not receipt or receipt.get('status')!='succeeded':
            print(json.dumps({'stage':id,'status':'failed','receipt':receipt},ensure_ascii=False),flush=True);raise RuntimeError('Inspect actual state before retry')
        value=receipt['result'].get('value');assert value and value['complete']
        records['scenes']=[s for s in records['scenes'] if s['id']!=id]+[value]
        captures.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'stage':id,'status':'recorded','actions':len(value['actions'])},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
