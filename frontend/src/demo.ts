import {useQuery} from '@tanstack/react-query';
import {request} from './api';
export function useDemoMode(token:string,owner:string){
 return useQuery({queryKey:[owner,'demo-capabilities'],queryFn:({signal})=>request<{demo_mode:boolean}>(token,'','/demo/capabilities',{signal}),retry:false}).data?.demo_mode===true;
}
