import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password, validate_password
from app.models import Role, User
from app.seed.defaults import seed_defaults


def main():
    with SessionLocal() as db:
        seed_defaults(db)
        username = os.getenv("ADMIN_USERNAME") or input("管理员账号: ").strip()
        full_name = os.getenv("ADMIN_FULL_NAME") or input("管理员姓名: ").strip()
        password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("初始密码: ")
        validate_password(password)
        if db.scalar(select(User.id).where(User.username == username)):
            print("账号已存在，未做修改")
            return
        role = db.scalar(select(Role).where(Role.name == "系统管理员"))
        db.add(
            User(
                username=username,
                full_name=full_name,
                department="系统管理",
                password_hash=hash_password(password),
                role_id=role.id,
                must_change_password=True,
            )
        )
        db.commit()
        print(f"管理员 {username} 创建成功；首次登录必须修改密码。")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, ValueError) as exc:
        print(f"创建失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
