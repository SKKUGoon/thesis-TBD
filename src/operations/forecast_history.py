"""Join immutable prior forecasts with only already available actual observations."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.operations.contracts import Conflict
from src.operations.db import one, rows


def forecast_history(conn: object, scope: str, item_id: str, forecast_id: str) -> dict:
    """Return current/prior curves and cutoff-safe, observation-matched errors."""
    item=one(conn,'SELECT * FROM app.item WHERE id=%s',(item_id,))
    current=one(conn,"SELECT f.*,s.forecast_origin,s.knowledge_as_of FROM app.forecast_run f JOIN app.input_snapshot s ON s.id=f.snapshot_id WHERE f.id=%s AND f.status='succeeded' AND f.validity='current'",(forecast_id,))
    now=one(conn,'SELECT simulation_at FROM app.scope WHERE id=%s',(scope,))['simulation_at']
    if current['knowledge_as_of']>now:raise Conflict('FORECAST_NOT_AVAILABLE')
    previous=conn.execute("SELECT f.*,s.forecast_origin FROM app.forecast_run f JOIN app.input_snapshot s ON s.id=f.snapshot_id WHERE f.status='succeeded' AND f.validity='current' AND s.forecast_origin<%s AND s.knowledge_as_of<=%s ORDER BY s.forecast_origin DESC,f.generated_at DESC LIMIT 1",(current['forecast_origin'],now)).fetchone()
    def curve(run: dict | None) -> dict | None:
        if run is None:return None
        values=rows(conn,'SELECT target_day,prediction FROM app.forecast_value WHERE forecast_id=%s AND item_id=%s ORDER BY target_day',(run['id'],item_id))
        return {'id':run['id'],'origin':run['forecast_origin'],'values':[{'day':v['target_day'],'quantity':v['prediction']} for v in values]}
    prior=curve(previous);latest=curve(current)
    start=current['forecast_origin']-timedelta(days=28)
    truth=rows(conn,"""SELECT DISTINCT ON(u.day) u.day,u.quantity,u.observation FROM app.usage_day u JOIN app.delivery d ON d.id=u.delivery_id
        WHERE d.status='validated' AND u.item_id=%s AND u.day>=%s AND u.day<%s AND u.available_at<=%s AND d.source_available_at<=%s
        ORDER BY u.day,d.source_revision DESC,d.received_at DESC""",(item_id,start,current['forecast_origin'],now,now))
    observed={r['day']:r['quantity'] for r in truth if r['observation']=='observed' and r['quantity'] is not None}
    eligible=[v for v in prior['values'] if v['day']<current['forecast_origin']] if prior else []
    matched=[(v['quantity'],observed[v['day']]) for v in eligible if v['day'] in observed]
    absolute=sum((abs(pred-actual) for pred,actual in matched),Decimal(0));denominator=sum((abs(actual) for _,actual in matched),Decimal(0))
    def rounded(value: Decimal | None) -> str | None:return str(value.quantize(Decimal('0.000001'))) if value is not None else None
    metrics={'observations':len(matched),'expected_observations':len(eligible),'mae':rounded(absolute/len(matched)) if matched else None,'wape_pct':rounded(absolute/denominator*100) if matched and denominator else None,
             'start':min((v['day'] for v in eligible),default=None),'end':max((v['day'] for v in eligible),default=None)}
    return {'item_id':item_id,'item_name':item['name'],'unit':item['base_unit'],'as_of':now,'current':latest,'previous':prior,'actual':truth,'metrics':metrics}


def patient_history(conn: object, scope: str, forecast_id: str) -> dict:
    """Read only three aggregate series sealed into this available forecast snapshot."""
    run = one(conn, "SELECT f.snapshot_id,s.forecast_origin,s.knowledge_as_of FROM app.forecast_run f JOIN app.input_snapshot s ON s.id=f.snapshot_id WHERE f.id=%s AND f.status='succeeded' AND f.validity='current'", (forecast_id,))
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    if run["knowledge_as_of"] > now:
        raise Conflict("FORECAST_NOT_AVAILABLE")
    source = rows(conn, "SELECT day,feature_key,value,observation FROM app.snapshot_feature WHERE snapshot_id=%s AND kind='patient' AND feature_key IN ('total__ALL','encounter__IN','encounter__OUT') ORDER BY day,feature_key", (run["snapshot_id"],))
    names = {"total__ALL": "total", "encounter__IN": "inpatient", "encounter__OUT": "outpatient"}
    daily: dict = {}
    for row in source:
        record = daily.setdefault(row["day"], {"day": row["day"], "total": None, "inpatient": None, "outpatient": None})
        record[names[row["feature_key"]]] = int(row["value"]) if row["observation"] == "observed" and row["value"] is not None else None
    return {"as_of": run["knowledge_as_of"], "forecast_origin": run["forecast_origin"], "values": list(daily.values())}
