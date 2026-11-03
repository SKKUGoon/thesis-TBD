import {describe,it,expect} from 'vitest';
import {addDays,currentResults,quantity,total,validReceipt} from '../src/domain';
import type {Workspace} from '../src/types';
describe('business boundaries',()=>{
  it('preserves exact decimal receipt quantities and rejects excess or malformed input',()=>{
    expect(validReceipt('0.000001','0.000002')).toBe(true);expect(validReceipt('0.000003','0.000002')).toBe(false);
    for(const value of ['-1','1e2','NaN','Infinity','1.0000001','','01'])expect(validReceipt(value,'100')).toBe(false);
    expect(total(['99999999999999.000001','0.000002'])).toBe('99999999999999.000003');
    expect(quantity('10.500000')).toBe('10.5');expect(addDays('2024-02-26',6)).toBe('2024-03-03');
  });
  it('binds forecast and proposal to the validated revision, and hides prior success while advancing',()=>{
    const data={scenario:{status:'ready',last_completed_week_start:'2024-03-04'},deliveries:[{kind:'weekly',status:'validated',week_start:'2024-03-04',source_revision:1,snapshot_id:'old'},{kind:'weekly',status:'validated',week_start:'2024-03-04',source_revision:2,snapshot_id:'current'}],forecasts:[{id:'wrong',snapshot_id:'old',validity:'current',status:'succeeded',generated_at:'2024-03-15'},{id:'right',snapshot_id:'current',validity:'current',status:'succeeded',generated_at:'2024-03-14'}],proposals:[{id:'p',forecast_id:'right',version:1},{id:'new',forecast_id:'right',version:2}]} as unknown as Workspace;
    expect(currentResults(data).forecast?.id).toBe('right');expect(currentResults(data).proposal?.id).toBe('new');
    data.scenario.status='advancing';expect(currentResults(data).forecast).toBeUndefined();
  });
});
