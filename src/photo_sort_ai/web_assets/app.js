'use strict';
const $ = id => document.getElementById(id);
let csrf = '', config = {}, results = [], events = [], lastEvent = 0, running = false, dirty = false, polling = false;
let defaultPrompt = '';
const presetLabels = {light:'軽量解析（AIなし）',quality:'MUSIQ：画質',aesthetic:'NIMA：見栄え',balanced:'MUSIQ＋NIMA',topiq:'TOPIQ：画質',clip:'CLIPIQA',laion:'LAION',technical:'BRISQUE＋NIQE',ollama:'Ollama',openai:'OpenAI互換API',onnx:'ONNX',custom:'カスタム'};
const statuses = {written:'星を保存',sidecar:'XMPへ保存',report:'結果のみ',protected:'既存の星を保護'};
const fields = [
 ['preset','評価方法',Object.keys(presetLabels)],['device','実行デバイス',['cpu','cuda','auto']],
 ['output','保存方法',['embedded','sidecar','report']],['image_source','画像の取得方法',['auto','preview','raw','companion']],
 ['max_side','画像の最大辺','number'],['cpu_threads','CPUスレッド数','number'],['poll_seconds','監視間隔（秒）','number'],['settle_seconds','コピー安定待ち（秒）','number'],
 ['delay_seconds','写真ごとの休止（秒）','number'],['thresholds','星の境界（0〜100、カンマ区切り）','array'],['exiftool_path','ExifToolの場所'],['state_dir','履歴・バックアップの保存先'],
 ['existing_policy','既存の星',['preserve','overwrite']]
];
const apiFields = [
 ['openai_base_url','API URL'],['openai_model','画像対応モデル名'],['openai_api_mode','API方式',['chat_completions','responses','decisions','openrouter_decisions']],
 ['openai_response_format','JSON方式',['json_object','json_schema','none']],['openai_api_key_env','APIキーの環境変数名'],['openai_image_detail','画像詳細',['auto','low','high','omit']],
 ['openai_timeout','待機時間（秒）','number'],['openai_max_output_tokens','出力トークン上限（0で省略）','number'],['openai_store','store指定',['false','true','omit']],
 ['api_concurrency','API同時処理数（1〜16）','number'],['api_rate_limit_retries','429／503の追加再試行（0〜10）','number']
];
const localFields = [['ollama_model','Ollamaモデル'],['ollama_url','Ollama URL'],['onnx_path','ONNXモデルの場所'],['onnx_provider','ONNX実行方式',['CPUExecutionProvider','DmlExecutionProvider','CUDAExecutionProvider']]];
function notify(message, error=false) { $('notice').hidden=false; $('notice').textContent=message; $('notice').classList.toggle('error',error); }
async function api(path, body) {
 const response = await fetch('/api/'+path, {method:body===undefined?'GET':'POST', headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:body===undefined?undefined:JSON.stringify(body)});
 const data = await response.json();
 if (!response.ok) { if(response.status===401 && path!=='login') showLogin(); throw new Error(data.error || '通信に失敗しました'); }
 return data;
}
function showLogin() { $('workspace').hidden=true; $('login').hidden=false; }
function setTab(id) { document.querySelectorAll('.tab').forEach(e=>e.hidden=e.id!==id); document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.tab===id)); $('page-title').textContent=document.querySelector(`[data-tab="${id}"]`).textContent; }
document.querySelectorAll('.nav').forEach(e=>e.onclick=()=>setTab(e.dataset.tab));
document.querySelectorAll('.switch-settings').forEach(e=>e.onclick=()=>setTab('settings'));
function buildFields(target, definitions) {
 $(target).replaceChildren();
 for (const [key,label,type] of definitions) {
  const wrapper=document.createElement('label'); wrapper.textContent=label;
  const input=document.createElement(Array.isArray(type)?'select':'input'); input.id=key;
  if(Array.isArray(type)) for(const option of type) { const o=document.createElement('option');o.value=option;o.textContent=key==='preset'?presetLabels[option]:option;input.append(o); }
  else { input.type=type==='number'?'number':'text'; if(type==='number')input.step='any'; }
  input.value=key==='openai_store'?(config[key]===null?'omit':String(config[key])):type==='array'?config[key].join(','):config[key];
  input.oninput=markDirty;wrapper.append(input);$(target).append(wrapper);
 }
}
function markDirty() { dirty=true;$('dirty-label').textContent='未保存の変更があります'; }
function selectedPromptName() { const value=$('prompt-preset').value;return value.startsWith('saved:')?value.slice(6):null; }
function updatePromptHint() {
 const name=selectedPromptName();
 for(const id of ['prompt-update','prompt-rename','prompt-delete'])$(id).disabled=name===null;
 const edited=name!==null&&$('openai_prompt').value!==config.openai_prompt_presets[name];
 $('prompt-hint').textContent=edited?'本文が選択したお気に入りと異なります。「更新」でお気に入りへ反映できます。設定の保存で確定します。':'お気に入りの変更と評価への適用は、画面下の「設定を保存」で確定します。';
}
function renderPromptPresets(preferred=null) {
 const select=$('prompt-preset');select.replaceChildren();
 for(const [value,text] of [['custom','カスタム（直接編集）'],['default','標準プロンプト'],...Object.keys(config.openai_prompt_presets).map(name=>['saved:'+name,name])]) {
  const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
 }
 const match=Object.entries(config.openai_prompt_presets).find(([,prompt])=>prompt===$('openai_prompt').value);
 select.value=preferred!==null&&Object.hasOwn(config.openai_prompt_presets,preferred)?'saved:'+preferred:match?'saved:'+match[0]:$('openai_prompt').value===defaultPrompt?'default':'custom';
 $('prompt-name').value=selectedPromptName()||'';updatePromptHint();
}
$('prompt-preset').onchange=()=>{
 const name=selectedPromptName();const old=$('openai_prompt').value;
 if(name!==null)$('openai_prompt').value=config.openai_prompt_presets[name];
 else if($('prompt-preset').value==='default')$('openai_prompt').value=defaultPrompt;
 $('prompt-name').value=name||'';updatePromptHint();if(old!==$('openai_prompt').value)markDirty();
};
function editPromptPreset(action) {
 try {
  const old=selectedPromptName(),name=$('prompt-name').value.trim(),prompt=$('openai_prompt').value;
  const library=Object.assign(Object.create(null),config.openai_prompt_presets);
  if(action==='add'||action==='rename') {
   if(!name||name.length>80)throw new Error('お気に入りの名前を1〜80文字で入力してください。');
   if(Object.hasOwn(library,name))throw new Error('同じ名前のお気に入りがあります。別の名前を入力するか、選択して更新してください。');
  }
  if(action!=='add'&&(old===null||!Object.hasOwn(library,old)))throw new Error('お気に入りを選択してください。');
  if(action==='add'||action==='update') {
   if(!prompt.trim()||prompt.length>50000)throw new Error('プロンプト本文を1〜50,000文字で入力してください。');
   if(action==='add'&&Object.keys(library).length>=100)throw new Error('お気に入りは100件まで登録できます。');
   library[action==='add'?name:old]=prompt;
  } else if(action==='rename'){library[name]=library[old];delete library[old];}
  else if(action==='delete')delete library[old];
  config.openai_prompt_presets=library;renderPromptPresets(action==='add'||action==='rename'?name:action==='update'?old:null);markDirty();
  // Preserve any other advanced JSON edits while reflecting library changes.
  try {const advanced=JSON.parse($('advanced').value);advanced.openai_prompt_presets=library;$('advanced').value=JSON.stringify(advanced,null,2);}catch{}
  notify('お気に入りを変更しました。「設定を保存」で確定してください。');
 }catch(error){notify(error.message,true);}
}
for(const action of ['add','update','rename','delete'])$('prompt-'+action).onclick=()=>editPromptPreset(action);
$('openai_prompt').addEventListener('input',updatePromptHint);
function renderConfig() {
 buildFields('general-fields',fields);buildFields('api-fields',apiFields);buildFields('local-fields',localFields);
 $('checkbox-fields').replaceChildren();
 for(const [key,label] of [['recursive','サブフォルダも対象'],['low_priority','低優先度で実行'],['backup','書き込み前にバックアップ']]) {
  const wrapper=document.createElement('label');wrapper.className='check';const input=document.createElement('input');input.type='checkbox';input.id=key;input.checked=config[key];input.onchange=markDirty;wrapper.append(input,document.createTextNode(label));$('checkbox-fields').append(wrapper);
 }
 $('folders').value=config.folders.join('\n');$('openai_prompt').value=config.openai_prompt;$('openai_extra_body').value=JSON.stringify(config.openai_extra_body,null,2);$('advanced').value=JSON.stringify(config,null,2);
 renderPromptPresets();
 $('metric-preset').textContent=presetLabels[config.preset]||config.preset;$('metric-folders').textContent=config.folders.length;
 $('folder-summary').replaceChildren();for(const folder of config.folders){const li=document.createElement('li');li.textContent=folder;$('folder-summary').append(li);}
 if(!config.folders.length){const li=document.createElement('li');li.textContent='フォルダがまだ設定されていません';$('folder-summary').append(li);}
 $('output-summary').textContent={embedded:'DNG/JPEG/TIFF内部へ星を保存',sidecar:'XMPサイドカーへ保存',report:'写真を変更せず、評価結果のみ保存'}[config.output];
 dirty=false;$('dirty-label').textContent='保存済み';
}
function collectConfig() {
 const value=structuredClone(config);
 for(const [key,,type] of [...fields,...apiFields,...localFields]) {let v=$(key).value;if(type==='number')v=Number(v);if(type==='array')v=v.split(',').map(Number);if(key==='openai_store')v=v==='omit'?null:v==='true';value[key]=v;}
 value.folders=$('folders').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
 for(const key of ['recursive','low_priority','backup'])value[key]=$(key).checked;
 value.openai_prompt=$('openai_prompt').value;value.openai_extra_body=JSON.parse($('openai_extra_body').value);
 return value;
}
async function save(event) {
 event.preventDefault();
 try { const body={config:collectConfig()};if($('api-key').value)body.api_key=$('api-key').value;if($('clear-api-key').checked)body.api_key='';
  const data=await api('config',body);config=data.config;$('api-key').value='';$('clear-api-key').checked=false;renderConfig();notify('設定を保存しました。');
 } catch(error){notify(error.message,true);}
}
$('settings-form').onsubmit=save;$('api-form').onsubmit=save;
for(const id of ['folders','openai_prompt','openai_extra_body','api-key','clear-api-key'])$(id).addEventListener('input',markDirty);
$('apply-json').onclick=()=>{try {const data=JSON.parse($('advanced').value);if(!data||Array.isArray(data)||typeof data!=='object')throw new Error('JSONオブジェクトを指定してください');config={...config,...data};renderConfig();markDirty();notify('詳細設定を入力欄へ適用しました。保存してください。');}catch(error){notify(error.message,true);}};
document.querySelectorAll('.job').forEach(button=>button.onclick=async()=>{try{if(dirty)throw new Error('設定に未保存の変更があります。先に保存してください。');await api('jobs',{mode:button.dataset.mode,limit:Number($('benchmark-limit').value),force:$('force').checked});notify('処理を開始しました。');await refresh();}catch(error){notify(error.message,true);}});
$('stop').onclick=async()=>{try{await api('stop',{});notify('停止を要求しました。現在の評価が終了するまで待ちます。');await refresh();}catch(error){notify(error.message,true);}};
$('retry').onclick=async()=>{try{await api('retry',{});notify('次回の処理で失敗した写真を再試行します。');await refresh();}catch(error){notify(error.message,true);}};
$('search').oninput=renderResults;$('rating-filter').onchange=renderResults;
function renderResults() {
 const search=$('search').value.toLocaleLowerCase(),rating=$('rating-filter').value;
 const filtered=results.filter(r=>(!rating||r.rating===Number(rating))&&r.path.toLocaleLowerCase().includes(search));
 $('result-rows').replaceChildren();$('empty-results').hidden=filtered.length>0;
 $('empty-results').textContent=results.length?'条件に一致する写真がありません。':'評価結果がまだありません。概要画面から評価を開始してください。';
 for(const row of filtered) {
  const tr=document.createElement('tr');const file=document.createElement('td');const name=document.createElement('div');name.className='file-name';name.textContent=row.path.split(/[\\/]/).pop();const path=document.createElement('div');path.className='file-path';path.textContent=row.path;file.append(name,path);tr.append(file);
  for(const [text,cls] of [['★'.repeat(row.rating),'stars'],[Number(row.score).toFixed(1),''],[statuses[row.status]||row.status,''],[Number(row.seconds||0).toFixed(2)+'秒','']]){const td=document.createElement('td');td.textContent=text;td.className=cls;tr.append(td);}
  tr.tabIndex=0;tr.setAttribute('aria-label',name.textContent+'の評価詳細');tr.onclick=()=>showPhoto(row);tr.onkeydown=e=>{if(e.key==='Enter')showPhoto(row);};$('result-rows').append(tr);
 }
}
function showPhoto(row){$('detail-image').src='/api/preview/'+row.preview_id;$('detail-image').hidden=false;$('detail-image').onerror=()=>{$('detail-image').hidden=true;};$('detail-name').textContent=row.path;$('detail-reason').textContent=row.reason;$('detail-data').textContent=JSON.stringify(row.details,null,2);$('photo-detail').showModal();}
$('close-detail').onclick=()=>$('photo-detail').close();
async function refresh() {
 if(polling||$('workspace').hidden)return;polling=true;
 try {
  const [state,history]=await Promise.all([api('status?after='+lastEvent),api('results')]);
  running=state.running;$('connection').textContent='接続中';$('activity-badge').textContent=state.stopping?'停止待ち':running?'処理中':'待機中';$('activity-badge').classList.toggle('working',running);
  const modes={scan:'一括評価',watch:'フォルダ監視',benchmark:'処理時間の計測',prepare:'モデル準備'};
  $('run-title').textContent=running?modes[state.mode]+'を実行中':state.error?'処理中にエラーが発生':state.finished?'処理が終了しました':'実行する処理を選択';
  $('current-path').textContent=state.current_path||state.error||(running?'写真・モデルを準備しています…':'設定を保存してから、一括評価または監視を開始してください。');
  document.querySelectorAll('.job,.save-button').forEach(b=>b.disabled=running);$('stop').disabled=!running||state.stopping;$('retry').disabled=running;
  $('summary').hidden=!state.summary;if(state.summary)$('summary').textContent=`検出 ${state.summary.found}枚 ／ 評価 ${state.summary.processed}枚 ／ エラー ${state.summary.errors}枚 ／ 残り ${state.summary.remaining}枚`;
  $('benchmark').hidden=!state.benchmark;if(state.benchmark){const b=state.benchmark;$('benchmark').textContent=`平均 ${b.mean_seconds.toFixed(2)}秒/枚 ／ 1,000枚 約${b.estimated_1000_hours.toFixed(2)}時間 ／ 1万枚 約${b.estimated_10000_hours.toFixed(2)}時間（保存時間を除く）`;}
  for(const event of state.events){events.push(event);lastEvent=event.id;}events=events.slice(-500);$('event-log').textContent=events.map(e=>new Date(e.time*1000).toLocaleTimeString()+' '+JSON.stringify(e)).join('\n');
  const changed=JSON.stringify(results)!==JSON.stringify(history.results);results=history.results;if(changed)renderResults();$('metric-results').textContent=results.length;
  $('failures').replaceChildren();for(const failure of history.failures){const li=document.createElement('li');li.textContent=failure.path+' — '+failure.error;$('failures').append(li);}if(!history.failures.length){const li=document.createElement('li');li.textContent='失敗した写真はありません';$('failures').append(li);}
 } catch(error){$('connection').textContent='接続を確認してください';notify(error.message,true);}finally{polling=false;}
}
async function initialize() {
 try {const info=await api('session');csrf=info.csrf;if(!info.authenticated){showLogin();return;}
  $('login').hidden=true;$('workspace').hidden=false;$('logout').hidden=!info.auth_required;$('access-badge').textContent=info.public?'外部公開・認証あり':'ローカル';
  const data=await api('config');config=data.config;defaultPrompt=data.default_prompt;renderConfig();await refresh();
 }catch(error){showLogin();$('login-error').textContent=error.message;}
}
$('login-form').onsubmit=async event=>{event.preventDefault();try{const data=await api('login',{password:$('password').value});csrf=data.csrf;$('password').value='';$('login-error').textContent='';await initialize();}catch(error){$('login-error').textContent=error.message;}};
$('logout').onclick=async()=>{try{await api('logout',{});await initialize();}catch(error){notify(error.message,true);}};
initialize();setInterval(refresh,2000);
