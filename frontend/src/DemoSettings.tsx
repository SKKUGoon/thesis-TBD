import {useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useDemoMode} from './demo';
import {ApiError,describeError,request} from './api';

type Settings={start_date:string;forecast_loading_seconds:number;available_start_dates:string[]};
export default function DemoSettings({token,owner,disabled,onChoose}:{token:string;owner:string;disabled:boolean;onChoose:(id:string)=>void}){
 const enabled=useDemoMode(token,owner);
 const query=useQuery({queryKey:[owner,'demo-settings'],queryFn:({signal})=>request<Settings>(token,'','/demo/settings',{signal}),retry:false,enabled});
 if(!enabled)return null;
 if(query.error instanceof ApiError&&query.error.status===404)return null;
 if(query.isPending)return <p role="status">설정을 확인하고 있습니다.</p>;
 if(query.error)return <p role="alert">{describeError(query.error)}</p>;
 return <details className="demo-settings"><summary>Demo Setting</summary><SettingsForm key={query.data.start_date+':'+query.data.forecast_loading_seconds} settings={query.data} token={token} owner={owner} disabled={disabled} onChoose={onChoose}/></details>;
}
function SettingsForm({settings,token,owner,disabled,onChoose}:{settings:Settings;token:string;owner:string;disabled:boolean;onChoose:(id:string)=>void}){
 const client=useQueryClient();
 const [date,setDate]=useState(settings.start_date),[seconds,setSeconds]=useState(String(settings.forecast_loading_seconds)),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef<{body:string;key:string}|null>(null);
 async function save(){
  if(busy||disabled)return;
  const body=JSON.stringify({start_date:date,forecast_loading_seconds:Number(seconds)});
  if(pending.current?.body!==body)pending.current={body,key:crypto.randomUUID()};
  setBusy(true);setError('');
  try{
   const response=await request<{scenario_id:string}>(token,'','/demo/settings',{body,key:pending.current.key});
   await client.invalidateQueries({queryKey:[owner,'scenarios']});
   onChoose(response.scenario_id);
   await client.invalidateQueries({queryKey:[owner,'demo-settings']});
  }catch(e){setError(describeError(e));}finally{setBusy(false)}
 }
 const valid=/^\d+$/.test(seconds)&&Number(seconds)<=600;
 return <form onSubmit={e=>{e.preventDefault();void save()}}><div className="form-grid"><label>시작 날짜<select aria-label="시작 날짜" value={date} onChange={e=>setDate(e.target.value)} disabled={disabled||busy}>{settings.available_start_dates.map(d=><option key={d} value={d}>{d}</option>)}</select></label><label>예측 로딩 시간 (초)<input aria-label="예측 로딩 시간 (초)" type="number" min="0" max="600" step="1" required value={seconds} onChange={e=>setSeconds(e.target.value)} disabled={disabled||busy}/></label></div><p className="small muted">저장된 예측을 지정한 시간 뒤에 수신합니다. 적용하면 선택한 날짜의 새 업무를 열며, 기존 업무와 이력은 보존합니다.</p>{error&&<p className="notice error" role="alert">{error}</p>}<button className="primary" disabled={disabled||busy||!valid}>{busy?'적용 중…':'설정 적용'}</button></form>;
}
