const API = process.env.NEXT_PUBLIC_API_URL ?? '';
export type Project = { id:string; title:string; question:string; scope:string; review_policy:string; created_at:string; run_count:number };
export type Paper = { id:string; title:string; authors:string[]; year:number|null; doi:string|null; url:string; abstract:string; venue:string; citation_count:number; has_full_text?:boolean };
export type Artifact = { id:string; stage:string; version:number; content:any; status:string; model:string; reviewer_note:string; created_at:string };
export type Run = { id:string; project_id:string; status:string; current_stage:string; models:Record<string,string>; settings:Record<string,any>; artifacts:Artifact[]; papers?:Paper[]; created_at:string; updated_at:string };
export type Provider = {id:string;gateway:string;configured:boolean;model:string};
export async function api<T>(path:string, init?:RequestInit):Promise<T>{
  let res:Response;
  try { const headers:Record<string,string>={...init?.headers as Record<string,string>};if(!(init?.body instanceof FormData))headers['Content-Type']='application/json';res=await fetch(`${API}/api/v1${path}`,{...init,headers,cache:'no-store'}); }
  catch { throw new Error('Cannot reach the API. Start the FastAPI server at '+API+'.'); }
  if(!res.ok){let msg=`Request failed (${res.status})`;try{msg=(await res.json()).detail||msg}catch{}throw new Error(msg)}
  return res.json();
}
export const STAGES=['search','extraction','organization','synthesis'];
