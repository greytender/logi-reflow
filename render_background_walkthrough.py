"""Render continuous Tabbit recordings with local speech and burned subtitles.

No screenshot input. Static portions of the actual stream retain reading holds.
Build-only dependencies: Pillow is not needed; imageio-ffmpeg in outputs/media-tools.
"""
from pathlib import Path
from collections import defaultdict
from hashlib import sha256
import argparse,json,math,os,re,shutil,subprocess,sys,wave
ROOT=Path(__file__).resolve().parent;WORK=ROOT/'outputs/video-work';MEDIA=ROOT/'web/media'
sys.path.insert(0,str(ROOT/'outputs/media-tools'))
import imageio_ffmpeg
def stamp(seconds,ass=False):
    n=round(seconds*(100 if ass else 1000));u=100 if ass else 1000
    h,n=divmod(n,3600*u);m,n=divmod(n,60*u);s,f=divmod(n,u)
    return f'{h}:{m:02}:{s:02}.{f:02}' if ass else f'{h:02}:{m:02}:{s:02},{f:03}'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-normalized',action='store_true');args=parser.parse_args()
    plan=json.loads((ROOT/'docs/video-narration.json').read_text(encoding='utf-8'))
    capture=json.loads((WORK/'background-captures.json').read_text(encoding='utf-8'))
    actual={s['id']:s for s in capture['scenes']}
    assert all(s['id'] in actual and actual[s['id']]['complete'] for s in plan['scenes'])
    assert capture['screenshots_used'] is False and capture['foreground_activated'] is False
    phrases=json.loads((WORK/'audio/phrases.json').read_text(encoding='utf-8'));by=defaultdict(list)
    for p in phrases:
        with wave.open(str(WORK/'audio'/p['filename'])) as f:
            assert (f.getnchannels(),f.getsampwidth(),f.getframerate())==(1,2,22050)
            p['seconds']=f.getnframes()/22050
        by[p['scene']].append(p)
    exe=imageio_ffmpeg.get_ffmpeg_exe();MEDIA.mkdir(exist_ok=True);normalized=WORK/'normalized';normalized.mkdir(exist_ok=True)
    t=0.;chapters=[];subs=[];sources=[]
    with wave.open(str(WORK/'narration.wav'),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(22050)
        def silence(seconds):audio.writeframes(b'\0'*(max(0,round(seconds*22050))*2))
        for scene in plan['scenes']:
            s=actual[scene['id']];speech=by[scene['id']]
            spoken=sum(p['seconds'] for p in speech)+.3*max(0,len(speech)-1)
            duration=math.ceil(max((s['endedAt']-s['recordingStartedAt'])/1000,spoken+4)*25)/25
            chapters.append({'id':scene['id'],'title':scene['title'],'start':round(t,3),'end':round(t+duration,3)})
            silence(.8);at=t+.8
            for i,p in enumerate(speech):
                with wave.open(str(WORK/'audio'/p['filename'])) as f:audio.writeframes(f.readframes(f.getnframes()))
                text=p['text'];offset=0.
                for j in range(0,len(text),48):
                    part=text[j:j+48];amount=p['seconds']*len(part)/len(text)
                    subs.append((at+offset,at+offset+amount,'\n'.join(part[k:k+24] for k in range(0,len(part),24))))
                    offset+=amount
                at+=p['seconds']
                if i<len(speech)-1:silence(.3);at+=.3
            silence(t+duration-at)
            target=normalized/f'{scene["id"]}.mp4'
            wanted_frames=round(duration*25)
            filt=f'setpts=PTS-STARTPTS,fps=25:start_time=0,scale=1920:960:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:0:color=0x15281f,tpad=stop_mode=clone:stop_duration={duration:.3f},trim=end_frame={wanted_frames},setpts=N/(25*TB),setsar=1'
            if not (args.reuse_normalized and target.exists()):
                with (normalized/f'{scene["id"]}.log').open('w',encoding='utf-8') as log:
                    subprocess.run([exe,'-hide_banner','-y','-i',s['video'],'-vf',filt,'-frames:v',str(wanted_frames),'-an','-c:v','libx264','-preset','veryfast','-crf','23','-threads','4','-pix_fmt','yuv420p',str(target)],stdout=log,stderr=log,check=True)
            produced=re.findall(r'frame=\s*(\d+)',(normalized/f'{scene["id"]}.log').read_text(encoding='utf-8'))
            assert produced and int(produced[-1])==wanted_frames, f'Incomplete chapter frames: {scene["id"]}'
            sources.append({'id':scene['id'],'duration':duration,'frames':wanted_frames,'video_bytes':Path(s['video']).stat().st_size,'source_sha256':sha256(Path(s['video']).read_bytes()).hexdigest(),'actions':len(s['actions']),'proof':s.get('proof')})
            t+=duration;print(json.dumps({'normalized':scene['id'],'seconds':round(duration,2)}),flush=True)
    (WORK/'clips.ffconcat').write_text('ffconcat version 1.0\n'+'\n'.join("file 'normalized/"+s['id']+".mp4'" for s in plan['scenes'])+'\n',encoding='utf-8')
    srt='\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{text}' for i,(a,b,text) in enumerate(subs))+'\n'
    (MEDIA/'logireflow-walkthrough.srt').write_text(srt,encoding='utf-8')
    (MEDIA/'logireflow-walkthrough.vtt').write_text('WEBVTT\n\n'+'\n\n'.join(f'{stamp(a).replace(",",".")} --> {stamp(b).replace(",",".")}\n{text}' for a,b,text in subs)+'\n',encoding='utf-8')
    ass='''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Subtitle,Microsoft YaHei,34,&H00FFFFFF,&H00FFFFFF,&H00102018,&H90000000,0,0,0,0,100,100,0,0,3,2,0,2,100,100,18,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for a,b,text in subs:
        escaped=text.replace('\\','＼').replace('{','（').replace('}','）').replace('\n',r'\N')
        ass+=f'Dialogue: 0,{stamp(a,True)},{stamp(b,True)},Subtitle,,0,0,0,,{escaped}\n'
    (WORK/'walkthrough.ass').write_text(ass,encoding='utf-8')
    fonts=WORK/'fonts';fonts.mkdir(exist_ok=True);font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc'
    if font.exists():shutil.copyfile(font,fonts/font.name)
    meta=';FFMETADATA1\ntitle=城配续航完整网页功能演示\n'
    for c in chapters:meta+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (WORK/'chapters.ffmeta').write_text(meta,encoding='utf-8')
    video=MEDIA/'logireflow-walkthrough.mp4'
    final_frames=round(t*25)
    final_filter=f'fps=25:start_time=0,tpad=stop_mode=clone:stop_duration=2,trim=end_frame={final_frames},setpts=N/(25*TB),subtitles=walkthrough.ass:fontsdir=fonts'
    command=[exe,'-hide_banner','-y','-safe','0','-f','concat','-i','clips.ffconcat','-i','narration.wav','-i','chapters.ffmeta','-map','0:v:0','-map','1:a:0','-map_metadata','2','-vf',final_filter,'-frames:v',str(final_frames),'-c:v','libx264','-preset','veryfast','-crf','23','-threads','4','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-movflags','+faststart','-t',f'{t:.3f}',str(video)]
    with (WORK/'encoding.log').open('w',encoding='utf-8') as log:subprocess.run(command,cwd=WORK,stdout=log,stderr=log,check=True)
    assert video.stat().st_size<95*1024*1024,'Ordinary GitHub file limit safety margin exceeded'
    (MEDIA/'logireflow-walkthrough-chapters.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    audit={'date':'2026-10-12','browser':'Tabbit','screen_source':'continuous browser-tab MediaRecorder footage; edited chapter transitions and reading holds','screenshots_used':False,'foreground_activated_for_full_recording':False,'browser_chrome_included':False,'duration_seconds':round(t,3),'width':1920,'height':1080,'fps':25,'aspect_ratio_preserved':True,'narration':'local Microsoft Huihui synthesized Chinese speech','burned_subtitles':True,'chapters':chapters,'sources':sources,'bytes':video.stat().st_size,'sha256':sha256(video.read_bytes()).hexdigest(),'paid_model_calls':0,'database':'isolated QA database under outputs/video-qa'}
    (WORK/'render-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'video':str(video),'minutes':round(t/60,2),'bytes':video.stat().st_size},ensure_ascii=False))
if __name__=='__main__':main()
