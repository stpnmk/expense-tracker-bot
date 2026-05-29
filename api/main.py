from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func

from api.db import Base, Expense, make_engine, make_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expenses")


class ExpenseIn(BaseModel):
    user_id: int
    amount: float = Field(gt=0)
    category: str = Field(min_length=1, max_length=64)


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/expenses")
def add_expense(payload: ExpenseIn, session_factory=Depends(get_session_factory)):
    with session_factory() as session:
        expense = Expense(
            user_id=payload.user_id,
            amount=payload.amount,
            category=payload.category.strip().lower(),
        )
        session.add(expense)
        session.commit()
        session.refresh(expense)
        return {
            "id": expense.id,
            "user_id": expense.user_id,
            "amount": expense.amount,
            "category": expense.category,
        }


@app.get("/stats")
def stats(user_id: int, session_factory=Depends(get_session_factory)):
    with session_factory() as session:
        rows = (
            session.query(Expense.category, func.sum(Expense.amount))
            .filter(Expense.user_id == user_id)
            .group_by(Expense.category)
            .all()
        )
        by_category = {category: float(total) for category, total in rows}
        return {"total": float(sum(by_category.values())), "by_category": by_category}
