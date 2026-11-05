import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {all,describeError,request} from './api';
import {addDays,clock,day,label,total} from './domain';
import {Modal} from './Modal';
import {useDemoMode} from './demo';
import HistoryChart from './HistoryChart';
import type {Forecast,Item,Proposal,Schema,Workspace} from './types';

export type DashboardPanel='자료·처리 내역'|'발주 검토'|'공급처 접수'|'입고 기록';
type Props={token:string;owner:string;scope:string;data?:Workspace;forecast?:Forecast;proposal?:Proposal;item?:Item;onSelect:(id:string)=>void;onSettings:()=>void;onLogout:()=>void;onDeliver:()=>void;canReset:boolean;resetDisabled:boolean;onReset:()=>void;panel:DashboardPanel|null;onPanel:(panel:DashboardPanel|null)=>void;disabled:boolean;message:string;isError:boolean;panels:Record<DashboardPanel,ReactNode>};
const displayQuantity=(value:string)=>Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2});
function Icon({name}:{name:string}){
 const paths:Record<string,ReactNode>={dashboard:<><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></>,profile:<><circle cx="12" cy="12" r="9"/><circle cx="12" cy="9" r="3"/><path d="M5 19c2-6 12-6 14 0"/></>,settings:<><circle cx="12" cy="12" r="4"/><path d="m9 3 1-1h4l1 1v3l3 1 3 2v6l-3 2-3 1v3H9v-3l-3-1-3-2V9l3-2 3-1Z"/></>,chat:<><path d="M3 4h18v13H8l-5 4Z"/><path d="M8 9h8M8 13h5"/></>,collapse:<><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m7-13-3 4 3 4"/></>,bell:<><path d="M5 17h14l-2-4V9a5 5 0 0 0-10 0v4Zm5 3h4M3 7l2-3m14 0 2 3"/></>,logout:<><path d="M9 4H4v16h5M13 8l4 4-4 4M8 12h13"/></>,close:<path d="m6 6 12 12M18 6 6 18"/>};
 return <svg className="dash-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.dashboard}</svg>;
}
export default function Dashboard(p:Props){
 const [collapsed,setCollapsed]=useState(true),[view,setView]=useState<'dashboard'|'chat'>('dashboard'),[tab,setTab]=useState('orders'),[dialog,setDialog]=useState(''),[alarm,setAlarm]=useState(false),[topic,setTopic]=useState(''),[input,setInput]=useState('');
 const demoMode=useDemoMode(p.token,p.owner);
 const [selectedModel,setSelectedModel]=useState<'timesfm'|'naive'>('timesfm');
 const model=demoMode?selectedModel:'timesfm';
 const values=useQuery({queryKey:[p.owner,p.scope,'dashboard-values',p.forecast?.id,model],enabled:!!p.forecast,queryFn:({signal})=>all<Omit<Schema['ForecastValue'],'prediction'> & {prediction:string|null}>(p.token,p.scope,`/forecasts/${p.forecast!.id}/values${model==='naive'?'?model=naive':''}`,signal)});
 const failures=p.data?.jobs.filter(j=>j.status==='failed')||[];const warningCount=(p.data?.scenario.inventory_source==='not_registered'?1:0)+failures.length;
 const choose=(id:string)=>{p.onSelect(id);setView('dashboard')};
 const latest=values.data?.filter(v=>v.item_id===p.item?.id)||[];
 const answer=topic==='재고 확인'?'실제 재고·포장 단위·납기가 아직 등록되지 않았습니다. 현재 자료만으로 주문 수량을 계산할 수 없습니다.':topic==='처리 내역'?`접수된 처리 작업 ${p.data?.jobs.length||0}개, 완료 ${p.data?.jobs.filter(j=>j.status==='succeeded').length||0}개, 실패 ${failures.length}개입니다.`:topic==='예측 요약'?p.forecast&&p.item?`${p.item.name}의 ${day(p.forecast.forecast_origin)}~${day(addDays(p.forecast.forecast_origin,13))} 예측입니다. ${latest.length===14&&latest.every(v=>v.prediction!==null)?'14일 합계 '+displayQuantity(total(latest.map(v=>v.prediction!)))+p.item.base_unit:'예측값을 확인하고 있습니다.'}`:'먼저 주간 자료를 전달해 주십시오.':'조회할 항목을 선택해 주십시오. 이 화면은 현재 업무 자료를 조회하며 자유 질문에 대한 AI 분석은 아직 연결되지 않았습니다.';
 return <div className={`live-dashboard ${collapsed?'sidebar-collapsed':''}`} data-testid="live-dashboard">
 <aside className="dash-sidebar"><nav className="sidebar-top-strip" aria-label="상단 메뉴">
 <button aria-label="프로필" title="프로필" onClick={()=>setDialog('프로필')}><Icon name="profile"/></button>
 <button aria-label="대시보드" title="대시보드" className={view==='dashboard'?'active':''} onClick={()=>setView('dashboard')}><Icon name="dashboard"/></button>
 <button aria-label="업무 설정" title="업무 설정" onClick={p.onSettings}><Icon name="settings"/></button>
 <button aria-label="업무 조회" title="업무 조회" className={view==='chat'?'active':''} onClick={()=>{setView('chat');setTopic('')}}><Icon name="chat"/></button>
 <button aria-label="접속 종료" title="접속 종료" onClick={p.onLogout}><Icon name="logout"/></button>
 <button aria-label={collapsed?'사이드바 펼치기':'사이드바 접기'} title={collapsed?'사이드바 펼치기':'사이드바 접기'} onClick={()=>setCollapsed(!collapsed)}><Icon name="collapse"/></button>
 </nav><div className="dash-brand"><img src="/company_logo.png" alt="MTECH"/></div>{!collapsed&&<div className="dash-sidebar-body">{['예측 요약','재고 확인','처리 내역'].map(t=><button className={topic===t&&view==='chat'?'selected':''} key={t} onClick={()=>{setView('chat');setTopic(t)}}>{t}</button>)}<button onClick={()=>p.onPanel('자료·처리 내역')}>주간 자료·처리 내역</button></div>}</aside>
 <main className="dash-main">{p.message&&<div className={`dash-status ${p.isError?'error':''}`} role={p.isError?'alert':'status'}>{p.message}</div>}
 {view==='dashboard'?<><section className="card dashboard-toolbar" aria-label="주간 자료 관리"><div className="toolbar-date">기준일 {clock(p.data?.scenario.simulation_at)}</div><div className="dash-week-actions"><button disabled={p.disabled||p.data?.scenario.status!=='ready'} onClick={p.onDeliver}>주간 자료 전달</button>{p.canReset&&<button className="reset-week-button" disabled={p.resetDisabled} onClick={p.onReset} title="마지막으로 전달한 주간을 다시 시험합니다">지난주 데이터 초기화</button>}{demoMode&&<button className="model-switch-button" title="모델 변경" aria-label="모델 변경" aria-pressed={model==='naive'} onClick={()=>setSelectedModel(model==='timesfm'?'naive':'timesfm')}>{model==='timesfm'?'TimesFM':'Naive'}</button>}<button aria-label="주간 자료와 처리 내역" title="주간 자료·처리 내역" onClick={()=>p.onPanel('자료·처리 내역')}>•••</button></div></section>
 <div className="dashboard-grid"><div className="left-column">
 <section className="card patient-card"><h3>일별 환자 집계</h3>{p.forecast?<PatientChart token={p.token} scope={p.scope} owner={p.owner} forecast={p.forecast}/>:<div className="dash-empty">주간 자료 전달 후 환자 집계와 예측을 표시합니다.</div>}</section>
 <section className="card forecast-card"><div className="forecast-header"><h3>{p.item?.kind==='material'?'물품 출고량':'약품 사용량'} 예측</h3><label className="dash-item-selector"><span className="sr-only">품목 선택</span><select aria-label="품목 선택" value={p.item?.id||''} onChange={e=>choose(e.target.value)}>{p.data?.items.map(i=><option value={i.id} key={i.id}>{i.name}</option>)}</select></label></div>
 {p.forecast&&p.item?<><HistoryChart token={p.token} scope={p.scope} owner={p.owner} itemId={p.item.id} forecastId={p.forecast.id} kind={p.item.kind} model={p.item.kind==='drug'?model:'timesfm'} compact/></>:<div className="dash-empty" role="status">{p.data?.scenario.status==='advancing'?<span className="forecast-loading">예측 결과를 수신하고 있습니다.</span>:'현재 주간의 예측이 없습니다.'}</div>}
 </section></div>
 <div className="right-column"><section className="card order-card"><div className="table-header"><div className="tab-bar"><button className={tab==='orders'?'active':''} onClick={()=>setTab('orders')}>권장 발주 물량</button><button className={tab==='receipts'?'active':''} onClick={()=>setTab('receipts')}>접수·입고</button><button onClick={()=>setDialog('진행 중 경매')}>진행 중 경매</button></div><button className="order-all-btn" disabled={!p.proposal||p.disabled} onClick={()=>p.onPanel('발주 검토')}>전체 주문하기</button></div>
 {tab==='orders'?<><p className="dash-date">{p.forecast?'예측 '+p.forecast.forecast_origin+' 기준 · 다음 14일':'주간 자료 전달 대기'}</p><div className="order-table-scroll"><table className="order-table"><thead><tr><th>약품·물품명</th><th className="n">예측 사용·출고량</th><th className="n">권장 발주량</th></tr></thead><tbody>{p.data?.items.map(i=>{const v=values.data?.filter(v=>v.item_id===i.id)||[];const l=p.proposal?.lines.find(l=>l.item_id===i.id);return <tr key={i.id} className={i.id===p.item?.id?'selected':''}><td><button className="item-button" aria-pressed={i.id===p.item?.id} onClick={()=>choose(i.id)}>{i.name}</button><span className="dash-unit">{i.base_unit}</span></td><td className="n">{v.length===14&&v.every(x=>x.prediction!==null)?displayQuantity(total(v.map(x=>x.prediction!))):'—'}</td><td className="n">{l?<span className="pill">{l.packs}포장</span>:<span className="dash-unregistered">미등록</span>}</td></tr>})}</tbody></table></div>{values.error&&<p role="alert">{describeError(values.error)}</p>}<div className="dash-table-footer"><span data-testid="forecast-count">품목 {p.data?.items.length||0}개 · 날짜별 예측 {values.data?.length||0}/{p.forecast?.expected_values||0}개</span><p>실재고·포장 단위·납기를 등록한 뒤 발주량을 계산합니다.</p><button onClick={()=>p.onPanel('발주 검토')}>재고·발주 정보 확인</button></div></>:<div className="dash-order-status"><h3>접수와 입고</h3><p>{p.data?.orders.length?`주문 ${p.data.orders.length}건이 있습니다.`:'현재 전달된 주문이 없습니다.'}</p><button onClick={()=>p.onPanel('공급처 접수')}>공급처 접수 확인</button><button onClick={()=>p.onPanel('입고 기록')}>입고 기록 확인</button></div>}
 </section></div></div></>:<section className="card dash-chat"><header><h3>{topic||'업무 조회'}</h3><p className="muted small">현재 접수된 자료 기준</p></header><div className="dash-chat-content"><div className="dash-answer">{answer}</div>{topic==='예측 요약'&&p.forecast&&p.item&&<HistoryChart token={p.token} scope={p.scope} owner={p.owner} itemId={p.item.id} forecastId={p.forecast.id} kind={p.item.kind}/>}</div><div className="dash-query-options">{['예측 요약','재고 확인','처리 내역'].map(t=><button key={t} onClick={()=>setTopic(t)}>{t}</button>)}</div><form onSubmit={e=>{e.preventDefault();setTopic(input.trim());setInput('')}}><label className="sr-only" htmlFor="dash-query">업무 조회 항목</label><input id="dash-query" value={input} onChange={e=>setInput(e.target.value)} placeholder="예측 요약, 재고 확인, 처리 내역"/><button disabled={!input.trim()}>조회</button></form></section>}
 </main>
 {warningCount>0&&<button className="dash-alarm" aria-label={`운영 경보 ${warningCount}개 보기`} onClick={()=>setAlarm(!alarm)}><Icon name="bell"/>경보 <strong>{warningCount}</strong></button>}
 {alarm&&<aside className="dash-alarm-panel card"><div className="dash-card-title"><h3>운영 경보</h3><button aria-label="경보 닫기" onClick={()=>setAlarm(false)}><Icon name="close"/></button></div>{p.data?.scenario.inventory_source==='not_registered'&&<article><strong>재고·발주 정보 등록 필요</strong><p>실재고·포장 단위·납기가 미등록입니다. 재고 부족 여부와 권장 발주량은 아직 판단할 수 없습니다.</p></article>}{failures.map(j=><article key={j.id}><strong>{label(j.stage)} 처리 실패</strong><p>{j.error_code}</p></article>)}</aside>}
 {dialog&&<Modal title={dialog} className="dash-modal" onClose={()=>setDialog('')}>{dialog==='프로필'?<><p>현재 업무에 부여된 역할</p><p>{p.data?.principal.roles.join(', ')||'권한 확인 중'}</p></>:<p>경매 서비스는 아직 연결되지 않았습니다. 현재 진행 중인 경매를 표시할 수 없습니다.</p>}</Modal>}
 {p.panel&&<Modal title={p.panel} className="dash-modal wide" onClose={()=>p.onPanel(null)}><div className="dash-action-content">{p.panels[p.panel]}</div></Modal>}
 </div>;
}
function PatientChart({token,scope,owner,forecast}:{token:string;scope:string;owner:string;forecast:Forecast}){
 const q=useQuery({queryKey:[owner,scope,'patient-history',forecast.id],queryFn:({signal})=>request<Schema['PatientHistory']>(token,scope,`/forecasts/${forecast.id}/patient-history`,{signal})});
 if(q.isPending)return <p role="status">환자 집계를 불러오고 있습니다.</p>;
 if(q.error)return <p role="alert">{describeError(q.error)}</p>;
 return <PatientPlot values={q.data.values}/>;
}
function PatientPlot({values:v}:{values:Schema['PatientHistory']['values']}){
 const frame=useRef<HTMLDivElement>(null),[size,setSize]=useState({width:550,height:260});
 useEffect(()=>{const observer=new ResizeObserver(([entry])=>setSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(frame.current!);return()=>observer.disconnect()},[]);
 const width=Math.max(1,size.width),height=Math.max(1,size.height),left=44,right=14,top=30,bottom=48;
 const max=Math.max(1,...v.flatMap(r=>[r.inpatient??0,r.outpatient??0]))*1.1;
 const x=(i:number)=>left+i*Math.max(1,width-left-right)/Math.max(1,v.length-1),y=(n:number)=>height-bottom-n*Math.max(1,height-top-bottom)/max;
 const path=(key:'inpatient'|'outpatient')=>{let pen=false;return v.map((r,i)=>{if(r[key]===null){pen=false;return ''}const s=`${pen?'L':'M'}${x(i)},${y(r[key]!)}`;pen=true;return s}).join(' ')};
 return <div ref={frame} className="patient-chart-frame"><svg className="patient-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="현재 예측 입력에 포함된 28일 입원·외래 환자 집계" data-testid="patient-chart"><text x="0" y="17">환자 집계 (명)</text>{[0,max/2,max].map(n=><g key={n}><line x1={left} x2={width-right} y1={y(n)} y2={y(n)}/><text x={left-8} y={y(n)+4} textAnchor="end">{Math.round(n)}</text></g>)}<path className="patient-in" d={path('inpatient')}/><path className="patient-out" d={path('outpatient')}/>{[0,Math.floor(v.length/2),v.length-1].filter((n,i,a)=>a.indexOf(n)===i).map(i=><text key={i} x={x(i)} y={height-27} textAnchor={i===0?'start':i===v.length-1?'end':'middle'}>{day(v[i]?.day)}</text>)}<circle cx={width/2-66} cy={height-9} r="4" fill="#5470c6"/><text x={width/2-56} y={height-5}>입원</text><circle cx={width/2+26} cy={height-9} r="4" fill="#91cc75"/><text x={width/2+36} y={height-5}>외래</text></svg></div>;
}
