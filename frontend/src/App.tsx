import {useCallback,useEffect,useRef,useState} from 'react';
import type {FormEvent,ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {all,ApiError,describeError,readJournal,request,writeJournal} from './api';
import {addDays,clock,currentResults,day,label,positive,quantity} from './domain';
import type {Accepted,Decision,Delivery,Forecast,Inventory,Item,Job,Journal,Order,Principal,Proposal,Scenario,Schema,Workspace} from './types';
import Dashboard from './Dashboard';
import {Modal} from './Modal';
export {Modal} from './Modal';
import DemoSettings from './DemoSettings';
import type {DashboardPanel} from './Dashboard';
import {ProposalTable,ReceiptPanel} from './OrderForms';

const ACTIVE=new Set(['queued','running','retry_wait']);
function Logo(){return <span className="logo-frame"><img src="/company_logo.png" alt="MTeCH"/></span>}
function Message({children,error=false}:{children:ReactNode;error?:boolean}){return <div className={error?'notice error':'notice'} role={error?'alert':'status'}>{children}</div>}
function Login({onLogin}:{onLogin:(token:string,principal:Principal)=>void}){
  const [value,setValue]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{const token=value.trim().replace(/^Bearer\s+/i,'');const me=await request<Principal>(token,'','/me');setValue('');onLogin(token,me);}catch(e){setError(describeError(e));}finally{setBusy(false)}}
  return <div className="login-shell"><header className="login-brand"><Logo/></header><main className="login-panel"><h1>접속</h1><p className="muted">발급받은 접속 토큰으로 업무를 시작합니다.</p><form onSubmit={submit}><label>접속 토큰<input type="password" value={value} onChange={e=>setValue(e.target.value)} autoComplete="off" required disabled={busy}/></label><label className="muted file-label">또는 토큰 파일 선택<input type="file" aria-label="토큰 파일" disabled={busy} onChange={async e=>{const file=e.target.files?.[0];if(file){if(file.size>16384){setError('토큰 파일 크기를 확인해 주십시오.');return;}setValue((await file.text()).trim());}e.target.value='';}}/></label>{error&&<Message error>{error}</Message>}<button className="primary" disabled={!value.trim()||busy}>{busy?'확인 중…':'연결'}</button></form></main></div>
}
export default function App(){
  const [session,setSession]=useState<{token:string;principal:Principal}|null>(null);const client=useQueryClient();
  if(!session)return <Login onLogin={(token,principal)=>setSession({token,principal})}/>;
  return <Session key={session.principal.id} {...session} onLogout={()=>{void client.cancelQueries();client.clear();setSession(null)}}/>;
}
function Session({token,principal,onLogout}:{token:string;principal:Principal;onLogout:()=>void}){
  const queryClient=useQueryClient();
  const saved=readJournal(principal.id);
  const [scope,setScope]=useState(()=>saved?.scope||new URLSearchParams(location.search).get('workspace')||'');
  const [panel,setPanel]=useState<DashboardPanel|null>(null);
  const [journal,setJournal]=useState<Journal|null>(saved),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[completion,setCompletion]=useState('');
  const [resetConfirm,setResetConfirm]=useState(false);
  const [setup,setSetup]=useState(false),[confirm,setConfirm]=useState<Proposal|null>(null),[item,setItem]=useState(''),[orderId,setOrderId]=useState('');
  const locked=useRef(false);
  const store=useCallback((j:Journal|null)=>{writeJournal(j);setJournal(j)},[]);
  useEffect(()=>{const url=new URL(location.href);scope?url.searchParams.set('workspace',scope):url.searchParams.delete('workspace');url.searchParams.delete('step');history.replaceState(null,'',url)},[scope]);
  const scenarios=useQuery({queryKey:[principal.id,'scenarios'],queryFn:({signal})=>all<Scenario>(token,'','/test/scenarios',signal).then(items=>items.filter(s=>s.data_source==='records')),enabled:principal.platform_role==='test_admin'});
  const workspace=useQuery({queryKey:[principal.id,scope,'workspace'],enabled:!!scope,queryFn:async({signal}):Promise<Workspace>=>{
    const [scenario,me,items,inventory,jobs,forecasts,proposals,orders,deliveries]=await Promise.all([
      request<Scenario>(token,scope,'/test/scenarios/'+scope,{signal}),request<Principal>(token,scope,'/me',{signal}),all<Item>(token,scope,'/items',signal),all<Inventory>(token,scope,'/inventory',signal),all<Job>(token,scope,'/jobs',signal),all<Forecast>(token,scope,'/forecasts',signal),all<Proposal>(token,scope,'/order-proposals',signal),all<Order>(token,scope,'/purchase-orders',signal),all<Delivery>(token,scope,'/weekly-deliveries',signal)]);
    items.sort((a,b)=>a.code.localeCompare(b.code));
    return {scenario,principal:me,items,inventory,jobs,forecasts,proposals,orders,deliveries};
  },refetchInterval:q=>q.state.error||q.state.data?.scenario.status==='advancing'||q.state.data?.scenario.status==='bootstrapping'||q.state.data?.jobs.some(j=>ACTIVE.has(j.status))||q.state.data?.orders.some(o=>['pending_send','sending'].includes(o.status))?2000:false});
  const data=workspace.data?.scenario.data_source==='records'?workspace.data:undefined;
  useEffect(()=>{if(workspace.data&&workspace.data.scenario.data_source!=='records'&&!journal){setScope('');setPanel(null)}},[workspace.data,journal]);
  useEffect(()=>{if(!scope&&scenarios.data?.length&&!journal)setScope(scenarios.data.at(-1)!.id)},[scope,scenarios.data,journal]);
  const roles=data?.principal.roles||[];
  const can=(role:string)=>roles.includes(role)||roles.includes('admin');
  const {forecast,proposal}=data?currentResults(data):{forecast:undefined,proposal:undefined};
  const pendingJob=journal?.accepted&&data?.jobs.find(j=>j.id===journal.accepted?.job_id);
  const readOnly=busy||!!journal||workspace.isError||!!(scope&&!data);
  const command=async(path:string,body:object,commandScope=scope,replay?:Journal)=>{
    if(locked.current||(!replay&&journal))return;
    locked.current=true;setBusy(true);setError('');setNotice('');
    const next:Journal=replay||{owner:principal.id,scope:commandScope,path,body:JSON.stringify(body),key:crypto.randomUUID(),createdAt:new Date().toISOString()};
    try{
      // Persist before sending. If storage fails, no request is sent.
      store(next);
      const response=await request<Accepted|Decision|Schema['ScenarioAccepted']>(token,next.scope,next.path,{body:next.body,key:next.key});
      if('job_id'in response){
        const target='scenario_id'in response?response.scenario_id:next.scope;
        store({...next,scope:target,accepted:response});if(target!==scope)setScope(target);
        setNotice('요청을 접수했습니다. 처리 결과를 확인하고 있습니다.');
      }else{
        store(null);setConfirm(null);if(next.path.endsWith('/decisions')){setPanel('공급처 접수');setOrderId(response.order_ids[0]||'');setNotice(response.decision==='reject'?'주문하지 않음으로 기록했습니다.':response.order_ids.length?'승인을 기록했습니다. 공급처 접수를 확인하고 있습니다.':'추가 주문이 없어 주문을 생성하지 않았습니다.');}
      }
      await Promise.all([queryClient.invalidateQueries({queryKey:[principal.id,commandScope,'workspace']}),queryClient.invalidateQueries({queryKey:[principal.id,'scenarios']})]);
    }catch(e){
      setConfirm(null);
      if(e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==401){store(null);setConfirm(null);await queryClient.invalidateQueries({queryKey:[principal.id,commandScope,'workspace']});}
      setError(describeError(e));
    }finally{locked.current=false;setBusy(false)}
  };
  useEffect(()=>{
    if(!journal?.accepted||!data||scope!==journal.scope)return;
    const job=data.jobs.find(j=>j.id===journal.accepted?.job_id);
    const weekly=journal.path.endsWith('/weekly-deliveries')||journal.path==='/test/scenarios'||journal.path.endsWith('/reset-last-week')||journal.path.endsWith('/retry')&&data.scenario.status!=='ready';
    if(job?.status==='succeeded'&&(!weekly||data.scenario.status==='ready')){
      store(null);setNotice('');setCompletion(journal.path.endsWith('/reset-last-week')?'지난주 데이터 초기화를 완료했습니다. 주간 자료 전달로 같은 주간을 다시 시험할 수 있습니다.':'처리를 완료했습니다.');
      if(journal.path.endsWith('/weekly-deliveries'))setPanel(null);
      void queryClient.invalidateQueries({queryKey:[principal.id,scope,'workspace']});
      void queryClient.invalidateQueries({queryKey:[principal.id,'scenarios']});
    }
  },[journal,data,scope,store,principal.id,queryClient]);
  function changeScope(id:string){if(busy||journal)return;void queryClient.cancelQueries({queryKey:[principal.id,scope]});setScope(id);setPanel(null);setItem('');setOrderId('');setConfirm(null);setError('');setNotice('');setCompletion('');setSetup(false);}
  const selectedOrder=data?.orders.find(o=>o.id===orderId)||data?.orders.filter(o=>!o.released_at&&o.receipt_status!=='received').sort((a,b)=>(b.accepted_at||'').localeCompare(a.accepted_at||''))[0]||data?.orders.at(-1);
  const selectedItem=data?.items.find(i=>i.id===item)||data?.items[0];
  const expired=proposal&&data?new Date(proposal.expires_at)<=new Date(data.scenario.simulation_at):false;
  const blocked=!!proposal&&(proposal.blocking_codes.length>0||proposal.lines.some(l=>l.blocking_codes.length>0));
  const approvalDisabled=readOnly||!can('approver')||!proposal||proposal.status!=='pending'||!!expired||blocked||data?.scenario.status!=='ready';
  const panels:Record<DashboardPanel,ReactNode>={
    '자료·처리 내역':<>{data&&<><dl className="facts"><dt>다음 전달 주간</dt><dd>{data.scenario.next_week_start} ~ {addDays(data.scenario.next_week_start,6)}</dd><dt>현재 상태</dt><dd>{label(data.scenario.status)}</dd></dl><p className="small muted">마감한 한 주의 자료를 전달하면 다음 14일의 예측을 갱신합니다.</p>{data.scenario.blocking_code&&<Message error>{data.scenario.blocking_code}</Message>}</>}
{workspace.error&&<Message error>{describeError(workspace.error)} <button onClick={()=>void workspace.refetch()}>연결 다시 확인</button>{workspace.error instanceof ApiError&&workspace.error.status===401&&<button onClick={onLogout}>토큰 다시 입력</button>}<span className="small"> 마지막 조회 {workspace.dataUpdatedAt?new Date(workspace.dataUpdatedAt).toLocaleTimeString('ko-KR'):'없음'}</span></Message>}
{error&&<Message error>{error} {error.includes('인증')&&<button onClick={onLogout}>토큰 다시 입력</button>}</Message>}
{notice&&<Message>{notice}</Message>}
{journal&&!journal.accepted&&<Message>이전 요청의 접수 결과를 확인해야 합니다. 입력 내용을 유지한 채 다시 확인합니다. <button disabled={busy} onClick={()=>void command(journal.path,{},journal.scope,journal)}>같은 요청으로 접수 확인</button></Message>}
{journal?.accepted&&<Message>{pendingJob?`${label(pendingJob.stage)} · ${label(pendingJob.status)}`:'접수한 작업을 확인하고 있습니다.'}{pendingJob?.error_code&&<span> · {pendingJob.error_code}</span>}{pendingJob?.status==='failed'&&pendingJob.retryable&&can('operator')&&<button disabled={busy} onClick={()=>{const retry:Journal={owner:principal.id,scope,path:'/jobs/'+pendingJob.id+'/retry',body:JSON.stringify({expected_attempt:pendingJob.current_attempt}),key:crypto.randomUUID(),createdAt:new Date().toISOString()};void command(retry.path,{},scope,retry)}}>실패한 작업 재시도</button>}</Message>}
{data&&<section className="process-history"><h3>처리 내역</h3><div className="table-wrap"><table><thead><tr><th>단계</th><th>상태</th><th>최근 변경</th><th>안내</th></tr></thead><tbody>{data.jobs.map(job=><tr key={job.id}><td>{label(job.stage)}</td><td>{label(job.status)}</td><td>{clock(job.updated_at)}</td><td>{job.error_code||'—'}</td></tr>)}</tbody></table></div><div className="small muted">작업 {data.jobs.length}개 · 예측 {data.forecasts.length}회 · 주문 {data.orders.length}건</div></section>}</>,
    '발주 검토':data?(proposal?<section><div className="row spread"><h2 className="section-title">주문할까요?</h2><span className="tag">{label(proposal.status)} · 버전 {proposal.version}</span></div><p className="small muted">예측 원점 {forecast?.forecast_origin} · 승인 기한 {clock(proposal.expires_at)}</p><ProposalTable data={data} proposal={proposal} selected={selectedItem?.id} onSelect={setItem}/>{blocked&&<Message error>승인 차단: {[...new Set([...proposal.blocking_codes,...proposal.lines.flatMap(l=>l.blocking_codes)])].join(', ')}</Message>}{expired&&<Message error>승인 기한이 지났습니다. 새 주문안을 확인해 주십시오.</Message>}<p className="small muted">전체 주문안에 대해 승인하거나 주문하지 않음을 선택합니다.</p><div className="row"><button className="primary" disabled={approvalDisabled} onClick={()=>setConfirm(structuredClone(proposal))}>주문 승인</button><button disabled={approvalDisabled} onClick={()=>void command('/order-proposals/'+proposal.id+'/decisions',{proposal_version:proposal.version,content_hash:proposal.content_hash,decision:'reject'})}>주문하지 않음</button>{can('operator')&&<button disabled={readOnly||data.scenario.status!=='ready'||['approved','rejected'].includes(proposal.status)} onClick={()=>void command('/order-proposals/'+proposal.id+'/recalculate',{expected_version:proposal.version,reason:'화면에서 최신 재고와 정책으로 재계산'})}>최신 상태로 재계산</button>}{['approved','rejected'].includes(proposal.status)&&<button onClick={()=>setPanel('공급처 접수')}>접수 확인</button>}</div></section>:<div className="notice">실제 재고와 발주 조건(포장 단위·납기)이 아직 등록되지 않았습니다. 해당 자료를 등록한 뒤 주문안을 계산할 수 있습니다.</div>):<Message>업무 자료를 확인해 주십시오.</Message>,
    '공급처 접수':data?(<section>{data.orders.length===0?<Message>{proposal?.status==='rejected'?'주문하지 않음으로 기록되었습니다.':'전달된 주문이 없습니다.'} <button onClick={()=>setPanel('발주 검토')}>주문안 확인</button></Message>:<><OrderPicker orders={data.orders} value={selectedOrder?.id||''} onChange={setOrderId}/>{selectedOrder&&<div className="order-detail-grid"><section className="order-detail"><div className="row spread"><h2 className="section-title">공급처 주문</h2><span className="tag">{label(selectedOrder.status)}</span></div><dl className="facts"><dt>공급처 접수번호</dt><dd>{selectedOrder.supplier_order_ref||'접수 대기'}</dd><dt>입고 상태</dt><dd>{label(selectedOrder.receipt_status)}</dd><dt>접수 시각</dt><dd>{clock(selectedOrder.accepted_at)}</dd></dl><OrderLines order={selectedOrder} data={data}/><div className="row">{['submission_unknown','sending'].includes(selectedOrder.status)&&<button className="primary" disabled={readOnly||!can('operator')} onClick={()=>void command('/purchase-orders/'+selectedOrder.id+'/reconcile',{reason:'화면에서 공급처 접수 여부 조회'})}>접수 여부 확인</button>}{selectedOrder.status==='accepted'&&<button className="primary" onClick={()=>{setOrderId(selectedOrder.id);setPanel('입고 기록')}}>입고 기록</button>}</div></section><aside className="order-help"><h2 className="section-title">주문 처리</h2><p>승인 후 공급처가 접수하면 입고를 기록할 수 있습니다.</p><p className="small muted">결과 불명 상태에서는 같은 주문의 접수 여부를 조회합니다.</p></aside></div>}</>}</section>):<Message>업무 자료를 확인해 주십시오.</Message>,
    '입고 기록':data?(<section>{data.orders.length===0?<Message>접수된 주문이 없습니다. <button onClick={()=>setPanel('발주 검토')}>주문안 확인</button></Message>:<><OrderPicker orders={data.orders} value={selectedOrder?.id||''} onChange={setOrderId}/>{selectedOrder&&<ReceiptPanel key={selectedOrder.id} order={selectedOrder} data={data} disabled={readOnly||!can('receiver')} onSubmit={body=>void command('/purchase-orders/'+selectedOrder.id+'/receipts',body)}/>}</>}</section>):<Message>업무 자료를 확인해 주십시오.</Message>,
  };
  return <><Dashboard token={token} owner={principal.id} scope={scope} data={data} forecast={forecast} proposal={proposal} item={selectedItem} onSelect={setItem} onSettings={()=>setSetup(true)} onLogout={onLogout} panel={panel} onPanel={setPanel} disabled={readOnly||!can('test_operator')} message={error||(workspace.error?describeError(workspace.error):'')||notice||(journal?.accepted?'자료를 처리하고 있습니다. 완료되면 예측을 갱신합니다.':'')} isError={!!error||!!workspace.error} canReset={principal.platform_role==='test_admin'} resetDisabled={readOnly||data?.scenario.status!=='ready'||!data?.scenario.last_completed_week_start} onReset={()=>setResetConfirm(true)} onDeliver={()=>void command('/test/scenarios/'+scope+'/weekly-deliveries',{expected_version:data!.scenario.version,week_start:data!.scenario.next_week_start})} panels={panels}/>
  {completion&&<Modal title="처리 완료" onClose={()=>setCompletion('')}><p>{completion}</p><div className="row"><button className="primary" onClick={()=>setCompletion('')}>확인</button></div></Modal>}
  {resetConfirm&&<Modal title="지난주 데이터 초기화" onClose={()=>setResetConfirm(false)}><p>마지막으로 전달한 주간 {data?.scenario.last_completed_week_start} ~ {data?.scenario.last_completed_week_start?addDays(data.scenario.last_completed_week_start,6):''}의 전달 전 상태로 돌아갑니다.</p><p className="small muted">새 테스트 업무를 준비합니다. 원본 사용 기록과 이전 예측·주문·입고 이력은 보존하며 기존 업무는 설정에서 다시 열 수 있습니다.</p><div className="row"><button className="primary" disabled={readOnly||!data?.scenario.last_completed_week_start} onClick={()=>{setResetConfirm(false);setItem('');setOrderId('');setPanel(null);void command('/test/scenarios/'+scope+'/reset-last-week',{expected_version:data!.scenario.version})}}>초기화</button><button onClick={()=>setResetConfirm(false)}>취소</button></div></Modal>}
  {setup&&<Modal title="업무 설정" onClose={()=>setSetup(false)}>{principal.platform_role==='test_admin'&&<DemoSettings token={token} owner={principal.id} disabled={busy||!!journal} onChoose={changeScope}/>}<Setup scenarios={scenarios.data||[]} admin={principal.platform_role==='test_admin'} disabled={busy||!!journal} onChoose={changeScope} onCreate={body=>{setSetup(false);void command('/test/scenarios',body,'')}}/>{scenarios.error&&<Message error>{describeError(scenarios.error)}</Message>}</Modal>}
  {confirm&&<Modal title="전체 주문 승인" onClose={()=>setConfirm(null)}><p>예측 원점 {forecast?.forecast_origin} · 주문안 버전 {confirm.version}</p><p className="small muted">승인 기한 {clock(confirm.expires_at)}</p>{confirm.lines.filter(l=>positive(l.order_units)).length?<ul>{confirm.lines.filter(l=>positive(l.order_units)).map(l=><li key={l.id}>{l.item_name} · {l.packs}포장 / {quantity(l.order_units)}{l.unit}</li>)}</ul>:<p>추가 주문이 없어 승인 이력만 기록합니다.</p>}<div className="row"><button className="primary" disabled={approvalDisabled} onClick={()=>void command('/order-proposals/'+confirm.id+'/decisions',{proposal_version:confirm.version,content_hash:confirm.content_hash,decision:'approve'})}>Yes · 주문 승인</button><button disabled={busy} onClick={()=>setConfirm(null)}>돌아가기</button></div></Modal>}
  </>;
}
function Setup({scenarios,admin,disabled,onChoose,onCreate}:{scenarios:Scenario[];admin:boolean;disabled:boolean;onChoose:(id:string)=>void;onCreate:(body:Schema['ScenarioRequest'])=>void}){
  const [name,setName]=useState('천안의료원 사용 기록'),[date,setDate]=useState('2024-03-04'),[profile,setProfile]=useState<'cheonan_records_v1'>('cheonan_records_v1'),[existing,setExisting]=useState(''),[manual,setManual]=useState('');
  return <div className="setup"><h2 className="section-title">업무 준비</h2>{scenarios.length>0&&<div className="row"><label>기존 업무<select value={existing} onChange={e=>setExisting(e.target.value)} disabled={disabled}><option value="">선택해 주십시오</option>{[...scenarios].reverse().map(s=><option key={s.id} value={s.id}>{s.name} · {day(s.next_week_start)} · {label(s.status)} · {s.id.slice(0,8)}</option>)}</select></label><button disabled={!existing||disabled} onClick={()=>onChoose(existing)}>업무 열기</button></div>}{admin&&<form onSubmit={e=>{e.preventDefault();onCreate({name,first_week_start:date,profile,initial_history_days:112,seed:20261022,hospital_code:'CHEONAN',warehouse_code:'RECORDS'})}}><div className="form-grid"><label>업무 이름<input value={name} onChange={e=>setName(e.target.value)} required maxLength={128} disabled={disabled}/></label><label>첫 월요일<input type="date" value={date} min="2020-04-27" max="2024-11-25" onChange={e=>setDate(e.target.value)} required disabled={disabled}/></label><div><span className="small muted">자료 기준</span><p>보유 품목·일별 사용 기록·환자 집계</p></div></div><button className="primary" disabled={disabled||new Date(date+'T00:00:00Z').getUTCDay()!==1}>새 업무 준비</button></form>}<details><summary>업무 ID로 열기</summary><div className="row"><label>업무 ID<input value={manual} onChange={e=>setManual(e.target.value)} placeholder="접근 권한이 있는 업무 ID" disabled={disabled}/></label><button disabled={disabled||!/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(manual)} onClick={()=>onChoose(manual)}>ID로 열기</button></div></details></div>;
}
function OrderPicker({orders,value,onChange}:{orders:Order[];value:string;onChange:(id:string)=>void}){return <label className="order-picker">주문 선택<select value={value} onChange={e=>onChange(e.target.value)}>{orders.map(o=><option value={o.id} key={o.id}>{o.supplier_order_ref||'주문 '+o.id.slice(0,8)} · {label(o.status)} · {label(o.receipt_status)}</option>)}</select></label>}
function OrderLines({order,data}:{order:Order;data:Workspace}){return <div className="table-wrap"><table><thead><tr><th>품목</th><th className="n">주문량</th><th className="n">입고량</th><th className="n">잔량</th></tr></thead><tbody>{order.lines.map(l=><tr key={l.id}><td>{data.items.find(i=>i.id===l.item_id)?.name||l.item_id}</td><td className="n">{quantity(l.ordered_units)} {l.unit}</td><td className="n">{quantity(l.received_units)} {l.unit}</td><td className="n">{quantity(l.remaining_units)} {l.unit}</td></tr>)}</tbody></table></div>}
