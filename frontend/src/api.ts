import type { Journal } from './types';

export class ApiError extends Error {
  constructor(public status:number, public code:string, public requestId?:string) { super(code); }
}
export async function request<T>(token:string,scope:string,path:string,options:{signal?:AbortSignal;body?:string;key?:string}={}):Promise<T> {
  const headers:Record<string,string>={Authorization:`Bearer ${token}`};
  if(scope) headers['X-Workspace-ID']=scope;
  if(options.body!==undefined){headers['Content-Type']='application/json';headers['Idempotency-Key']=options.key!;}
  let response:Response;
  try { response=await fetch('/api/v1'+path,{method:options.body===undefined?'GET':'POST',headers,body:options.body,signal:options.signal,cache:'no-store'}); }
  catch(error){if(error instanceof DOMException&&error.name==='AbortError')throw error;throw new ApiError(0,'CONNECTION_LOST');}
  let value:unknown;
  try{value=await response.json();}catch{throw new ApiError(response.status>=400?response.status:0,'INVALID_RESPONSE');}
  if(!response.ok){const body=value as {error?:{code?:string};request_id?:string};throw new ApiError(response.status,body.error?.code||'HTTP_ERROR',body.request_id);}
  return value as T;
}
export async function all<T>(token:string,scope:string,path:string,signal?:AbortSignal):Promise<T[]> {
  const items:T[]=[];let cursor:string|null=null;const seen=new Set<string>();
  do{
    const suffix:URLSearchParams=new URLSearchParams({limit:'100',...(cursor?{cursor}:{})});
    const page:{items:T[];next_cursor:string|null}=await request<{items:T[];next_cursor:string|null}>(token,scope,path+(path.includes('?')?'&':'?')+suffix,{signal});
    items.push(...page.items);cursor=page.next_cursor;
    if(cursor&&seen.has(cursor))throw new ApiError(0,'REPEATED_CURSOR');
    if(cursor)seen.add(cursor);
  }while(cursor);
  return items;
}
const journalKey='mtech.pending.v1';
export function readJournal(owner:string):Journal|null {
  try{const j=JSON.parse(sessionStorage.getItem(journalKey)||'null') as Journal|null;
    if(j&&j.owner===owner&&typeof j.scope==='string'&&typeof j.path==='string'&&/^\/(test\/scenarios|order-proposals|purchase-orders|jobs|receipts)(\/|$)/.test(j.path)&&!j.path.includes('..')&&typeof j.body==='string'&&typeof j.key==='string')return j;
  }catch{/* Invalid local state never starts a request. */}
  return null;
}
export function writeJournal(journal:Journal|null):void {
  if(journal)sessionStorage.setItem(journalKey,JSON.stringify(journal));else sessionStorage.removeItem(journalKey);
}
export function describeError(error:unknown):string {
  if(!(error instanceof ApiError))return error instanceof Error?error.message:'처리 중 오류가 발생했습니다.';
  if(error.code==='PRERECORDED_FORECAST_UNAVAILABLE')return '이후 주간의 예측 결과가 없습니다. 설정에서 시작 날짜를 변경해 주십시오.';
  if(error.code==='INVALID_SETTINGS_OR_REQUEST')return '시작 날짜와 예측 로딩 시간(0–600초)을 확인해 주십시오.';
  if(error.status===401)return '인증이 만료되었거나 유효하지 않습니다. 토큰을 다시 입력해 주십시오.';
  if(error.status===403)return '이 작업을 수행할 권한이 없습니다.';
  if(error.status===404)return '접근 가능한 자료가 없습니다. 업무 범위를 확인해 주십시오.';
  if(error.status===409)return `현재 자료가 변경되었거나 작업 조건을 충족하지 않습니다. 최신 상태를 다시 확인해 주십시오. (${error.code})`;
  if(error.status===0||error.status>=500)return '서버와 연결을 확인할 수 없습니다. 요청 접수 여부를 확인해 주십시오.';
  return `요청을 처리하지 못했습니다. (${error.code})`;
}
