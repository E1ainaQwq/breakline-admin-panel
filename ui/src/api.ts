export let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api(path: string, data?: unknown): Promise<any> {
  const response = await fetch('/api/panel/'+path,{method:data===undefined?'GET':'POST',credentials:'same-origin',headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(35000)});
  const value=await response.json();
  if(response.status===401)window.dispatchEvent(new Event('breakline:session-expired'));
  if(!response.ok || value.ok!==true) throw new Error(value.message||'管理服务暂不可用');
  return value.data??value;
}
export function query(path: string, values: Record<string, string | number>) {return path+'?'+new URLSearchParams(Object.entries(values).map(([k,v])=>[k,String(v)])).toString();}
export async function uploadFile(server:string,path:string,file:File):Promise<any>{
 const response=await fetch('/api/panel/'+query('instance/upload',{server,path}),{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf},body:file,signal:AbortSignal.timeout(15*60*1000)});
 const value=await response.json();if(response.status===401)window.dispatchEvent(new Event('breakline:session-expired'));
 if(!response.ok||value.ok!==true)throw new Error(value.message||'上传未完成，目标文件未替换');return value.data;
}
