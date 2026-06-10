from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from api.db import Base, Budget, Expense, make_engine, make_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expenses")


class ExpenseIn(BaseModel):
    user_id: int
    amount: float = Field(gt=0)
    category: str = Field(min_length=1, max_length=64)


class BudgetIn(BaseModel):
    user_id: int
    category: str = Field(min_length=1, max_length=64)
    limit: float = Field(gt=0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    Base.metadata.create_all(engine)
    app.state.session_factory = make_session_factory(engine)
    logger.info("База готова")
    yield


app = FastAPI(title="Expense Analyzer", lifespan=lifespan)


def get_session_factory():
    return app.state.session_factory


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def period_start(period: str) -> datetime | None:
    now = utcnow()
    if period == "today":
        return datetime(now.year, now.month, now.day)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return datetime(now.year, now.month, 1)
    return None


def month_spent(session, user_id: int, category: str) -> float:
    now = utcnow()
    start = datetime(now.year, now.month, 1)
    total = (
        session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.category == category,
            Expense.created_at >= start,
        )
        .scalar()
    )
    return float(total or 0.0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/expenses")
def add_expense(payload: ExpenseIn, session_factory=Depends(get_session_factory)):
    category = payload.category.strip().lower()
    with session_factory() as session:
        expense = Expense(user_id=payload.user_id, amount=payload.amount, category=category)
        session.add(expense)
        session.commit()
        session.refresh(expense)

        budget = (
            session.query(Budget)
            .filter(Budget.user_id == payload.user_id, Budget.category == category)
            .one_or_none()
        )
        budget_info = None
        if budget is not None:
            spent = month_spent(session, payload.user_id, category)
            budget_info = {
                "limit": budget.limit_amount,
                "spent": spent,
                "exceeded": spent > budget.limit_amount,
            }

        return {
            "id": expense.id,
            "user_id": expense.user_id,
            "amount": expense.amount,
            "category": expense.category,
            "budget": budget_info,
        }


@app.get("/stats")
def stats(user_id: int, period: str = "all", session_factory=Depends(get_session_factory)):
    with session_factory() as session:
        query = session.query(Expense.category, func.sum(Expense.amount)).filter(
            Expense.user_id == user_id
        )
        start = period_start(period)
        if start is not None:
            query = query.filter(Expense.created_at >= start)
        rows = query.group_by(Expense.category).all()
        by_category = {category: float(total) for category, total in rows}
        return {
            "period": period,
            "total": float(sum(by_category.values())),
            "by_category": by_category,
        }


@app.get("/expenses")
def list_expenses(user_id: int, limit: int = 5, session_factory=Depends(get_session_factory)):
    limit = max(1, min(limit, 50))
    with session_factory() as session:
        rows = (
            session.query(Expense)
            .filter(Expense.user_id == user_id)
            .order_by(Expense.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {"id": e.id, "amount": e.amount, "category": e.category}
            for e in rows
        ]


@app.delete("/expenses/last")
def delete_last(user_id: int, session_factory=Depends(get_session_factory)):
    with session_factory() as session:
        expense = (
            session.query(Expense)
            .filter(Expense.user_id == user_id)
            .order_by(Expense.id.desc())
            .first()
        )
        if expense is None:
            raise HTTPException(status_code=404, detail="Нет расходов для удаления")
        deleted = {"id": expense.id, "amount": expense.amount, "category": expense.category}
        session.delete(expense)
        session.commit()
        return deleted


@app.post("/budgets")
def set_budget(payload: BudgetIn, session_factory=Depends(get_session_factory)):
    category = payload.category.strip().lower()
    with session_factory() as session:
        budget = (
            session.query(Budget)
            .filter(Budget.user_id == payload.user_id, Budget.category == category)
            .one_or_none()
        )
        if budget is None:
            budget = Budget(user_id=payload.user_id, category=category, limit_amount=payload.limit)
            session.add(budget)
        else:
            budget.limit_amount = payload.limit
        session.commit()
        return {"category": category, "limit": payload.limit}


@app.get("/budgets")
def list_budgets(user_id: int, session_factory=Depends(get_session_factory)):
    with session_factory() as session:
        budgets = session.query(Budget).filter(Budget.user_id == user_id).all()
        result = []
        for budget in budgets:
            spent = month_spent(session, user_id, budget.category)
            result.append(
                {
                    "category": budget.category,
                    "limit": budget.limit_amount,
                    "spent": spent,
                    "remaining": budget.limit_amount - spent,
                }
            )
        return result
