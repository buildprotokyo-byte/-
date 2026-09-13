// Shared, company-hosted open-source AI proxy for the deep-analysis features
// (営業実行ナビ / 施工の上級者レビュー / ホームページ要約 / 学習センター) that used to
// require each staff member's own PC to run LM Studio locally. Speaks the same
// OpenAI-compatible /chat/completions shape the client already expects, so the
// PC app can call this instead of (or as a fallback to) http://127.0.0.1:1234/v1.
// Uses the same BUILDPRO_OSS_AI_* secrets as buildpro-ai-guide, so both features
// share one centrally-hosted open-source model.
const HEADERS={
  'Content-Type':'application/json; charset=utf-8',
  'Access-Control-Allow-Origin':'*',
  'Access-Control-Allow-Headers':'authorization, apikey, content-type',
  'Access-Control-Allow-Methods':'POST, OPTIONS'
};
const json=(status:number,body:unknown)=>new Response(JSON.stringify(body),{status,headers:HEADERS});
const env=(name:string,required=true)=>{const value=String(Deno.env.get(name)||'').trim();if(required&&!value)throw new Error(`server_configuration_missing:${name}`);return value};
async function authenticate(request:Request){
  const authorization=request.headers.get('authorization')||'';
  if(!authorization.startsWith('Bearer '))throw new Error('authentication_required');
  const response=await fetch(`${env('SUPABASE_URL')}/auth/v1/user`,{headers:{authorization,apikey:env('SUPABASE_ANON_KEY')}});
  if(!response.ok)throw new Error('invalid_buildpro_session');
  const me=await fetch(`${env('SUPABASE_URL')}/functions/v1/buildpro-api`,{method:'POST',headers:{'Content-Type':'application/json',authorization,apikey:env('SUPABASE_ANON_KEY')},body:JSON.stringify({action:'me'})});
  const profile=await me.json().catch(()=>({}));
  if(!me.ok||!profile?.staff)throw new Error('staff_master_link_required');
  return profile.staff;
}
function sanitizeMessages(input:unknown){
  if(!Array.isArray(input))throw new Error('messages_required');
  const out=input.slice(0,40).map((m:any)=>({
    role:['system','user','assistant'].includes(m?.role)?m.role:'user',
    content:String(m?.content??'').slice(0,12000)
  })).filter(m=>m.content.trim());
  if(!out.length)throw new Error('messages_required');
  return out;
}
Deno.serve(async(request:Request)=>{
  if(request.method==='OPTIONS')return new Response('ok',{headers:HEADERS});
  if(request.method!=='POST')return json(405,{ok:false,error:'method_not_allowed'});
  try{
    await authenticate(request);
    const body=await request.json().catch(()=>({}));
    const messages=sanitizeMessages(body.messages);
    const base=env('BUILDPRO_OSS_AI_BASE_URL',false).replace(/\/$/,'');
    if(!base)return json(503,{ok:false,error:'shared_ai_not_configured'});
    const key=env('BUILDPRO_OSS_AI_API_KEY',false),model=env('BUILDPRO_OSS_AI_MODEL',false)||'Qwen/Qwen2.5-7B-Instruct';
    const temperature=Math.min(1,Math.max(0,Number(body.temperature)||.2));
    const maxTokens=Math.min(4000,Math.max(64,Number(body.max_tokens)||900));
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),100000);
    try{
      const response=await fetch(`${base}/chat/completions`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json',...(key?{Authorization:`Bearer ${key}`}:{})},body:JSON.stringify({model,temperature,max_tokens:maxTokens,stream:false,messages})});
      if(!response.ok)throw new Error(`oss_ai_http_${response.status}`);
      const data=await response.json();
      const content=String(data?.choices?.[0]?.message?.content||'').trim();
      if(!content)throw new Error('oss_ai_empty_response');
      // Same shape as an OpenAI-compatible /chat/completions response, so the
      // PC client can read data.choices[0].message.content unchanged.
      return json(200,{ok:true,mode:'central_open_source_model',model,choices:[{message:{role:'assistant',content}}]});
    }catch(error){
      return json(502,{ok:false,error:'shared_ai_upstream_failed',detail:String(error?.message||error)});
    }finally{clearTimeout(timer)}
  }catch(error){const message=String(error?.message||error),status=/authentication|required|invalid_buildpro_session/.test(message)?401:/staff_master/.test(message)?403:400;return json(status,{ok:false,error:message})}
});
