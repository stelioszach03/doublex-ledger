from __future__ import annotations

from loguru import logger

from ledger.db.models import Account, Base, AccountStatus
from ledger.db.session import SessionLocal, engine


DEFAULT_ACCOUNTS = [
    {"code": "1000", "name": "Cash", "ccy": "USD", "status": AccountStatus.active},
    {"code": "1100", "name": "Bank", "ccy": "USD", "status": AccountStatus.active},
    {"code": "2000", "name": "Accounts Payable", "ccy": "USD", "status": AccountStatus.active},
    {"code": "3000", "name": "Equity", "ccy": "USD", "status": AccountStatus.active},
    {"code": "4000", "name": "Revenue", "ccy": "USD", "status": AccountStatus.active},
    {"code": "5000", "name": "Expense", "ccy": "USD", "status": AccountStatus.active},
]


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for acc in DEFAULT_ACCOUNTS:
            exists = db.query(Account).filter(Account.code == acc["code"]).one_or_none()
            if not exists:
                db.add(Account(**acc))
        db.commit()
        logger.info("Seeded default accounts")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
