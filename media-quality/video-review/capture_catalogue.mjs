// Fresh real-page captures for revised CTA offers and current catalogue counts.
import {createRequire} from 'node:module';import fs from 'node:fs';import path from 'node:path';
const require=createRequire('/Users/jamesphoenix/.npm-global/lib/node_modules/@playwright/cli/node_modules/playwright-core/package.json');
const{chromium}=require('playwright-core');const work='/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/workflow/feedback-release-20261026';
const out=path.join(work,'fresh-page-captures');fs.mkdirSync(out,{recursive:true});
const b=await chromium.launch({executablePath:'/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/smart-cub/apps/prototypes/founder-videos/node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell',headless:true,args:['--no-sandbox']});
const rows=[];
try{for(const[name,url]of [['maths','https://smartcublearning.com/11-plus/maths/'],['nvr','https://smartcublearning.com/11-plus/non-verbal-reasoning/'],['english','https://smartcublearning.com/11-plus/english/'],['exercises','https://smartcublearning.com/11-plus/exercises/'],['diagnostic','https://smartcublearning.com/free-diagnostic'],['skills','https://smartcublearning.com/11-plus/']]){
 for(const[tag,viewport,deviceScaleFactor]of [['desk',{width:1280,height:800},2],['mob',{width:430,height:760},3]]){
  const ctx=await b.newContext({viewport,deviceScaleFactor,isMobile:tag==='mob',hasTouch:tag==='mob'});const p=await ctx.newPage();
  const response=await p.goto(url,{waitUntil:'networkidle',timeout:45000});if(!response.ok())throw Error('Page unavailable: '+url);
  await p.getByRole('button',{name:'Reject',exact:true}).click().catch(()=>{});await p.evaluate(()=>document.fonts.ready);
  await p.addStyleTag({content:'aside[aria-label="Cookie choices"], [data-cookie-settings], [aria-label="Help & feedback"]{visibility:hidden!important}'});
  const text=await p.locator('body').innerText();await p.screenshot({path:path.join(out,`${name}-${tag}.png`)});
  rows.push({name,aspect:tag,url:p.url(),title:await p.title(),httpStatus:response.status(),opening:text.slice(0,2200),at:new Date().toISOString()});await ctx.close();
 }
}fs.writeFileSync(path.join(out,'capture-evidence.json'),JSON.stringify(rows,null,2));console.log('Captured '+rows.length+' current public-page images.');}finally{await b.close();}
