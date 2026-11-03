import Decimal from 'decimal.js';
import type { Forecast,Proposal,Workspace } from './types';
Decimal.set({precision:40});
export const labels:Record<string,string>={bootstrapping:'초기 자료 준비 중',ready:'준비 완료',advancing:'주간 자료 처리 중',queued:'접수 대기',running:'처리 중',succeeded:'완료',retry_wait:'재시도 대기',failed:'실패',blocked:'차단',pending_send:'전송 대기',sending:'전송 확인 중',accepted:'접수 완료',submission_unknown:'접수 여부 확인 필요',rejected:'거절',voided:'종료',not_received:'미입고',partially_received:'부분 입고',received:'전체 입고',bootstrap:'초기 준비',simulate:'주간 자료 생성',ingest:'자료 검사',forecast:'TimesFM 예측',proposal:'주문안 준비',submit:'주문 전달',reconcile:'공급처 조회',receipt:'입고 반영',evaluate:'실제값 평가',pending:'승인 대기',draft:'검토 중',approved:'승인 완료',expired:'승인 기한 경과',superseded:'새 버전으로 교체',input_revised:'입력 정정으로 무효'};
export const label=(value:string):string=>labels[value]||value;
export const quantity=(value:string|number|undefined):string=>value===undefined?'—':new Decimal(value).toFixed().replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
export const positive=(value:string):boolean=>new Decimal(value).gt(0);
export const total=(values:string[]):string=>values.reduce((sum,v)=>sum.plus(v),new Decimal(0)).toFixed();
export function validReceipt(value:string,remaining:string):boolean {return /^(0|[1-9][0-9]{0,13})(\.[0-9]{1,6})?$/.test(value)&&new Decimal(value).lte(remaining);}
export function addDays(day:string,n:number):string {const d=new Date(day+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);}
export const day=(value:string|null|undefined):string=>value?value.slice(5,10).replace('-','/'):'—';
export function clock(value:string|null|undefined):string {return value?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—';}
export function currentResults(data:Workspace):{forecast:Forecast|undefined;proposal:Proposal|undefined} {
  // Bind to the validated snapshot for the completed weekly delivery; do not pick any old success.
  if(data.scenario.status!=='ready'||!data.scenario.last_completed_week_start)return {forecast:undefined,proposal:undefined};
  const delivery=data.deliveries.filter(d=>d.kind==='weekly'&&d.status==='validated'&&d.week_start===data.scenario.last_completed_week_start).sort((a,b)=>b.source_revision-a.source_revision)[0];
  const forecast=data.forecasts.filter(f=>f.snapshot_id===delivery?.snapshot_id&&f.validity==='current'&&f.status==='succeeded').sort((a,b)=>(b.generated_at||'').localeCompare(a.generated_at||''))[0];
  const proposal=data.proposals.filter(p=>p.forecast_id===forecast?.id).sort((a,b)=>b.version-a.version)[0];
  return {forecast,proposal};
}
