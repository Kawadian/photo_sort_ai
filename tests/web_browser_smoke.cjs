/* Optional browser smoke: NODE_PATH must contain Playwright. No Node runtime is needed by the app. */
'use strict';
const { chromium } = require('playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const net = require('node:net');

(async () => {
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'photo-web-'));
 const output=path.resolve('web-test-output');fs.mkdirSync(output,{recursive:true});
 const python=process.env.PHOTO_SORT_TEST_PYTHON||'python';
 const setup=spawnSync(python,['-c',
  'from pathlib import Path; import sys; from PIL import Image; from photo_sort_ai.config import Config,save_config; r=Path(sys.argv[1]); p=r/"photos"; p.mkdir(); Image.new("RGB",(400,250),(90,140,160)).save(p/"sample.jpg"); save_config(Config(folders=[str(p)],state_dir=str(r/"state"),output="report",settle_seconds=0,low_priority=False),r/"config.json")',root],{encoding:'utf8'});
 assert.equal(setup.status,0,setup.stderr);
 const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
 const env={...process.env,PHOTO_SORT_WEB_PASSWORD:'web-test-password'};
 const child=spawn(python,['-m','photo_sort_ai','--config',path.join(root,'config.json'),'web','--no-browser','--port',String(port),
                          '--public','--host','127.0.0.1','--public-url',`http://127.0.0.1:${port}`],{env});
 let serverLog='';child.stdout.on('data',data=>serverLog+=data);child.stderr.on('data',data=>serverLog+=data);
 let browser;
 try {
  const base=`http://127.0.0.1:${port}`;
  const deadline=Date.now()+15000;
  while(true){try{const response=await fetch(base+'/api/session');if(response.ok)break;}catch{}if(Date.now()>deadline)throw new Error(serverLog);await new Promise(resolve=>setTimeout(resolve,100));}
  browser=await chromium.launch({headless:true,executablePath:process.env.PHOTO_SORT_BROWSER||undefined,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('#login').waitFor({state:'visible'});
  await page.locator('#password').fill('wrong');await page.locator('#login-form button').click();await page.locator('#login-error').filter({hasText:'パスワードが違います'}).waitFor();
  await page.locator('#password').fill('web-test-password');await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state:'visible'});await page.locator('[data-tab=settings]').click();await page.locator('#cpu_threads').waitFor({state:'visible'});
  await page.locator('[data-tab=overview]').click();await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  await page.locator('[data-tab=settings]').click();await page.locator('#cpu_threads').fill('3');await page.locator('#settings-form button[type=submit]').click();await page.locator('#notice').filter({hasText:'設定を保存しました'}).waitFor();
  await page.locator('[data-tab=api]').click();await page.locator('#openai_api_mode').selectOption('openrouter_decisions');await page.locator('#api_concurrency').fill('4');await page.locator('#api-form button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#dirty-label').textContent==='保存済み');
  const saved=JSON.parse(fs.readFileSync(path.join(root,'config.json'),'utf8'));assert.equal(saved.cpu_threads,3);assert.equal(saved.api_concurrency,4);assert.equal(saved.openai_api_mode,'openrouter_decisions');
  await page.locator('[data-tab=overview]').click();await page.locator('[data-mode=scan]').click();await page.waitForFunction(()=>document.querySelector('#metric-results').textContent==='1');
  await page.locator('[data-tab=results]').click();await page.locator('#result-rows tr').first().click();await page.waitForFunction(()=>document.querySelector('#detail-image').complete&&document.querySelector('#detail-image').naturalWidth>0);await page.locator('#close-detail').click();
  await page.locator('#search').fill('no-match');assert.equal(await page.locator('#result-rows tr').count(),0);await page.locator('#search').fill('sample');assert.equal(await page.locator('#result-rows tr').count(),1);
  await page.screenshot({path:path.join(output,'results.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.locator('[data-tab=overview]').click();await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'mobile page overflows');
  assert.deepEqual(errors,[]);console.log('Browser login, config save, Decisions selection, scan, preview, search and mobile layout: OK');
 } finally {
  if(browser)await browser.close();child.kill();await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);fs.rmSync(root,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
