import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const token=readFileSync(new URL('../../deploy/.state/test-token',import.meta.url),'utf8').trim();
const proof=JSON.parse(readFileSync(new URL('../../runs/cheonan/reset_last_week_20261028_01/api_checks.json',import.meta.url),'utf8'));
test('cancel or reset latest week and run the same real-data week again',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/?workspace='+proof.test_scope_id);await page.getByLabel('접속 토큰',{exact:true}).fill(token);await page.getByRole('button',{name:'연결',exact:true}).click();
 await expect(page.getByTestId('forecast-count')).toContainText('420/420');const source=new URL(page.url()).searchParams.get('workspace');
 const reset=page.getByRole('button',{name:'지난주 데이터 초기화',exact:true});await expect(reset).toBeEnabled();await reset.click();
 const dialog=page.getByRole('dialog',{name:'지난주 데이터 초기화',exact:true});await expect(dialog).toContainText('원본 사용 기록과 이전 예측·주문·입고 이력은 보존');await dialog.getByRole('button',{name:'취소',exact:true}).click();expect(new URL(page.url()).searchParams.get('workspace')).toBe(source);await expect(page.getByTestId('forecast-count')).toContainText('420/420');
 await reset.click();await dialog.getByRole('button',{name:'초기화',exact:true}).click();await expect(page.locator('.dash-status')).toContainText('지난주 데이터 초기화를 완료했습니다',{timeout:120000});expect(new URL(page.url()).searchParams.get('workspace')).not.toBe(source);
 await expect(page.getByTestId('forecast-count')).toContainText('0/0');await expect(page.getByTestId('patient-chart')).toHaveCount(0);await expect(reset).toBeDisabled();
 await page.getByRole('button',{name:'주간 자료와 처리 내역',exact:true}).click();await expect(page.getByRole('dialog',{name:'자료·처리 내역',exact:true})).toContainText(proof.reset_week_start);await page.getByRole('dialog',{name:'자료·처리 내역',exact:true}).getByRole('button',{name:'닫기',exact:true}).click();
 await page.getByRole('button',{name:'주간 자료 전달',exact:true}).click();await expect(page.locator('.dash-status')).toHaveText('처리를 완료했습니다.',{timeout:240000});await expect(page.getByTestId('forecast-count')).toContainText('420/420');await expect(page.getByTestId('patient-chart')).toBeVisible();await expect(reset).toBeEnabled();
 await page.screenshot({path:'../runs/cheonan/reset_last_week_20261028_01/dashboard-reset-button.png',animations:'disabled'});expect(errors).toEqual([]);
});
