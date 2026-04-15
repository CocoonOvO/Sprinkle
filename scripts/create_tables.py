#!/usr/bin/env python3
"""创建数据库表"""

import sys
sys.path.insert(0, "src")

from sqlalchemy import create_engine, text
from sprinkle.models import Base

DATABASE_URL = "postgresql://cream@localhost:5432/synthink_db"

def create_tables():
    engine = create_engine(DATABASE_URL)
    
    # 删除旧表（如果存在）
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS conversation_members CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS messages CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS conversations CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        conn.commit()
    
    # 创建新表
    Base.metadata.create_all(engine)
    print("数据库表创建成功！")

if __name__ == "__main__":
    create_tables()
