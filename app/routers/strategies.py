import json
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Strategy, BacktestRun, Trade, Job
from app.schemas import (
    StrategyCreate, StrategyResponse, RunCreate, TradeResponse, JobCreatedResponse
)
from app.tasks import backtest_task

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("", response_model=StrategyResponse)
def create_strategy(request: StrategyCreate, db: Session = Depends(get_db)):
    strategy = Strategy(
        name=request.name,
        dataset_id=request.dataset_id,
        instrument_id=request.instrument_id,
        timeframe_id=request.timeframe_id,
        side=request.side,
        session_start=request.session_start,
        session_end=request.session_end,
        weekdays=request.weekdays,
        entry_timing=request.entry_timing,
        rule_json=request.rule_json,
        tp_type=request.tp_type,
        tp_value=request.tp_value,
        sl_type=request.sl_type,
        sl_value=request.sl_value,
        max_hold_bars=request.max_hold_bars,
        cooldown_bars=request.cooldown_bars,
        fee_pips=request.fee_pips,
        created_at=datetime.utcnow(),
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy


@router.get("", response_model=List[StrategyResponse])
def list_strategies(db: Session = Depends(get_db)):
    return db.query(Strategy).order_by(Strategy.created_at.desc()).all()


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(strategy_id: int, db: Session = Depends(get_db)):
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.post("/{strategy_id}/run", response_model=JobCreatedResponse)
def run_backtest(
    strategy_id: int,
    request: RunCreate,
    db: Session = Depends(get_db),
):
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    job = Job(
        dataset_id=strategy.dataset_id,
        job_type="backtest",
        status="pending",
        params=json.dumps({
            "strategy_id": strategy_id,
            "start_ts": request.start_ts.isoformat() if request.start_ts else None,
            "end_ts": request.end_ts.isoformat() if request.end_ts else None,
        }),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    run = BacktestRun(
        strategy_id=strategy_id,
        job_id=job.id,
        start_ts=request.start_ts,
        end_ts=request.end_ts,
        status="pending",
        params_json=json.dumps({
            "start_ts": request.start_ts.isoformat() if request.start_ts else None,
            "end_ts": request.end_ts.isoformat() if request.end_ts else None,
        }),
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    backtest_task.delay(
        job_id=job.id,
        run_id=run.id,
        strategy_id=strategy_id,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
    )

    return JobCreatedResponse(job_id=job.id, message="Backtest job started")


@router.get("/runs/{run_id}/trades", response_model=List[TradeResponse])
def get_run_trades(run_id: int, db: Session = Depends(get_db)):
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    trades = db.query(Trade).filter(Trade.run_id == run_id).order_by(Trade.entry_ts).all()
    return trades
