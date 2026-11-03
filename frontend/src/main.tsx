import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import App from './App';
import './base.css';
import './dashboard.css';
const client=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:true,refetchIntervalInBackground:false},mutations:{retry:false}}});
createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></React.StrictMode>);
