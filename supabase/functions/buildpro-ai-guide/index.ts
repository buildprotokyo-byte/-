const HEADERS={
  'Content-Type':'application/json; charset=utf-8',
  'Access-Control-Allow-Origin':'*',
  'Access-Control-Allow-Headers':'authorization, apikey, content-type',
  'Access-Control-Allow-Methods':'POST, OPTIONS'
};
const json=(status:number,body:unknown)=>new Response(JSON.stringify(body),{status,headers:HEADERS});
const env=(name:string,required=true)=>{const value=String(Deno.env.get(name)||'').trim();if(required&&!value)throw new Error(`server_configuration_missing:${name}`);return value};
const clip=(value:unknown,max:number)=>String(value??'').replace(/\s+/g,' ').trim().slice(0,max);
async function authenticate(request:Request){
  const authorization=request.headers.get('authorization')||'';
  if(!authorization.startsWith('Bearer '))throw new Error('authentication_required');
  const response=await fetch(`${env('SUPABASE_URL')}/auth/v1/user`,{headers:{authorization,apikey:env('SUPABASE_ANON_KEY')}});
  if(!response.ok)throw new Error('invalid_buildpro_session');
  const user=await response.json();
  const me=await fetch(`${env('SUPABASE_URL')}/functions/v1/buildpro-api`,{method:'POST',headers:{'Content-Type':'application/json',authorization,apikey:env('SUPABASE_ANON_KEY')},body:JSON.stringify({action:'me'})});
  const profile=await me.json().catch(()=>({}));
  if(!me.ok||!profile?.staff)throw new Error('staff_master_link_required');
  return{user,staff:profile.staff};
}
function builtIn(question:string,context:any){
  const area=clip(context?.screen,80)||'現在の画面',caseName=clip(context?.caseName,120)||'未保存案件',weak=clip(context?.weak,240)||'予算・決裁者・期限・現地条件の未確認事項';
  if(/未来|ストーリー|分岐|営業|受注/.test(question))return`${caseName}の悪い分岐を先に止めます。\n1. ${weak}を事実確認する。\n2. 決裁者・期限・予算を回答当日に記録へ固定する。\n3. 施工・積算への影響と代替案を確認する。\n4. 次回接点と中止条件を決める。未入力の事実は推測しません。`;
  if(/Outlook|アウトルック|予定/.test(question))return'自分のOutlook予定を日・週・月で確認してください。本人の会社メールと社員IDが一致していることが前提です。現在の権限は読み取り専用で、予定の変更・削除・メール送信は行いません。';
  return`${area}では、本人・案件・日付を確認し、必須項目を上から入力します。登録ボタンは一度だけ押し、完了表示・件数・履歴のいずれかが更新されたことを確認してください。`;
}
Deno.serve(async(request:Request)=>{
  if(request.method==='OPTIONS')return new Response('ok',{headers:HEADERS});
  if(request.method!=='POST')return json(405,{ok:false,error:'method_not_allowed'});
  try{
    await authenticate(request);
    const body=await request.json().catch(()=>({})),question=clip(body.question,1200),context=body.context||{};
    if(!question)return json(400,{ok:false,error:'question_required'});
    const base=env('BUILDPRO_OSS_AI_BASE_URL',false).replace(/\/$/,'');
    if(!base)return json(200,{ok:true,mode:'server_builtin',answer:builtIn(question,context)});
    const key=env('BUILDPRO_OSS_AI_API_KEY',false),model=env('BUILDPRO_OSS_AI_MODEL',false)||'Qwen/Qwen2.5-7B-Instruct';
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),24000);
    try{
      const response=await fetch(`${base}/chat/completions`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json',...(key?{Authorization:`Bearer ${key}`}:{})},body:JSON.stringify({model,temperature:.1,max_tokens:650,messages:[{role:'system',content:'あなたはBUILD PROの業務伴走AIです。入力された事実だけを使い、推測を事実として書きません。回答は日本語で短く、確認事項、次の行動、完了条件の順に示します。個人情報や秘密を回答へ再掲しすぎないでください。'},{role:'user',content:JSON.stringify({question,context:{screen:clip(context.screen,80),caseName:clip(context.caseName,120),score:clip(context.score,80),official:clip(context.official,800),strong:clip(context.strong,500),weak:clip(context.weak,500),neutralizers:clip(context.neutralizers,500),scenarios:clip(context.scenarios,1000)}})}]})});
      if(!response.ok)throw new Error(`oss_ai_http_${response.status}`);
      const data=await response.json(),answer=clip(data?.choices?.[0]?.message?.content,5000);
      if(!answer)throw new Error('oss_ai_empty_response');
      return json(200,{ok:true,mode:'central_open_source_model',model,answer});
    }catch(error){return json(200,{ok:true,mode:'server_builtin_fallback',warning:String(error?.message||error),answer:builtIn(question,context)})}finally{clearTimeout(timer)}
  }catch(error){const message=String(error?.message||error),status=/authentication|required|invalid_buildpro_session/.test(message)?401:/staff_master/.test(message)?403:500;return json(status,{ok:false,error:message})}
});
