import {useEffect,useState} from 'react';
import type {FormEvent} from 'react';
import {clock,label,positive,quantity,validReceipt} from './domain';
import type {Order,Proposal,Schema,Workspace} from './types';

export function ProposalTable({data,proposal,selected,onSelect}:{data:Workspace;proposal?:Proposal;selected?:string;onSelect:(id:string)=>void}){
  const items=data.items;
  return <div className="table-wrap"><table><caption>품목별 재고와 주문 권고</caption><thead><tr><th>품목</th><th className="n">가용 재고</th><th className="n">확정 입고 예정</th><th className="n">접수 대기 예약</th><th className="n">보호기간 수요</th><th className="n">안전재고</th><th className="n">권고 포장</th></tr></thead><tbody>{items.map(item=>{const line=proposal?.lines.find(l=>l.item_id===item.id),stock=data.inventory.find(s=>s.item_id===item.id);return <tr key={item.id} className={selected===item.id?'selected':''}><td><button className="item-button" aria-pressed={selected===item.id} onClick={()=>onSelect(item.id)}>{item.name}</button><span className="small muted unit">{item.kind==='drug'?'약품':'물품'} · {item.base_unit}</span></td><td className="n">{data.scenario.inventory_source==='not_registered'?'미등록':quantity(stock?.available_units)}</td><td className="n">{data.scenario.inventory_source==='not_registered'?'미등록':quantity(stock?.confirmed_incoming_units)}</td><td className="n">{data.scenario.inventory_source==='not_registered'?'미등록':quantity(stock?.pending_order_units)}</td><td className="n">{quantity(line?.forecast_units)}</td><td className="n">{quantity(line?.safety_units)}</td><td className="n">{line?line.packs?`${line.packs}포장`:'주문 없음':'—'}</td></tr>})}</tbody></table></div>;
}
export function ReceiptPanel({order,data,disabled,onSubmit}:{order:Order;data:Workspace;disabled:boolean;onSubmit:(body:Schema['ReceiptRequest'])=>void}){
  const [quantities,setQuantities]=useState<Record<string,string>>({}),[error,setError]=useState('');
  const fingerprint=order.lines.map(l=>l.id+':'+l.received_units).join('|');
  useEffect(()=>{setQuantities({});setError('')},[fingerprint]);
  const complete=order.receipt_status==='received';
  const editable=!disabled&&order.status==='accepted'&&!order.released_at&&!complete;
  const name=(itemId:string)=>data.items.find(i=>i.id===itemId)?.name||itemId;
  function submit(event:FormEvent){event.preventDefault();setError('');
    const lines=order.lines.map(l=>({...l,input:quantities[l.id]||'0'}));
    if(lines.some(l=>!validReceipt(l.input,l.remaining_units))){setError('잔량 이내의 수량을 소수점 여섯 자리까지 입력해 주십시오.');return;}
    const positiveLines=lines.filter(l=>positive(l.input));if(!positiveLines.length){setError('한 품목 이상에 양의 입고 수량을 입력해 주십시오.');return;}
    const occurred=data.scenario.simulation_at;
    if(!order.accepted_at||new Date(occurred)<new Date(order.accepted_at)){setError('공급처 접수 시각 이후에 입고를 기록할 수 있습니다.');return;}
    onSubmit({source_receipt_key:crypto.randomUUID(),occurred_at:occurred,lines:positiveLines.map(l=>({order_line_id:l.id,quantity:l.input,unit:l.unit}))});
  }
  return <form onSubmit={submit}><div className="row spread"><h2 className="section-title">입고 기록</h2><span className="tag" data-testid="receipt-status">{label(order.receipt_status)}</span></div><p className="small muted">입고 시각 {clock(data.scenario.simulation_at)}</p>{order.status!=='accepted'&&<div className="notice">공급처 접수가 확인되어야 입고를 기록할 수 있습니다.</div>}<div className="table-wrap"><table><thead><tr><th>품목</th><th className="n">주문량</th><th className="n">누적 입고량</th><th className="n">잔량</th><th className="n">이번 입고</th><th className="n">가용 재고</th></tr></thead><tbody>{order.lines.map(l=><tr key={l.id}><td>{name(l.item_id)}<span className="small muted unit">{l.unit}</span></td><td className="n">{quantity(l.ordered_units)}</td><td className="n" data-testid={'received-'+l.item_id}>{quantity(l.received_units)}</td><td className="n">{quantity(l.remaining_units)}</td><td className="n"><input aria-label={name(l.item_id)+' 이번 입고 수량'} data-testid={'qty-'+l.item_id} className="qty" inputMode="decimal" value={quantities[l.id]||'0'} disabled={!editable||!positive(l.remaining_units)} onChange={e=>setQuantities({...quantities,[l.id]:e.target.value})}/></td><td className="n" data-testid={'stock-'+l.item_id}>{quantity(data.inventory.find(i=>i.item_id===l.item_id)?.available_units)}</td></tr>)}</tbody></table></div>{error&&<div className="notice error" role="alert">{error}</div>}<div className="row actions"><button type="button" disabled={!editable} onClick={()=>setQuantities(Object.fromEntries(order.lines.map(l=>[l.id,l.remaining_units])))}>잔량 전부 채우기</button><button className="primary" disabled={!editable}>입고 기록</button></div><p className="small muted">부분 입고 후 남은 수량을 이어서 기록할 수 있습니다. 처리가 완료되면 재고와 잔량을 갱신합니다.</p></form>;
}
