
"""ORM 声明式基类与命名约定。"""
from sqlalchemy import DateTime
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 命名约定：约束有稳定名字，改表时才能精确引用
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


Timestamp = DateTime()