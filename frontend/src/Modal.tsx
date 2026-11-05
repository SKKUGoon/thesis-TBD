import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';

export function DialogShell({title,onClose,children,className=''}:{title:string;onClose:()=>void;children:ReactNode;className?:string}){
 const ref=useRef<HTMLDialogElement>(null),outside=useRef(false);
 useEffect(()=>{const dialog=ref.current!;dialog.showModal();return()=>dialog.close()},[]);
 const isOutside=(x:number,y:number)=>{const rect=ref.current!.getBoundingClientRect();return x<rect.left||x>rect.right||y<rect.top||y>rect.bottom};
 return <dialog ref={ref} className={className} aria-label={title} onCancel={event=>{event.preventDefault();onClose()}} onPointerDown={event=>{outside.current=event.target===event.currentTarget&&isOutside(event.clientX,event.clientY)}} onClick={event=>{if(outside.current&&event.target===event.currentTarget&&isOutside(event.clientX,event.clientY))onClose();outside.current=false}}>{children}</dialog>;
}
export function Modal({title,onClose,children,className=''}:{title:string;onClose:()=>void;children:ReactNode;className?:string}){
 return <DialogShell title={title} onClose={onClose} className={className}><div className="row spread"><h2 className="section-title">{title}</h2><button className="quiet" aria-label="닫기" onClick={onClose}>닫기</button></div>{children}</DialogShell>;
}
