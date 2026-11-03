import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {request,describeError} from './api';
import {addDays,day,total} from './domain';
import type {Schema} from './types';
type History=Schema['ForecastHistory'];
export default function HistoryChart({token,scope,owner,itemId,forecastId,kind,compact=false}:{token:string;scope:string;owner:string;itemId:string;forecastId:string;kind:'drug'|'material';compact?:boolean}){
 const query=useQuery({queryKey:[owner,scope,'forecast-history',itemId,forecastId],queryFn:({signal})=>request<History>(token,scope,`/items/${itemId}/forecast-history?forecast_id=${forecastId}`,{signal})});
 if(query.isPending)return <p role="status">실제 사용량과 이전 예측을 불러오고 있습니다.</p>;
 if(query.error)return <div role="alert" className="notice error">{describeError(query.error)} <button onClick={()=>void query.refetch()}>다시 조회</button></div>;
 return <Chart data={query.data} kind={kind} compact={compact}/>;
}
function Chart({data,kind,compact=false}:{data:History;kind:'drug'|'material';compact?:boolean}){
 const actualLabel=kind==='material'?'기록 출고량':'실제 사용량';
 const frame=useRef<HTMLDivElement>(null),summary=useRef<HTMLDivElement>(null),futureSummary=useRef<HTMLDivElement>(null);
 const [size,setSize]=useState({width:600,height:400}),[summaryHeight,setSummaryHeight]=useState(110),[hover,setHover]=useState<string|null>(null),[tableOpen,setTableOpen]=useState(false);
 useEffect(()=>{const observer=new ResizeObserver(([entry])=>setSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(frame.current!);return()=>observer.disconnect()},[]);
 const current=data.current.values,previous=data.previous?.values||[];
 const allActual=data.actual.map(v=>({day:v.day,quantity:v.observation==='observed'?v.quantity:null}));
 // Shorten only the visible historical window; preserve all observations in the table.
 const displayStart=addDays(data.current.origin,-14);
 const actual=allActual.filter(v=>v.day>=displayStart),prior=previous.filter(v=>v.day>=displayStart);
 const dates=[...new Set([...current,...prior,...actual].map(v=>v.day))].sort();
 const tableDates=[...new Set([...current,...previous,...allActual].map(v=>v.day))].sort();
 const hasValidation=!!data.previous&&data.metrics.start!==null&&data.metrics.end!==null;
 useEffect(()=>{
  const elements=[summary.current,futureSummary.current].filter((element):element is HTMLDivElement=>element!==null);
  const observer=new ResizeObserver(()=>setSummaryHeight(Math.ceil(Math.max(0,...elements.map(element=>element.getBoundingClientRect().height)))));
  elements.forEach(element=>observer.observe(element));return()=>observer.disconnect();
 },[hasValidation,data.item_id]);
 const width=Math.max(1,size.width),height=Math.max(1,size.height),left=44,right=12,top=Math.max(30,summaryHeight+42),bottom=42;
 const values=[...current,...prior,...actual].filter(v=>v.quantity!==null).map(v=>Number(v.quantity));
 const max=Math.max(1,...values)*1.12,chartWidth=Math.max(1,width-left-right),chartHeight=Math.max(1,height-top-bottom);
 const x=(date:string)=>left+dates.indexOf(date)*chartWidth/Math.max(1,dates.length-1),y=(value:string|number)=>top+chartHeight-Number(value)*chartHeight/max;
 const path=(points:{day:string;quantity:string|null}[])=>{let pen=false;return points.map(v=>{if(v.quantity===null){pen=false;return '';}const p=(pen?'L':'M')+x(v.day)+','+y(v.quantity);pen=true;return p}).join(' ')};
 const step=chartWidth/Math.max(1,dates.length-1),boundary=x(data.current.origin),bandStart=hasValidation?Math.max(left,x(data.metrics.start!)-step/2):boundary,bandWidth=Math.max(0,boundary-bandStart);
 const ticks=width<420?[dates[0],data.current.origin,dates.at(-1)!]:[dates[0],data.metrics.start&&dates.includes(data.metrics.start)?data.metrics.start:dates[Math.floor(dates.length/3)],data.current.origin,dates.at(-1)!];
 const digits=(v:string|null)=>v===null?'미산출':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:2});
 const metric=data.previous?`이전 예측 검증 (${day(data.metrics.start)}~${day(data.metrics.end)})`:'이전 예측 없음';
 const errorText=`하루 평균 오차 ${digits(data.metrics.mae)}${data.metrics.mae===null?'':data.unit}`;
 const relativeError=`실제 ${kind==='material'?'출고량':'사용량'} 대비 오차 ${digits(data.metrics.wape_pct)}${data.metrics.wape_pct===null?'':'%'}`;
 const selected=hover?{day:hover,actual:actual.find(v=>v.day===hover)?.quantity,previous:prior.find(v=>v.day===hover)?.quantity,current:current.find(v=>v.day===hover)?.quantity}:null;
 const forecastTotal=current.length===14&&current.every(value=>value.quantity!==null)?total(current.map(value=>value.quantity!)):null;
 const hoverValues=selected?[{label:actualLabel,quantity:selected.actual,color:'#ef555e'},{label:'이전 예측',quantity:selected.previous,color:'#697b99'},{label:'이번 예측',quantity:selected.current,color:'#3266e3'}].filter(value=>value.quantity!==undefined&&value.quantity!==null):[];
 const tooltipWidth=Math.min(210,chartWidth),tooltipX=hover&&x(hover)+tooltipWidth+12<=width-right?x(hover)+12:Math.max(left,hover?x(hover)-tooltipWidth-12:left);
 return <div className={`history-chart ${compact?'compact-history':''}`} data-testid="history-chart">
  <button className="history-table-button" aria-label="테이블 뷰 보기" title="테이블 뷰 보기" onClick={()=>setTableOpen(true)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M10 4v16"/></svg></button>
  {tableOpen&&<TableView onClose={()=>setTableOpen(false)} dates={tableDates} actual={allActual} previous={previous} current={current} actualLabel={actualLabel} digits={digits}/>}
  <div className="chart-legend" style={{justifyContent:'center',paddingLeft:left,paddingRight:right}}><span className="actual-key">{actualLabel}</span><span className="prior-key">이전 예측 {day(data.previous?.origin)}</span><span className="current-key">이번 예측 {day(data.current.origin)}</span></div>
  <div ref={frame} className="history-plot-frame" style={{minHeight:Math.max(compact?340:400,summaryHeight+230)}}>
  <svg className="comparison-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.item_name} 실제 사용량·이전 예측·이번 예측 비교. ${metric}. ${errorText}. ${relativeError}`} onPointerMove={e=>{const bounds=e.currentTarget.getBoundingClientRect();if(e.clientY-bounds.top<top||e.clientY-bounds.top>height-bottom||e.clientX-bounds.left<left||e.clientX-bounds.left>width-right){setHover(null);return;}const i=Math.round((e.clientX-bounds.left-left)/chartWidth*(dates.length-1));setHover(dates[Math.min(dates.length-1,Math.max(0,i))])}} onPointerLeave={()=>setHover(null)}>
   <desc>실제값은 최근 14일을 표시합니다. 회색 음영은 이전 예측의 실제값 확인 구간이고 청색 음영은 이번 예측 기간입니다.</desc>
   {hasValidation&&<rect className="history-observed-band" x={bandStart} y="20" width={bandWidth} height={Math.max(0,height-bottom-20)}/>}
   <rect className="history-future-band" x={boundary} y="20" width={Math.max(0,width-right-boundary)} height={Math.max(0,height-bottom-20)}/>
   {hasValidation&&<foreignObject className="verification-summary-container" x={bandStart+5} y="25" width={Math.max(1,bandWidth-10)} height={Math.max(1,summaryHeight+4)}><div ref={summary} className="verification-summary">
    <div className="verification-heading" data-testid="history-metric"><strong>이전 예측 검증 <span>({day(data.metrics.start)}~{day(data.metrics.end)})</span></strong></div>
    <div data-testid="history-error"><span>하루 평균 오차 </span><strong>{digits(data.metrics.mae)}{data.metrics.mae===null?'':data.unit}</strong></div>
    <div data-testid="history-relative-error"><span>실제 {kind==='material'?'출고량':'사용량'} 대비 오차 </span><strong>{digits(data.metrics.wape_pct)}{data.metrics.wape_pct===null?'':'%'}</strong></div>
   </div></foreignObject>}
   <foreignObject className="future-summary-container" x={boundary+8} y="25" width={Math.max(1,width-right-boundary-16)} height={Math.max(1,summaryHeight+4)}><div ref={futureSummary} className="future-summary"><strong>이번 예측 시작</strong><div data-testid="forecast-total">14일 합계 <strong>{forecastTotal===null?'확인 중':digits(forecastTotal)+data.unit}</strong></div></div></foreignObject>
   <text x="0" y={top-10}>수량 {data.unit.length<12?'('+data.unit+')':''}</text>
   {[0,max/2,max].map(v=><g key={v}><line className="grid" x1={left} x2={width-right} y1={y(v)} y2={y(v)}/><text x={left-8} y={y(v)+4} textAnchor="end">{v===0?'0':v<10?v.toFixed(1):Math.round(v).toLocaleString()}</text></g>)}
   <line className="cutoff" x1={boundary} x2={boundary} y1="20" y2={height-bottom}/>
   <path className="actual-trace" d={path(actual)}/><path className="current-trace" d={path(current)}/><path className="prior-trace" d={path(prior)} strokeDasharray="7 5"/>
   {actual.filter(v=>v.quantity!==null).map(v=><circle className="actual-dot" key={v.day} cx={x(v.day)} cy={y(v.quantity!)} r="2.5"/>)}
   {ticks.filter((v,i,a)=>a.indexOf(v)===i).map((d,i,a)=><text key={d} x={x(d)} y={height-14} textAnchor={i===0?'start':i===a.length-1?'end':'middle'}>{day(d)}</text>)}
   {hover&&<line className="hover-guide" x1={x(hover)} x2={x(hover)} y1={top} y2={height-bottom}/>}
   {hover&&hoverValues.map(value=><circle className="hover-point" key={value.label} cx={x(hover)} cy={y(value.quantity!)} r="4" fill={value.color} stroke="white" strokeWidth="1.5"/>)}
   {selected&&hoverValues.length>0&&<foreignObject className="graph-tooltip-container" x={tooltipX} y={top+8} width={tooltipWidth} height={34+hoverValues.length*23}><div className="graph-tooltip" role="tooltip"><strong>{selected.day}</strong>{hoverValues.map(value=><div key={value.label}><span style={{color:value.color}}>{value.label}</span><strong>{digits(value.quantity!)}{data.unit}</strong></div>)}</div></foreignObject>}
  </svg></div>
 </div>;
}
function TableView({onClose,dates,actual,previous,current,actualLabel,digits}:{onClose:()=>void;dates:string[];actual:{day:string;quantity:string|null}[];previous:{day:string;quantity:string|null}[];current:{day:string;quantity:string|null}[];actualLabel:string;digits:(v:string|null)=>string}){
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const element=dialog.current!;element.showModal();return()=>element.close()},[]);
 return <dialog ref={dialog} className="history-table-dialog" aria-label="테이블 뷰" onCancel={event=>{event.preventDefault();onClose()}}><div className="row spread"><h3>테이블 뷰</h3><button aria-label="닫기" className="quiet" onClick={onClose}>닫기</button></div><div className="table-wrap"><table><thead><tr><th>날짜</th><th className="n">{actualLabel}</th><th className="n">이전 예측</th><th className="n">이번 예측</th></tr></thead><tbody>{dates.map(d=><tr key={d}><td>{d}</td><td className="n">{digits(actual.find(v=>v.day===d)?.quantity??null)}</td><td className="n">{digits(previous.find(v=>v.day===d)?.quantity??null)}</td><td className="n">{digits(current.find(v=>v.day===d)?.quantity??null)}</td></tr>)}</tbody></table></div></dialog>;
}
