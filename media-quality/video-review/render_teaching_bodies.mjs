// Local-only render of the two explicitly requested teaching-body repairs.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import {createRequire} from 'node:module';
const source='/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/workflow/feedback-release-20261026/source/teaching-body-repairs';
const require=createRequire(path.join(source,'package.json'));
const {bundle}=require('@remotion/bundler');const {getCompositions,renderMedia,renderStill}=require('@remotion/renderer');
const stage=JSON.parse(fs.readFileSync(path.join(source,'../../teaching-body-stage.json'),'utf8'));
if(!stage.pronunciationListeningAccepted)throw Error('Pronunciation sample has not passed listening');
os.setPriority(0,10);process.chdir(source);
const browserExecutable='/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/smart-cub/apps/prototypes/founder-videos/node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell';
const out=path.resolve(source,'../../teaching-body-repairs');fs.mkdirSync(out,{recursive:true});
const url=await bundle({entryPoint:stage.entry,publicDir:path.join(source,'public')});
const compositions=await getCompositions(url,{browserExecutable,chromiumOptions:{gl:'angle'}});
for(const comp of compositions){
 const aspect=comp.id.endsWith('-tall')?'tall':'wide';
 const skill=comp.id.includes('sound-device')?'english.language.identify-sound-device':'english.punctuation.mark-possession';
 const dest=path.join(out,`${skill}-${aspect}.mp4`),receipt=path.join(out,`${skill}-${aspect}.json`);
 if(fs.existsSync(receipt)){const r=JSON.parse(fs.readFileSync(receipt));if(fs.existsSync(dest)&&crypto.createHash('sha256').update(fs.readFileSync(dest)).digest('hex')===r.outputSha256)continue;throw Error('Committed teaching output changed');}
 const temp=dest+'.rendering.mp4';await renderMedia({serveUrl:url,browserExecutable,composition:comp,codec:'h264',crf:18,audioBitrate:'192k',concurrency:4,chromiumOptions:{gl:'angle'},outputLocation:temp});fs.renameSync(temp,dest);
 const data=JSON.parse(fs.readFileSync(path.join(source,'data',`${skill}.json`)));
 const cut=Math.ceil((Math.min(data.durationMs,data.words.at(-1).e+70))/1000*30)/30;
 const result={state:'rendered_pending_review',output:dest,outputSha256:crypto.createHash('sha256').update(fs.readFileSync(dest)).digest('hex'),cutSeconds:cut,skillKey:skill,aspect,composition:comp.id,sourceAudioSha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(source,'public/audio',`${skill}.mp3`))).digest('hex')};
 fs.writeFileSync(receipt,JSON.stringify(result,null,2));
 await renderStill({serveUrl:url,browserExecutable,composition:comp,frame:skill.includes('mark-possession')?30:Math.round(data.words[118].s/1000*30),output:path.join(out,`${skill}-${aspect}-check.png`),chromiumOptions:{gl:'angle'}});
 console.log(JSON.stringify(result));
}
fs.rmSync(url,{recursive:true,force:true});
